"""Network signals: TLS certificate, Google PageSpeed Insights, Wayback Machine, sitemap dates."""

from __future__ import annotations

import asyncio
import re
import ssl
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

from leadengine.http import HttpClient
from leadengine.log import get_logger

log = get_logger("website.remote")

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CDX_URL = "https://web.archive.org/cdx/search/cdx"


# ── TLS certificate ──────────────────────────────────────────────────
async def check_certificate(host: str, port: int = 443, timeout: float = 10.0) -> dict[str, Any]:
    """``{valid, error, expires_at, days_left, issuer}``. Reads the certificate even when it
    fails verification (expired / self-signed / wrong host), so we can say why."""
    out: dict[str, Any] = {"valid": False, "error": None, "expires_at": None, "days_left": None, "issuer": None}
    try:
        ctx = ssl.create_default_context()
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=ctx, server_hostname=host), timeout)
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        writer.close()
        out["valid"] = True
    except ssl.SSLCertVerificationError as exc:
        out["error"] = exc.verify_message or "certificate verify failed"
        der = await _raw_certificate(host, port, timeout)
    except (OSError, asyncio.TimeoutError) as exc:
        out["error"] = f"no HTTPS ({type(exc).__name__})"
        return out
    if der:
        out.update(_parse_der(der))
    return out


async def _raw_certificate(host: str, port: int, timeout: float) -> bytes | None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=ctx, server_hostname=host), timeout)
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        writer.close()
        return der
    except (OSError, asyncio.TimeoutError, ssl.SSLError):
        return None


def _parse_der(der: bytes) -> dict[str, Any]:
    try:
        from cryptography import x509
    except ImportError:  # pragma: no cover - cryptography ships with most Python installs
        return {}
    cert = x509.load_der_x509_certificate(der)
    expires = cert.not_valid_after_utc
    issuer = cert.issuer.rfc4514_string()
    org = re.search(r"O=([^,]+)", issuer)
    return {
        "expires_at": expires.date().isoformat(),
        "days_left": (expires - datetime.now(timezone.utc)).days,
        "issuer": org.group(1) if org else issuer[:80],
    }


# ── PageSpeed Insights ───────────────────────────────────────────────
async def pagespeed(http: HttpClient, url: str, api_key: str = "", strategy: str = "mobile") -> dict[str, Any] | None:
    params = {"url": url, "strategy": strategy, "category": "performance"}
    if api_key:
        params["key"] = api_key
    try:
        r = await http.request("GET", PSI_URL, params=params, timeout=90, retries=1)
    except Exception as exc:
        log.warning("pagespeed failed", extra={"data": {"url": url, "error": str(exc)[:120]}})
        return None
    if r.status_code != 200:
        log.warning("pagespeed HTTP error", extra={"data": {"url": url, "status": r.status_code}})
        return {"error": f"HTTP {r.status_code}" + (" (add PAGESPEED_API_KEY)" if r.status_code == 429 else "")}
    data = r.json()
    lh = data.get("lighthouseResult") or {}
    audits = lh.get("audits") or {}

    def num(key: str) -> float | None:
        return (audits.get(key) or {}).get("numericValue")

    perf = ((lh.get("categories") or {}).get("performance") or {}).get("score")
    field = ((data.get("loadingExperience") or {}).get("metrics") or {})
    return {
        "performance": round(perf * 100) if isinstance(perf, (int, float)) else None,
        "lcp_ms": num("largest-contentful-paint"),
        "cls": num("cumulative-layout-shift"),
        "tbt_ms": num("total-blocking-time"),
        "fcp_ms": num("first-contentful-paint"),
        "field_lcp_category": (field.get("LARGEST_CONTENTFUL_PAINT_MS") or {}).get("category"),
        "strategy": strategy,
    }


# ── Wayback Machine ──────────────────────────────────────────────────
async def wayback_span(http: HttpClient, domain: str) -> dict[str, Any] | None:
    """First and latest archive capture of the domain (age + whether it's maintained)."""
    async def one(limit: int) -> str | None:
        try:
            r = await http.request("GET", CDX_URL, params={"url": domain, "output": "json", "fl": "timestamp",
                                                           "limit": limit, "filter": "statuscode:200"},
                                   timeout=30, retries=1)
            rows = r.json() if r.status_code == 200 else []
        except Exception:
            return None
        return rows[1][0] if len(rows) > 1 else None

    first, last = await asyncio.gather(one(1), one(-1))
    if not first:
        return None

    def date(ts: str | None) -> str | None:
        return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if ts else None

    return {"first_capture": date(first), "last_capture": date(last), "first_year": int(first[:4])}


# ── sitemap / Last-Modified ──────────────────────────────────────────
async def sitemap_lastmod(http: HttpClient, base_url: str, max_files: int = 3) -> str | None:
    """Newest ``<lastmod>`` from robots.txt sitemaps or /sitemap.xml (ISO date)."""
    candidates: list[str] = []
    try:
        r = await http.request("GET", base_url.rstrip("/") + "/robots.txt", timeout=10, retries=0)
        if r.status_code == 200:
            candidates += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
    except Exception:
        pass
    candidates += [base_url.rstrip("/") + p for p in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml")]
    newest: str | None = None
    seen = 0
    for url in dict.fromkeys(candidates):
        if seen >= max_files:
            break
        try:
            r = await http.request("GET", url, timeout=10, retries=0)
        except Exception:
            continue
        if r.status_code != 200 or "<" not in r.text[:200]:
            continue
        seen += 1
        try:
            root = ElementTree.fromstring(r.content)
        except ElementTree.ParseError:
            continue
        for el in root.iter():
            if el.tag.endswith("lastmod") and el.text:
                d = el.text.strip()[:10]
                if re.match(r"\d{4}-\d{2}-\d{2}", d) and (newest is None or d > newest):
                    newest = d
    return newest
