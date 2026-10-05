"""Email verification: syntax -> MX -> (optional) SMTP RCPT probe with catch-all detection,
or a self-hosted Reacher instance (https://github.com/reacherhq/check-if-email-exists).

SMTP needs outbound port 25, which most home ISPs and many clouds block — run it from a
VPS or point ``reacher_url`` at Reacher on a VPS. Without either, MX is the default level.
"""

from __future__ import annotations

import asyncio
import random
import string
from dataclasses import dataclass, field
from typing import Any

import dns.asyncresolver
import dns.exception
import dns.resolver

from leadengine.enrich.emails.filters import domain_of, reject_reason
from leadengine.http import HttpClient
from leadengine.log import get_logger

log = get_logger("emails.verify")

DISPOSABLE = frozenset({
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "temp-mail.org", "yopmail.com",
    "trashmail.com", "fakeinbox.com", "maildrop.cc", "getnada.com", "sharklasers.com", "dispostable.com",
    "throwawaymail.com", "mintemail.com", "mohmal.com", "emailondeck.com",
})


@dataclass
class Verification:
    status: str                      # valid | invalid | catch_all | risky | unknown
    reason: str
    level: str                       # syntax | mx | smtp | reacher
    mx_hosts: list[str] = field(default_factory=list)
    catch_all: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "level": self.level,
                "mx": self.mx_hosts[:3], "catch_all": self.catch_all}


