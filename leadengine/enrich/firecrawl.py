"""Firecrawl (firecrawl.dev): reads websites our plain fetch can't — JavaScript-only builders, bot-protected
sites — and lists every page of a site in one call.

Modes (Settings page → Firecrawl, or ``[firecrawl] mode`` in config.toml):

* ``off``      — never used.
* ``fallback`` — only when a page doesn't load normally, or is an empty JavaScript shell (1 credit per page).
* ``smart``    — fallback + a full page list (``/map``, 1 credit) for the Local SEO audit and the top competitors.
* ``full``     — smart + AI extraction of services, service areas, owner and years in business (about 5 credits).

Every call is recorded in the credits page as provider ``firecrawl``, and ``max_per_run`` caps the credits
one run may spend.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from leadengine.config import Settings
from leadengine.http import HttpClient
from leadengine.log import get_logger

log = get_logger("firecrawl")

API = "https://api.firecrawl.dev/v1"
MODES = ("off", "fallback", "smart", "full")

INFO_SCHEMA = {
    "type": "object",
    "properties": {
        "services": {"type": "array", "items": {"type": "string"}, "description": "services the business offers"},
        "service_areas": {"type": "array", "items": {"type": "string"}, "description": "towns/cities/counties served"},
        "owner_name": {"type": "string", "description": "owner or founder full name, if stated"},
        "founded_year": {"type": "integer", "description": "year founded / in business since, if stated"},
        "years_in_business": {"type": "integer"},
        "license_number": {"type": "string"},
        "emails": {"type": "array", "items": {"type": "string"}},
        "phones": {"type": "array", "items": {"type": "string"}},
        "offers": {"type": "array", "items": {"type": "string"}, "description": "discounts, financing, guarantees"},
        "booking": {"type": "boolean", "description": "can customers book or request a quote online"},
    },
}
INFO_PROMPT = ("Extract facts about this local service business from its website: the services it offers, the "
               "towns it serves, the owner's name, the year it was founded or years in business, its license "
               "number, contact emails and phones, special offers, and whether visitors can book or request a "
               "quote online. Leave a field empty when the site doesn't say it.")

_JS_SHELL_HINTS = ("enable javascript", "requires javascript", "you need to enable javascript", "javascript is disabled",
                   "please turn on javascript", "loading...")


def needs_js(html: str) -> bool:
    """True when a page is an empty JavaScript shell: almost no visible text, but scripts that would build it."""
    if not html:
        return True
    low = html.lower()
    body = re.sub(r"(?is)<(script|style|noscript|template)\b.*?</\1>", " ", low)
    text = re.sub(r"(?s)<[^>]+>", " ", body)
    words = len(text.split())
    if words >= 80:
        return False
    if any(h in low for h in _JS_SHELL_HINTS):
        return True
    return low.count("<script") >= 3 and words < 40


class Firecrawl:
    def __init__(self, api_key: str, http: HttpClient, *, mode: str = "smart", credits=None,
                 max_per_run: int = 300, timeout_ms: int = 30000) -> None:
        self.api_key = api_key
        self.http = http
        self.mode = mode if mode in MODES else "smart"
        self.credits = credits
        self.max_per_run = max_per_run
        self.timeout_ms = timeout_ms
        self.used = 0
        self._broken = False          # bad key / out of credits: stop trying for this run
        self._map_cache: dict[str, list[str]] = {}

    # what each mode allows
    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and self.mode != "off" and not self._broken

    @property
    def can_map(self) -> bool:
        return self.enabled and self.mode in ("smart", "full")

    @property
    def can_extract(self) -> bool:
        return self.enabled and self.mode == "full"

    def _budget_ok(self, cost: int) -> bool:
        if self.used + cost > self.max_per_run:
            if self.used <= self.max_per_run:
                log.warning("firecrawl credit cap for this run reached", extra={"data": {"cap": self.max_per_run}})
                self.used = self.max_per_run + 1
            return False
        return True

    async def _post(self, endpoint: str, body: dict[str, Any], cost: int) -> dict[str, Any] | None:
        if not self.enabled or not self._budget_ok(cost):
            return None
        try:
            r = await self.http.request("POST", f"{API}/{endpoint}", json=body, retries=1,
                                        timeout=self.timeout_ms / 1000 + 30,
                                        headers={"Authorization": f"Bearer {self.api_key}"})
        except Exception as exc:
            log.warning("firecrawl call failed", extra={"data": {"endpoint": endpoint, "error": str(exc)[:150]}})
            self._record(endpoint, 0, False, type(exc).__name__)
            return None
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code in (401, 402, 403):
            self._broken = True
            log.warning("firecrawl disabled for this run", extra={"data": {"status": r.status_code,
                                                                             "error": str(data.get("error"))[:150]}})
        if r.status_code != 200 or not data.get("success"):
            self._record(endpoint, 0, False, f"HTTP {r.status_code}: {str(data.get('error'))[:80]}")
            return None
        spent = int(((data.get("data") or {}).get("metadata") or {}).get("creditsUsed") or cost) \
            if isinstance(data.get("data"), dict) else cost
        self.used += spent
        self._record(endpoint, spent, True, body.get("url"))
        return data

    def _record(self, endpoint: str, units: int, ok: bool, note: str | None) -> None:
        if self.credits is not None:
            self.credits.record("firecrawl", endpoint, units=max(1, units), success=ok, note=(note or "")[:200])

    async def scrape(self, url: str, *, links: bool = False) -> dict[str, Any] | None:
        """The fully rendered page: ``{"url", "status", "html", "markdown", "links", "title"}`` (1 credit)."""
        formats = ["rawHtml", "markdown"] + (["links"] if links else [])
        data = await self._post("scrape", {"url": url, "formats": formats, "onlyMainContent": False,
                                           "timeout": self.timeout_ms, "blockAds": True}, 1)
        if not data:
            return None
        d = data.get("data") or {}
        meta = d.get("metadata") or {}
        return {"url": meta.get("url") or meta.get("sourceURL") or url, "status": int(meta.get("statusCode") or 200),
                "html": d.get("rawHtml") or d.get("html") or "", "markdown": d.get("markdown") or "",
                "links": d.get("links") or [], "title": meta.get("title"), "content_type": meta.get("contentType")}

    async def map(self, url: str, *, limit: int = 500) -> list[str] | None:
        """Every page Firecrawl can find on the site (sitemap + links), up to ``limit`` (1 credit)."""
        if not self.can_map:
            return None
        host = (urlsplit(url if "://" in url else "https://" + url).hostname or "").removeprefix("www.")
        if host in self._map_cache:
            return self._map_cache[host]
        data = await self._post("map", {"url": url if "://" in url else "https://" + url, "limit": limit,
                                         "includeSubdomains": False}, 1)
        links = None if data is None else [u for u in data.get("links") or [] if isinstance(u, str)]
        if links is not None:
            self._map_cache[host] = links
        return links

    async def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]] | None:
        """Web search: [{"url", "title", "description"}] (about 2 credits per 10 results). Any mode but off."""
        data = await self._post("search", {"query": query, "limit": limit}, max(1, limit // 5))
        if not data:
            return None
        return [{"url": x.get("url"), "title": x.get("title") or "", "description": x.get("description") or ""}
                for x in data.get("data") or [] if isinstance(x, dict) and x.get("url")]

    async def extract_info(self, url: str) -> dict[str, Any] | None:
        """Structured business facts from the homepage (full mode only; JSON extraction ≈ 5 credits)."""
        if not self.can_extract:
            return None
        data = await self._post("scrape", {"url": url, "formats": ["json"], "onlyMainContent": False,
                                           "timeout": self.timeout_ms,
                                           "jsonOptions": {"schema": INFO_SCHEMA, "prompt": INFO_PROMPT}}, 5)
        if not data:
            return None
        info = (data.get("data") or {}).get("json") or {}
        return {k: v for k, v in info.items() if v not in (None, "", [], 0)}


def build_firecrawl(settings: Settings, http: HttpClient | None, credits=None) -> Firecrawl | None:
    """A client when a key is saved and the mode isn't ``off``; otherwise None (everything works without it)."""
    cfg = settings.section("firecrawl")
    mode = str(cfg.get("mode", "smart")).lower()
    if not settings.firecrawl_api_key or mode == "off" or http is None:
        return None
    return Firecrawl(settings.firecrawl_api_key, http, mode=mode, credits=credits,
                     max_per_run=int(cfg.get("max_per_run", 300)), timeout_ms=int(cfg.get("timeout_ms", 30000)))


