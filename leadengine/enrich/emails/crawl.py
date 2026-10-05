"""Polite, bounded website crawler for contact information.

Order: homepage -> pages linked from it that look like contact/about/team/privacy
(scored by URL and link text, footer links count too) -> a few standard paths only for
page types that were not linked. (v3 put guessed paths first in reverse order, so
``/contact`` was never reached.)
"""

from __future__ import annotations

import asyncio
import re
import ssl
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from leadengine.errors import NetworkError
from leadengine.http import HttpClient
from leadengine.log import get_logger
from leadengine.normalize import is_shared_domain, normalize_domain

log = get_logger("emails.crawl")

MAX_BYTES = 1_500_000
LINK_KEYWORDS: list[tuple[str, int, str]] = [
    # (keyword in url/text, score, page type)
    ("contact", 100, "contact"), ("get-in-touch", 95, "contact"), ("reach-us", 90, "contact"),
    ("reach-out", 90, "contact"), ("email-us", 90, "contact"), ("write-us", 80, "contact"),
    ("quote", 60, "contact"), ("estimate", 60, "contact"), ("request-service", 55, "contact"),
    ("about", 85, "about"), ("our-story", 80, "about"), ("who-we-are", 80, "about"), ("company", 50, "about"),
    ("team", 75, "team"), ("staff", 75, "team"), ("owner", 70, "team"), ("people", 60, "team"),
    ("meet", 60, "team"), ("leadership", 60, "team"),
    ("privacy", 45, "legal"), ("legal", 35, "legal"), ("terms", 30, "legal"), ("impressum", 40, "legal"),
    ("location", 30, "other"), ("support", 30, "other"), ("faq", 20, "other"),
]
FALLBACK_PATHS: list[tuple[str, str]] = [
    ("/contact", "contact"), ("/contact-us", "contact"), ("/about", "about"), ("/about-us", "about"),
]
_SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|docx?|xlsx?|mp4|mp3|css|js|ico|xml)(\?|$)", re.I)
FACEBOOK_RE = re.compile(r"https?://(?:www\.|m\.|web\.)?facebook\.com/(?!sharer|share|dialog|plugins|tr\b|events)[A-Za-z0-9.\-_/%]+", re.I)


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    html: str
    kind: str = "home"               # home | contact | about | team | legal | other | facebook


@dataclass
class CrawlResult:
    start_url: str
    pages: list[Page] = field(default_factory=list)
    site_domains: set[str] = field(default_factory=set)
    facebook_urls: list[str] = field(default_factory=list)
    reachable: bool = False
    ssl_error: bool = False          # site only loads with certificate checks off (Phase 4 signal)
    redirected_to: str | None = None
    error: str | None = None


def _clean_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/") or "/", parts.query, ""))


def score_link(href: str, text: str) -> tuple[int, str] | None:
    # "Get in Touch", "get_in_touch" and "/get-in-touch" should all match the same keyword
    hay = re.sub(r"[\s_]+", "-", f"{href} {text}".lower())
    best: tuple[int, str] | None = None
    for kw, score, kind in LINK_KEYWORDS:
        if kw in hay and (best is None or score > best[0]):
            best = (score, kind)
    return best


def _is_ssl_error(exc: BaseException) -> bool:
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(seen) or "SSL" in type(seen).__name__:
            return True
        seen = seen.__cause__ or seen.__context__
    return False