class EmailVerifier:
    """Verifies emails; per-domain results (MX, catch-all) are cached in memory and, when a
    repository is given, in the ``domain_checks`` table."""

    def __init__(self, cfg: dict[str, Any], http: HttpClient | None = None, repo=None,
                 reacher_secret: str = "", domain_ttl_days: float = 30) -> None:
        self.mode = str(cfg.get("verify", "mx")).lower()          # none | mx | smtp | reacher
        self.smtp_helo = cfg.get("smtp_helo") or "localhost"
        self.smtp_from = cfg.get("smtp_from") or f"verify@{self.smtp_helo}"
        self.smtp_timeout = float(cfg.get("smtp_timeout_seconds", 10))
        self.smtp_port = int(cfg.get("smtp_port", 25))
        self.reacher_url = str(cfg.get("reacher_url", "")).rstrip("/")
        self.reacher_secret = reacher_secret
        self.http = http
        self.repo = repo
        self.domain_ttl = domain_ttl_days
        self._mx_mem: dict[str, list[str] | None] = {}
        self._catch_all_mem: dict[str, bool | None] = {}
        self._domain_locks: dict[str, asyncio.Lock] = {}

    # ── MX ───────────────────────────────────────────────────────────
    async def mx_hosts(self, domain: str) -> list[str] | None:
        """Mail hosts by priority; ``[]`` = domain takes no mail; ``None`` = lookup failed."""
        if domain in self._mx_mem:
            return self._mx_mem[domain]
        lock = self._domain_locks.setdefault(domain, asyncio.Lock())
        async with lock:
            if domain in self._mx_mem:
                return self._mx_mem[domain]
            if self.repo is not None and (row := self.repo.get_domain_check(domain, "mx", self.domain_ttl)):
                self._mx_mem[domain] = row.payload.get("hosts")
                return self._mx_mem[domain]
            hosts = await self._lookup_mx(domain)
            self._mx_mem[domain] = hosts
            if self.repo is not None and hosts is not None:
                self.repo.set_domain_check(domain, "mx", {"hosts": hosts})
            return hosts

    async def _lookup_mx(self, domain: str) -> list[str] | None:
        try:
            answer = await dns.asyncresolver.resolve(domain, "MX", lifetime=6)
            hosts = [str(r.exchange).rstrip(".") for r in sorted(answer, key=lambda r: r.preference)]
            return [h for h in hosts if h]  # RFC 7505 null MX "." -> []
        except dns.resolver.NXDOMAIN:
            return []
        except dns.resolver.NoAnswer:
            try:  # no MX but an A record: mail goes to the domain itself (RFC 5321)
                await dns.asyncresolver.resolve(domain, "A", lifetime=6)
                return [domain]
            except (dns.exception.DNSException, OSError):
                return []
        except (dns.exception.DNSException, OSError) as exc:
            log.debug("mx lookup failed", extra={"data": {"domain": domain, "error": type(exc).__name__}})
            return None

    # ── SMTP ─────────────────────────────────────────────────────────
    async def smtp_probe(self, host: str, rcpts: list[str]) -> dict[str, int | None]:
        """RCPT TO codes for each address on one connection (no message is sent)."""
        codes: dict[str, int | None] = {r: None for r in rcpts}
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, self.smtp_port), self.smtp_timeout)

        async def reply() -> int:
            code = 0
            while True:
                line = await asyncio.wait_for(reader.readline(), self.smtp_timeout)
                if not line:
                    raise ConnectionError("connection closed")
                code = int(line[:3])
                if line[3:4] != b"-":
                    return code

        async def cmd(text: str) -> int:
            writer.write(text.encode() + b"\r\n")
            await writer.drain()
            return await reply()

        try:
            if await reply() >= 400:
                return codes
            if await cmd(f"EHLO {self.smtp_helo}") >= 400 and await cmd(f"HELO {self.smtp_helo}") >= 400:
                return codes
            if await cmd(f"MAIL FROM:<{self.smtp_from}>") >= 400:
                return codes
            for r in rcpts:
                codes[r] = await cmd(f"RCPT TO:<{r}>")
            await cmd("QUIT")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        return codes

    async def _smtp_verify(self, email: str, hosts: list[str]) -> Verification:
        domain = domain_of(email)
        probe = "".join(random.choices(string.ascii_lowercase + string.digits, k=14)) + "@" + domain
        try:
            codes = await self.smtp_probe(hosts[0], [email, probe])
        except (OSError, asyncio.TimeoutError, ConnectionError, ValueError) as exc:
            return Verification("unknown", f"SMTP not reachable ({type(exc).__name__}; port 25 blocked?)", "smtp", hosts)
        code, probe_code = codes[email], codes[probe]
        catch_all = probe_code is not None and probe_code < 300
        self._catch_all_mem[domain] = catch_all
        if code is None:
            return Verification("unknown", "SMTP conversation failed", "smtp", hosts, catch_all)
        if code < 300:
            if catch_all:
                return Verification("catch_all", "server accepts any address", "smtp", hosts, True)
            return Verification("valid", "mailbox accepted (RCPT 250)", "smtp", hosts, False)
        if code in (550, 551, 553, 554):
            return Verification("invalid", f"mailbox rejected ({code})", "smtp", hosts, catch_all)
        return Verification("unknown", f"temporary answer {code} (greylisting?)", "smtp", hosts, catch_all)

    # ── Reacher ──────────────────────────────────────────────────────
    async def _reacher_verify(self, email: str) -> Verification:
        if self.http is None or not self.reacher_url:
            return Verification("unknown", "reacher_url not configured", "reacher")
        headers = {"x-reacher-secret": self.reacher_secret} if self.reacher_secret else {}
        try:
            r = await self.http.request("POST", f"{self.reacher_url}/v0/check_email",
                                        json={"to_email": email}, headers=headers, timeout=60)
            data = r.json()
        except Exception as exc:
            return Verification("unknown", f"Reacher error: {type(exc).__name__}", "reacher")
        reach = data.get("is_reachable", "unknown")
        smtp = data.get("smtp") or {}
        catch_all = smtp.get("is_catch_all")
        status = {"safe": "valid", "invalid": "invalid", "risky": "risky"}.get(reach, "unknown")
        if catch_all:
            status = "catch_all"
        return Verification(status, f"Reacher: {reach}", "reacher", [], catch_all)

    # ── public ───────────────────────────────────────────────────────
    async def verify(self, email: str) -> Verification:
        if reason := reject_reason(email):
            return Verification("invalid", reason, "syntax")
        domain = domain_of(email)
        if domain in DISPOSABLE:
            return Verification("invalid", "disposable address", "syntax")
        if self.mode == "none":
            return Verification("unknown", "verification disabled", "syntax")
        if self.mode == "reacher":
            return await self._reacher_verify(email)
        hosts = await self.mx_hosts(domain)
        if hosts is None:
            return Verification("unknown", "DNS lookup failed", "mx")
        if not hosts:
            return Verification("invalid", "domain does not accept email (no MX)", "mx")
        if self.mode == "smtp":
            return await self._smtp_verify(email, hosts)
        return Verification("unknown", "domain accepts email (MX ok); mailbox not checked", "mx", hosts)