# ── page-list analysis (SEO audit) ───────────────────────────────────
_SKIP = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|docx?|xml|css|js)(\?|$)|/(wp-content|wp-json|cdn-cgi|tag|author|"
                   r"category|feed|page/\d+)(/|$)|[?#]", re.I)
_BLOG = re.compile(r"/(blog|news|articles?|posts?|resources|tips)(/|$)", re.I)
_AREA = re.compile(r"(service-areas?|areas-we-serve|areas-served|locations?|cities|communities|near-me)(/|-|$)", re.I)
_NOT_SERVICE = re.compile(r"(^|/|-)(about|contact|privacy|terms|careers?|jobs|faqs?|reviews?|testimonials?|gallery|"
                          r"photos|pricing|coupons?|sitemap|login|cart|thank-you)(/|-|$)", re.I)


def page_stats(urls: list[str], *, service_words: list[str], towns: list[str]) -> dict[str, Any]:
    """Count real pages, service pages, town pages and blog posts from a site's page list."""
    pages = sorted({u.split("#")[0].rstrip("/") for u in urls if u and not _SKIP.search(u)})
    from leadengine.enrich.seo import SERVICE_HINTS

    hints = tuple(SERVICE_HINTS) + tuple(w for w in service_words if len(w) > 3)
    town_slugs = [re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-") for t in towns if len(t) > 3]
    service, area, blog = [], [], []
    for u in pages:
        path = urlsplit(u).path.lower()
        if path in ("", "/"):
            continue
        if _BLOG.search(path):
            blog.append(u)
            continue
        is_area = any(s and s in path for s in town_slugs) or (_AREA.search(path) and path.count("/") >= 2)
        if is_area:
            area.append(u)
        elif any(h in path for h in hints) and not _NOT_SERVICE.search(path):
            service.append(u)
    return {"pages": len(pages), "service_pages": len(service), "area_pages": len(area), "blog_posts": len(blog),
            "examples": {"service": service[:6], "area": area[:6]}}
