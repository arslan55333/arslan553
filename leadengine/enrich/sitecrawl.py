"""Whole-site SEO crawl (an Ahrefs-style site audit, small and cheap) + content gap vs competitors.

Site crawl:
* page list from Firecrawl ``/map`` (1 credit) — or free from ``sitemap.xml`` / the homepage links;
* every page (up to ``max_pages``) is fetched by our own client — free — and checked for: broken (4xx/5xx),
  slow, missing / duplicate / too-long titles, missing meta descriptions, no or several H1s, thin content,
  noindex, missing canonical, images without alt text, no LocalBusiness schema.

Content gap:
* the page lists of the 3 businesses ranking above it are compared with its own: service and town pages the
  competitors have and it doesn't — the exact pages to build (and the pitch).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from leadengine.log import get_logger

log = get_logger("sitecrawl")

ASSET = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|docx?|xlsx?|mp4|mp3|css|js|ico|xml|txt)(\?|$)", re.I)
SKIP_PATH = re.compile(r"/(wp-json|wp-content|wp-admin|cdn-cgi|feed|tag|author|cart|checkout|my-account|login)(/|$)", re.I)
BORING = {"about", "about-us", "contact", "contact-us", "privacy", "privacy-policy", "terms", "terms-of-service",
          "terms-and-conditions", "blog", "faq", "faqs", "reviews", "testimonials", "gallery", "careers", "jobs",
          "sitemap", "thank-you", "thanks", "home", "index", "accessibility", "cookie-policy", "news", "team",
          "our-team", "coupons", "specials", "financing", "locations", "service-areas", "areas-we-serve", "services"}
STOP = {"and", "the", "in", "of", "for", "near", "me", "ny", "nj", "ca", "tx", "fl", "best", "top", "a", "to", "our",
        "services", "service", "company", "companies", "llc", "inc", "html", "php", "page", "pages", "us"}


def same_site(url: str, host: str) -> bool:
    h = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return h == host


def clean_urls(urls: list[str], host: str, limit: int) -> list[str]:
    out, seen = [], set()
    for u in urls:
        u = u.split("#")[0]
        if not u.startswith("http") or not same_site(u, host) or ASSET.search(u) or SKIP_PATH.search(u) or "?" in u:
            continue
        key = u.rstrip("/").lower().replace("://www.", "://")
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    out.sort(key=lambda u: (urlsplit(u).path.strip("/") != "", urlsplit(u).path.count("/"), len(u)))
    return out[:limit]


async def page_list(http, url: str, *, firecrawl=None, limit: int = 300) -> tuple[list[str], str]:
    base = url if "://" in url else "https://" + url
    host = (urlsplit(base).hostname or "").lower().removeprefix("www.")
    if firecrawl is not None and firecrawl.can_map:
        got = await firecrawl.map(base, limit=limit)
        if got:
            return clean_urls(got, host, limit), "firecrawl"
    root = f"{urlsplit(base).scheme}://{urlsplit(base).netloc}"
    try:
        r = await http.request("GET", root + "/sitemap.xml", retries=0, timeout=15)
        locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", r.text) if r.status_code == 200 else []
        subs = [x for x in locs if x.endswith(".xml")][:5]
        for sm in subs:
            try:
                r2 = await http.request("GET", sm, retries=0, timeout=15)
                locs += re.findall(r"<loc>\s*([^<]+?)\s*</loc>", r2.text)
            except Exception:
                continue
        pages = [x for x in locs if not x.endswith(".xml")]
        if pages:
            return clean_urls(pages, host, limit), "sitemap"
    except Exception:
        pass
    try:
        r = await http.request("GET", base, retries=0, timeout=15)
        links = [urljoin(str(r.url), a.get("href", "")) for a in BeautifulSoup(r.text, "html.parser").find_all("a", href=True)]
        return clean_urls([str(r.url)] + links, host, limit), "homepage links"
    except Exception:
        return [], "none"


def check_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "")
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    robots = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    canonical = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
    for t in soup(["script", "style", "noscript"]):
        if t.get("type") != "application/ld+json":
            t.decompose()
    words = len(soup.get_text(" ", strip=True).split())
    imgs = soup.find_all("img")
    return {"title": title[:120], "title_len": len(title), "meta": bool(meta and (meta.get("content") or "").strip()),
            "h1": len(soup.find_all("h1")), "words": words,
            "noindex": bool(robots and "noindex" in (robots.get("content") or "").lower()),
            "canonical": bool(canonical), "img_no_alt": sum(1 for i in imgs if not (i.get("alt") or "").strip()),
            "schema": "localbusiness" in html.lower() or '"@type":"plumber"' in html.lower().replace(" ", "")}


async def crawl_site(http, url: str, *, firecrawl=None, max_pages: int = 40, concurrency: int = 5,
                     urls: list[str] | None = None, source: str = "", js_pages: int = 15) -> dict[str, Any]:
    if urls is None:
        urls, source = await page_list(http, url, firecrawl=firecrawl)
    total_known = len(urls)
    urls = urls[:max_pages]
    sem = asyncio.Semaphore(concurrency)
    pages: list[dict[str, Any]] = []

    async def one(u: str) -> None:
        async with sem:
            t0 = time.monotonic()
            try:
                r = await http.request("GET", u, retries=0, timeout=20)
            except Exception as exc:
                pages.append({"url": u, "status": None, "error": type(exc).__name__})
                return
            ms = int((time.monotonic() - t0) * 1000)
            row: dict[str, Any] = {"url": u, "status": r.status_code, "ms": ms,
                                   "redirected": str(r.url).rstrip("/") != u.rstrip("/")}
            if r.status_code < 400 and "html" in r.headers.get("content-type", "html"):
                row.update(check_page(r.text, str(r.url)))
            pages.append(row)

    await asyncio.gather(*(one(u) for u in urls))
    pages.sort(key=lambda p: urls.index(p["url"]) if p["url"] in urls else 999)
    rendered = False
    if looks_js_built(pages):
        # one shared title and no H1 anywhere = the pages are built by JavaScript; read them rendered
        if firecrawl is not None and firecrawl.enabled:
            rendered = True
            for row in [p for p in pages if p.get("status") and p["status"] < 400][:js_pages]:
                got = await firecrawl.scrape(row["url"])
                if got and got.get("html"):
                    row.update(check_page(got["html"], row["url"]), rendered=True)
    out = {"source": source, "pages_known": total_known, "pages": pages, "rendered": rendered,
           "js_site": looks_js_built(pages) or rendered, **summarize(pages)}
    if rendered:
        out["issues"].insert(0, "the site is built with JavaScript: before scripts run, every page has the same title "
                                "and almost no text — Google indexes that slower and less reliably than plain HTML")
    elif out["js_site"]:
        out["issues"].insert(0, "the site is built with JavaScript — Google may not see its content the way visitors do "
                                "(add a Firecrawl key to check each page as Google renders it)")
    return out


def looks_js_built(pages: list[dict[str, Any]]) -> bool:
    ok = [p for p in pages if p.get("status") and p["status"] < 400 and "words" in p and not p.get("rendered")]
    if len(ok) < 3:
        return False
    titles = Counter(p.get("title") or "" for p in ok)
    return titles.most_common(1)[0][1] >= 0.8 * len(ok) and all(p.get("h1", 0) == 0 for p in ok)


def summarize(pages: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [p for p in pages if p.get("status") and p["status"] < 400 and "words" in p]
    broken = [p for p in pages if p.get("status") is None or p["status"] >= 400]
    titles = Counter(p["title"].strip().lower() for p in ok if p.get("title"))
    dup = [t for t, n in titles.items() if n > 1]
    checks = [   # (weight, count, message)
        (10, len(broken), "broken page(s) — visitors and Google hit an error"),
        (6, sum(1 for p in ok if not p.get("title")), "page(s) with no title"),
        (5, sum(1 for p in ok if p.get("title") and titles[p["title"].strip().lower()] > 1),
         f"page(s) share a duplicate title ({len(dup)} title(s) used more than once)"),
        (3, sum(1 for p in ok if p.get("title_len", 0) > 65), "title(s) too long for Google (over 65 characters)"),
        (5, sum(1 for p in ok if not p.get("meta")), "page(s) with no meta description"),
        (5, sum(1 for p in ok if p.get("h1") == 0), "page(s) with no H1 headline"),
        (2, sum(1 for p in ok if p.get("h1", 0) > 1), "page(s) with more than one H1"),
        (6, sum(1 for p in ok if p.get("words", 0) < 300), "thin page(s) (under 300 words)"),
        (8, sum(1 for p in ok if p.get("noindex")), "page(s) hidden from Google (noindex)"),
        (2, sum(1 for p in ok if not p.get("canonical")), "page(s) without a canonical tag"),
        (3, sum(1 for p in ok if p.get("img_no_alt", 0) > 0), "page(s) with images missing alt text"),
        (4, sum(1 for p in ok if p.get("ms", 0) > 3000), "slow page(s) (over 3 seconds)"),
    ]
    issues, penalty = [], 0.0
    n = max(1, len(pages))
    for w, count, msg in checks:
        if count:
            issues.append((w * count / n, f"{count} {msg}"))
            penalty += w * min(1.0, count / n)
    if ok and not any(p.get("schema") for p in ok):
        issues.append((3, "no LocalBusiness schema on any page"))
        penalty += 4
    score = max(0, round(100 - penalty * 1.6))
    return {"checked": len(pages), "broken": len(broken), "score": score,
            "issues": [m for _, m in sorted(issues, key=lambda x: -x[0])],
            "duplicate_titles": dup[:5]}


# ── content gap ──────────────────────────────────────────────────────
def topic(url: str) -> str | None:
    parts = [p for p in urlsplit(url).path.lower().split("/") if p]
    if not parts or parts[0] in ("blog", "news", "posts", "articles", "tag", "category") or parts[-1] in BORING:
        return None
    words = [w for w in re.split(r"[-_\s.]+", parts[-1]) if w and w not in STOP and not w.isdigit()]
    return " ".join(words) if words else None


def _covered(t: str, mine: list[set[str]], town_words: set[str] = frozenset()) -> bool:
    ws = set(t.split())
    towns = ws & town_words
    if towns:                                  # a town page is covered only by a page for the same town
        return any(towns & m for m in mine)
    return any(len(ws & m) / max(1, len(ws | m)) >= 0.5 or ws <= m for m in mine)


SERVICE_WORDS = ("removal", "rental", "repair", "install", "replace", "cleaning", "cleanout", "clean out", "service",
                 "pumping", "inspection", "maintenance", "emergency", "residential", "commercial", "demolition",
                 "dumpster", "junk", "haul", "roof", "drain", "sewer", "water", "heater", "hvac", "furnace",
                 "electric", "plumb", "pest", "termite", "tree", "lawn", "landscap", "paint", "remodel", "kitchen",
                 "air conditioning",
                 "bath", "floor", "fence", "concrete", "gutter", "window", "door", "garage", "move", "moving",
                 "storage", "disposal", "recycl", "container", "roll off", "debris", "appliance", "furniture",
                 "mattress", "estate", "construction", "yard", "hot tub", "piano", "shed", "carpet")


def is_service(t: str, service_words: tuple[str, ...] = SERVICE_WORDS) -> bool:
    words = t.split()
    return any((" " in w and w in t) or any(x.startswith(w) for x in words) for w in service_words)


def content_gap(own: list[str], competitors: dict[str, list[str]], towns: list[str], limit: int = 15,
                service_words: tuple[str, ...] = SERVICE_WORDS) -> dict[str, Any]:
    """Service / town pages the competitors have and this site doesn't (holiday, news and other pages ignored)."""
    mine = [set(t.split()) for t in (topic(u) for u in own) if t]
    town_words = {w for t in towns for w in re.findall(r"[a-z]+", t.lower()) if len(w) > 3}
    counts: dict[str, set[str]] = {}
    example: dict[str, str] = {}
    for name, urls in competitors.items():
        for u in urls:
            t = topic(u)
            if not t or _covered(t, mine, town_words):
                continue
            if not (set(t.split()) & town_words) and not is_service(t, service_words):
                continue                      # not a service or town page (holiday hours, news, team …)
            counts.setdefault(t, set()).add(name)
            example.setdefault(t, u)
    rows = sorted(({"topic": t, "competitors": len(n), "who": sorted(n), "example": example[t],
                    "kind": "town" if set(t.split()) & town_words else "service"}
                   for t, n in counts.items()), key=lambda r: (-r["competitors"], r["topic"]))
    return {"missing_services": [r for r in rows if r["kind"] == "service"][:limit],
            "missing_towns": [r for r in rows if r["kind"] == "town"][:limit],
            "own_topics": len(mine), "competitors_checked": len(competitors)}
