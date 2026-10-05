"""Rotating proxy pool with health checks and automatic temporary bans.

Proxies come from ``PROXIES`` (comma/newline separated) or ``PROXY_FILE`` in ``.env``.
Accepted formats: ``http://user:pass@host:port``, ``host:port``, ``host:port:user:pass``,
``socks5://host:port``. With no proxies everything still works (direct connection).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from leadengine.log import get_logger

log = get_logger("proxy")

HEALTH_URL = "https://www.google.com/generate_204"


@dataclass
class Proxy:
    url: str                       # normalised: scheme://[user:pass@]host:port
    failures: int = 0
    successes: int = 0
    banned_until: float = 0.0
    last_error: str | None = field(default=None, repr=False)

    @property
    def label(self) -> str:
        """Host:port only — never print credentials."""
        parts = urlsplit(self.url)
        return f"{parts.hostname}:{parts.port}"

    def playwright(self) -> dict[str, str]:
        parts = urlsplit(self.url)
        out = {"server": f"{parts.scheme}://{parts.hostname}:{parts.port}"}
        if parts.username:
            out["username"] = parts.username
            out["password"] = parts.password or ""
        return out


def parse_proxy(line: str) -> str | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "://" in line:
        parts = urlsplit(line)
        return line if parts.hostname and parts.port else None
    bits = line.split(":")
    if len(bits) == 2:
        return f"http://{bits[0]}:{bits[1]}"
    if len(bits) == 4:
        host, port, user, pwd = bits
        return f"http://{quote(user, safe='')}:{quote(pwd, safe='')}@{host}:{port}"
    return None


class ProxyPool:
    def __init__(self, urls: list[str], *, max_failures: int = 3, ban_seconds: float = 600.0) -> None:
        self._proxies = [Proxy(u) for u in dict.fromkeys(urls)]
        self._index = 0
        self.max_failures = max_failures
        self.ban_seconds = ban_seconds
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls, env_value: str = "", file_path: str = "", **kw) -> "ProxyPool":
        lines = env_value.replace(",", "\n").splitlines()
        if file_path and Path(file_path).exists():
            lines += Path(file_path).read_text("utf-8").splitlines()
        return cls([u for u in (parse_proxy(x) for x in lines) if u], **kw)

    def __len__(self) -> int:
        return len(self._proxies)

    @property
    def enabled(self) -> bool:
        return bool(self._proxies)

    def proxies(self) -> list[Proxy]:
        return list(self._proxies)

    def available(self, now: float | None = None) -> list[Proxy]:
        now = now or time.monotonic()
        return [p for p in self._proxies if p.banned_until <= now]

    def next(self) -> Proxy | None:
        """Round-robin over healthy proxies. ``None`` = go direct (no proxies or all banned)."""
        live = self.available()
        if not live:
            if self._proxies:
                log.warning("all proxies banned, using direct connection")
            return None
        proxy = live[self._index % len(live)]
        self._index += 1
        return proxy

    def report(self, proxy: Proxy | None, ok: bool, error: str | None = None) -> None:
        if proxy is None:
            return
        if ok:
            proxy.successes += 1
            proxy.failures = 0
            return
        proxy.failures += 1
        proxy.last_error = error
        if proxy.failures >= self.max_failures:
            proxy.banned_until = time.monotonic() + self.ban_seconds
            proxy.failures = 0
            log.warning("proxy banned", extra={"data": {"proxy": proxy.label, "seconds": self.ban_seconds, "error": error}})

    def ban(self, proxy: Proxy | None, reason: str) -> None:
        """Immediate ban (e.g. Google showed a captcha through this proxy)."""
        if proxy is None:
            return
        proxy.banned_until = time.monotonic() + self.ban_seconds
        proxy.last_error = reason
        log.warning("proxy banned", extra={"data": {"proxy": proxy.label, "reason": reason}})

    async def health_check(self, url: str = HEALTH_URL, timeout: float = 10.0) -> dict[str, bool]:
        """Test every proxy in parallel; dead ones are banned. Returns ``{label: ok}``."""

        async def check(p: Proxy) -> tuple[str, bool]:
            try:
                async with httpx.AsyncClient(proxy=p.url, timeout=timeout) as client:
                    r = await client.get(url)
                ok = r.status_code < 400
                err = None if ok else f"HTTP {r.status_code}"
            except Exception as exc:
                ok, err = False, type(exc).__name__
            if ok:
                self.report(p, True)
            else:
                self.ban(p, f"health check failed: {err}")
            return p.label, ok

        results = await asyncio.gather(*(check(p) for p in self._proxies))
        return dict(results)
