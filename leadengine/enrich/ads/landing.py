"""Ad landing-page audit: is the page the advertiser pays Google to send people to any good?

Checks what matters for paid clicks: dedicated landing page vs homepage, message match between the ad
and the page headline, tap-to-call, quote form, call-to-action, mobile layout, speed, HTTPS, trust
signals. Result: ``score`` 0–100 (higher = better page) and ``issues`` in plain English, worst first.
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from leadengine.enrich.website.signals import analyze_html
from leadengine.http import HttpClient
from leadengine.log import get_logger

log = get_logger("ads.landing")
STOP = {"the", "and", "for", "your", "you", "our", "with", "near", "best", "top", "in", "of", "a", "to", "on", "at",
        "call", "now", "today", "free", "get", "us", "we", "is", "are", "services", "service", "company", "llc", "inc"}


def _words(text: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def message_match(ad_text: str | None, page_text: str | None) -> float | None:
    """Share of the ad's meaningful words that appear in the page headline/title (0..1)."""
    ad = _words(ad_text)
    if not ad:
        return None
    return round(len(ad & _words(page_text)) / len(ad), 2)


def page_facts(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    sig = analyze_html(html, date.today().year)
    text = soup.get_text(" ", strip=True).lower()
    return {
        "title": (sig.title or "")[:150], "h1": h1.get_text(" ", strip=True)[:150] if h1 else None,
        "tel_links": sig.tel_links, "forms": sig.forms, "quote_form": sig.quote_form, "cta": sig.cta_phrases[:5],
        "viewport": sig.viewport, "word_count": sig.word_count, "reviews": sig.reviews_section,
        "trust": [t for t in ("licensed", "insured", "bbb", "years", "guarantee", "warranty", "family owned",
                              "5-star", "reviews") if t in text][:5],
        "is_homepage": urlparse(url).path in ("", "/"),
        "https": url.startswith("https://"),
        "nav_links": len(soup.select("nav a")) or len(soup.select("header a")),
    }


def score_landing(f: dict[str, Any], *, ad_title: str | None, load_ms: int | None, mobile: dict | None,
                  pagespeed: dict | None) -> tuple[int, list[str]]:
    issues: list[tuple[float, str]] = []
    score = 100.0
    if f.get("is_homepage"):
        score -= 12
        issues.append((8, "the ad sends visitors to the homepage instead of a page made for that search"))
    mm = message_match(ad_title, " ".join(filter(None, [f.get("h1"), f.get("title")])))
    f["message_match"] = mm
    if mm is not None and mm < 0.34:
        score -= 15
        head = f.get("h1") or f.get("title") or "no headline"
        issues.append((9, f"the page headline doesn't match the ad (ad: \"{(ad_title or '')[:60]}\" → page: \"{head[:60]}\")"))
    if not f.get("tel_links"):
        score -= 15
        issues.append((9, "no tap-to-call button for people coming from the ad on a phone"))
    if not f.get("quote_form") and not f.get("forms"):
        score -= 12
        issues.append((7, "no quote/request form on the landing page"))
    if not f.get("cta"):
        score -= 6
        issues.append((5, "no clear call-to-action (\"Get a quote\", \"Call now\")"))
    if not f.get("viewport"):
        score -= 15
        issues.append((10, "not built for phones (no mobile viewport) — most ad clicks are on phones"))
    elif mobile and (mobile.get("horizontal_overflow_px", 0) > 20 or mobile.get("viewport_width", 0) > 500):
        score -= 10
        issues.append((8, "the landing page doesn't fit a phone screen"))
    perf = (pagespeed or {}).get("performance")
    if perf is not None and perf < 50:
        score -= 12
        issues.append((8 + (50 - perf) / 50, f"slow on phones: Google PageSpeed {perf}/100 — paid clicks leave before it loads"))
    elif load_ms and load_ms > 4000:
        score -= 8
        issues.append((7, f"took {load_ms / 1000:.1f}s to load in our test"))
    if not f.get("https"):
        score -= 10
        issues.append((8, "no HTTPS — Chrome shows \"Not secure\" next to a paid click"))
    if not f.get("reviews") and not f.get("trust"):
        score -= 5
        issues.append((4, "no reviews or trust signals on the landing page"))
    if (f.get("nav_links") or 0) > 12 and f.get("is_homepage"):
        score -= 3
        issues.append((3, f"{f['nav_links']} menu links to wander off to (a landing page should have one goal)"))
    return max(0, round(score)), [t for _, t in sorted(issues, key=lambda x: -x[0])]


async def audit_landing(http: HttpClient, url: str, *, ad_title: str | None = None, renderer=None,
                        pagespeed_key: str = "", use_pagespeed: bool = True, shot_path=None,
                        firecrawl=None) -> dict[str, Any]:
    from leadengine.enrich.firecrawl import needs_js
    from leadengine.enrich.website import remote

    out: dict[str, Any] = {"url": url, "ad_title": ad_title}
    t0 = time.monotonic()
    html, final, status, error = None, url, None, None
    try:
        r = await http.request("GET", url, follow_redirects=True, retries=1)
        html, final, status = r.text, str(r.url), r.status_code
    except Exception as exc:
        error = type(exc).__name__
    out["load_ms"] = int((time.monotonic() - t0) * 1000) if error is None else None
    if firecrawl is not None and firecrawl.enabled and (error or (status or 0) >= 400 or needs_js(html or "")):
        got = await firecrawl.scrape(final)              # protected or JavaScript-only page: read it rendered
        if got and got["status"] < 400 and got["html"]:
            html, final, status, error = got["html"], got["url"], got["status"], None
            out["via_firecrawl"] = True
    if error:
        return {**out, "error": error, "score": 0, "issues": ["the ad's landing page did not load in our test"]}
    out["final_url"] = final
    if (status or 0) >= 400:
        if status in (401, 403, 429, 503):
            return {**out, "status": status, "score": None,
                    "issues": [f"the landing page blocks automatic checks (HTTP {status}) — check it by hand"]}
        return {**out, "status": status, "score": 0,
                "issues": [f"the ad's landing page returns an error (HTTP {status})"]}
    facts = page_facts(html or "", final)
    mobile = None
    if renderer is not None:
        rend = await renderer.render(final, shot_path)
        mobile = rend.get("mobile")
        out["screenshot"], out["mobile_screenshot"] = rend.get("screenshot"), rend.get("mobile_screenshot")
    ps = await remote.pagespeed(http, final, pagespeed_key) if use_pagespeed else None
    score, issues = score_landing(facts, ad_title=ad_title, load_ms=out["load_ms"], mobile=mobile,
                                  pagespeed=ps if ps and not ps.get("error") else None)
    return {**out, "facts": facts, "mobile": mobile, "pagespeed": ps, "score": score, "issues": issues}