class SiteCrawler:
    def __init__(
        self,
        http: HttpClient,
        *,
        max_pages: int = 8,
        timeout: float = 12.0,
        insecure_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.http = http
        self.max_pages = max_pages
        self.timeout = timeout
        self._insecure: httpx.AsyncClient | None = None
        self._insecure_transport = insecure_transport

    async def aclose(self) -> None:
        if self._insecure is not None:
            await self._insecure.aclose()

    def _insecure_client(self) -> httpx.AsyncClient:
        if self._insecure is None:
            self._insecure = httpx.AsyncClient(
                verify=False, follow_redirects=True, timeout=self.timeout, transport=self._insecure_transport,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                       "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"})
        return self._insecure

    async def fetch(self, url: str, result: CrawlResult) -> Page | None:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                 "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
                   "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"}
        try:
            r = await self.http.request("GET", url, headers=headers, timeout=self.timeout, retries=1)
        except NetworkError as exc:
            if not _is_ssl_error(exc):
                return None
            result.ssl_error = True
            try:
                r = await self._insecure_client().get(url, headers=headers)
            except httpx.HTTPError:
                return None
        if r.status_code >= 400:
            return None
        ctype = r.headers.get("content-type", "text/html")
        if "html" not in ctype and "text/plain" not in ctype:
            return None
        return Page(url, str(r.url), r.status_code, r.text[:MAX_BYTES])

    async def _home(self, website: str, result: CrawlResult) -> Page | None:
        text = website.strip()
        if "://" not in text:
            text = "https://" + text
        parts = urlsplit(text)
        host = parts.netloc
        bare = host[4:] if host.startswith("www.") else host
        tries = [f"https://{host}{parts.path}", f"http://{host}{parts.path}"]
        alt = bare if host.startswith("www.") else f"www.{bare}"
        tries += [f"https://{alt}/", f"http://{alt}/"]
        for url in dict.fromkeys(tries):
            page = await self.fetch(url, result)
            if page is not None:
                return page
        return None

    async def crawl(self, website: str) -> CrawlResult:
        result = CrawlResult(start_url=website)
        home = await self._home(website, result)
        if home is None:
            result.error = "website did not load"
            return result
        result.reachable = True
        result.pages.append(home)
        start_domain = normalize_domain(website)
        final_domain = normalize_domain(home.final_url)
        result.site_domains = {d for d in (start_domain, final_domain) if d and not is_shared_domain(d)}
        if final_domain and final_domain != start_domain:
            result.redirected_to = final_domain

        queue = self.plan(home, result)
        visited = {_clean_url(home.url), _clean_url(home.final_url)}
        todo = [(u, k) for u, k in queue if _clean_url(u) not in visited][: self.max_pages - 1]
        for i in range(0, len(todo), 3):  # small batches: polite but not slow
            batch = todo[i:i + 3]
            pages = await asyncio.gather(*(self.fetch(u, result) for u, _ in batch))
            for (url, kind), page in zip(batch, pages):
                visited.add(_clean_url(url))
                if page is not None and _clean_url(page.final_url) not in {_clean_url(p.final_url) for p in result.pages}:
                    page.kind = kind
                    result.pages.append(page)
        return result

    def plan(self, home: Page, result: CrawlResult) -> list[tuple[str, str]]:
        """Pages to visit after the homepage, best first."""
        soup = BeautifulSoup(home.html, "html.parser")
        footer = soup.find("footer")
        footer_links = set(id(a) for a in footer.find_all("a", href=True)) if footer else set()
        scored: dict[str, tuple[int, str]] = {}
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            full = urljoin(home.final_url, href)
            if FACEBOOK_RE.match(full):
                fb = full.split("?")[0].rstrip("/")
                if fb not in result.facebook_urls:
                    result.facebook_urls.append(fb)
                continue
            dom = normalize_domain(full)
            if not dom or dom not in result.site_domains or _SKIP_EXT.search(full):
                continue
            hit = score_link(urlsplit(full).path, a.get_text(" ", strip=True))
            if hit is None:
                continue
            score, kind = hit
            if id(a) in footer_links:
                score += 5
            key = _clean_url(full)
            if key not in scored or score > scored[key][0]:
                scored[key] = (score, kind)
        ordered = sorted(scored.items(), key=lambda kv: -kv[1][0])
        plan = [(url, kind) for url, (_, kind) in ordered]
        linked_kinds = {kind for _, kind in plan}
        base = f"{urlsplit(home.final_url).scheme}://{urlsplit(home.final_url).netloc}"
        for path, kind in FALLBACK_PATHS:
            if kind not in linked_kinds:
                plan.append((base + path, kind))
        return plan
