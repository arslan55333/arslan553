"""Local SEO score 0–100: on-page basics + Google Business Profile strength + (optional) authority.

Everything is measured, nothing is guessed:
* on-page: title / H1 / meta with city + service, LocalBusiness schema, NAP match with the Google listing,
  service pages, area pages, map embed, reviews on site, sitemap, indexable, content depth, image alts;
* Google profile (from Maps): claimed, photos, review count vs the local competition, review velocity,
  owner replies, categories, hours, website linked;
* authority: Open PageRank (free key ``OPENPAGERANK_API_KEY``), skipped when no key.
"""

from __future__ import annotations

import json
import re
import statistics
from datetime import date
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from leadengine.http import HttpClient
from leadengine.log import get_logger
from leadengine.normalize import normalize_phone

log = get_logger("seo")
LOCAL_TYPES = {"localbusiness", "homeandconstructionbusiness", "professionalservice", "plumber", "electrician",
               "roofingcontractor", "hvacbusiness", "locksmith", "movingcompany", "generalcontractor",
               "housepainter", "automotivebusiness", "autorepair", "dentist", "medicalbusiness", "legalservice",
               "attorney", "lawfirm", "homeimprovement", "store", "restaurant", "healthandbeautybusiness"}
SERVICE_HINTS = ("service", "repair", "install", "replacement", "cleaning", "removal", "rental", "inspection",
                 "maintenance", "emergency", "residential", "commercial", "pumping", "remodel", "roof", "drain")


def _types(node: Any) -> set[str]:
    t = node.get("@type") if isinstance(node, dict) else None
    items = t if isinstance(t, list) else [t]
    return {str(x).lower() for x in items if x}


def schema_nodes(soup: BeautifulSoup) -> list[dict]:
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (ValueError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            n = stack.pop()
            if isinstance(n, dict):
                out.append(n)
                stack.extend(v for v in n.get("@graph", []) if isinstance(v, dict))
            elif isinstance(n, list):
                stack.extend(n)
    return out


def onpage_checks(html: str, url: str, *, city: str | None, service: str | None, phone: str | None,
                  towns: list[str] | None = None) -> tuple[float, list[tuple[float, str]], list[str], dict]:
    """(fraction 0..1, weighted issues, positives, facts) for the homepage."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    low = text.lower()
    city_l = (city or "").lower()
    svc_words = [w for w in re.findall(r"[a-z]+", (service or "").lower()) if len(w) > 3]
    title = (soup.title.get_text(strip=True) if soup.title else "")[:200]
    h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    meta_d = (meta.get("content") or "") if meta else ""
    robots = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    nodes = schema_nodes(soup)
    local = [n for n in nodes if _types(n) & LOCAL_TYPES]
    host = urlparse(url).netloc
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"])
        if urlparse(href).netloc == host:
            links.append((href.lower(), a.get_text(" ", strip=True).lower()))
    service_pages = {h for h, t in links if any(k in h or k in t for k in SERVICE_HINTS + tuple(svc_words))
                     and urlparse(h).path not in ("", "/")}
    area_pages = {h for h, t in links if any(tn.lower() in h.replace("-", " ") or tn.lower() in t
                                              for tn in (towns or []) if len(tn) > 3)}
    page_digits = re.sub(r"\D", "", text)
    phone_n = normalize_phone(phone)
    imgs = soup.find_all("img")
    no_alt = sum(1 for i in imgs if not (i.get("alt") or "").strip())
    facts = {"title": title, "h1": h1s[:3], "meta_description": meta_d[:200], "schema_types": sorted({t for n in nodes for t in _types(n)})[:8],
             "local_schema": bool(local), "service_pages": len(service_pages), "area_pages": len(area_pages),
             "words": len(text.split()), "images": len(imgs), "images_no_alt": no_alt}

    checks: list[tuple[float, bool, str, str]] = [   # (weight, ok, issue, positive)
        (8, bool(title) and city_l in title.lower(), f"page title doesn't mention {city or 'your city'}"
         + (f" (it is \"{title[:60]}\")" if title else " (no title at all)"), "city in the page title"),
        (6, bool(title) and any(w in title.lower() for w in svc_words) if svc_words else bool(title),
         "page title doesn't say what you do", "service in the page title"),
        (6, len(h1s) >= 1, "no main headline (H1) for Google to read", "has an H1 headline"),
        (4, any(city_l and city_l in h.lower() or any(w in h.lower() for w in svc_words) for h in h1s),
         "headline doesn't mention the service or city", "headline names the service/city"),
        (4, bool(meta_d), "no meta description (Google writes its own snippet)", "has a meta description"),
        (9, bool(local), "no LocalBusiness schema markup (helps Google understand your business, area and hours)",
         "LocalBusiness schema markup"),
        (8, bool(phone_n) and phone_n in page_digits, "phone number on the site doesn't match the Google listing"
         if phone_n else "no phone number found on the site", "phone matches the Google listing"),
        (5, bool(city_l) and city_l in low, f"{city or 'your city'} isn't mentioned on the homepage", "city mentioned on the page"),
        (9, len(service_pages) >= 3, f"only {len(service_pages)} service page(s) — each service needs its own page to rank",
         f"{len(service_pages)} service pages"),
        (7, len(area_pages) >= 2 or "service area" in low or "areas we serve" in low,
         "no pages or section for the towns you serve", "service-area pages/section"),
        (3, "google.com/maps" in html or "maps.googleapis" in html or "g.page" in html,
         "no Google Map / directions link", "map or directions link"),
        (5, any(k in low for k in ("review", "testimonial", "what our customers say")),
         "no reviews or testimonials on the website", "reviews shown on the site"),
        (5, len(text.split()) >= 300, f"thin homepage ({len(text.split())} words) — not much for Google to rank",
         "enough content on the homepage"),
        (3, not imgs or no_alt / max(1, len(imgs)) <= 0.5, f"{no_alt} of {len(imgs)} images have no description (alt text)",
         "images have alt text"),
        (8, not (robots and "noindex" in (robots.get("content") or "").lower()),
         "the homepage tells Google NOT to index it (noindex)", "indexable by Google"),
    ]
    total = sum(w for w, *_ in checks)
    got = sum(w for w, ok, *_ in checks if ok)
    issues = [(w, issue) for w, ok, issue, _ in checks if not ok]
    positives = [pos for _, ok, _, pos in checks if ok]
    return got / total, issues, positives, facts


def gbp_checks(b, competitors: list[dict], today: date | None = None) -> tuple[float | None, list[tuple[float, str]], list[str], dict]:
    """Google Business Profile strength from what the Maps scrape recorded."""
    today = today or date.today()
    checks: list[tuple[float, bool, str, str]] = []
    if b.claimed is not None:
        checks.append((10, bool(b.claimed), "Google listing is not claimed by the owner", "listing claimed"))
    if b.photo_count is not None:
        checks.append((6, b.photo_count >= 20, f"only {b.photo_count} photos on the Google listing (aim for 20+)",
                       f"{b.photo_count} photos"))
    comp_reviews = [c["review_count"] for c in competitors if c.get("review_count")]
    median = statistics.median(comp_reviews) if comp_reviews else None
    if b.review_count is not None and median:
        checks.append((9, b.review_count >= 0.6 * median,
                       f"{b.review_count} reviews vs about {int(median)} for the businesses that rank above you",
                       "review count competitive"))
    dates = [date.fromisoformat(d) for d in (b.recent_review_dates or []) if re.match(r"\d{4}-\d\d-\d\d", d)]
    if dates:
        in90 = sum(1 for d in dates if (today - d).days <= 90)
        checks.append((7, in90 >= 3, f"only {in90} new review(s) in the last 90 days", f"{in90} reviews in 90 days"))
    if b.owner_response_rate is not None:
        checks.append((5, b.owner_response_rate >= 0.5, f"owner replies to {int(b.owner_response_rate * 100)}% of reviews",
                       "owner replies to reviews"))
    checks.append((4, len(b.categories or []) >= 2, "only one business category on Google (add secondary categories)",
                   "multiple categories"))
    checks.append((3, bool(b.hours), "no opening hours on the Google listing", "hours listed"))
    checks.append((5, bool(b.website), "no website linked from the Google listing", "website linked"))
    total = sum(w for w, *_ in checks)
    got = sum(w for w, ok, *_ in checks if ok)
    return (got / total if total else None), [(w, i) for w, ok, i, _ in checks if not ok], \
        [p for _, ok, _, p in checks if ok], {"competitor_median_reviews": median}


async def open_pagerank(http: HttpClient, domain: str, api_key: str) -> float | None:
    if not api_key or not domain:
        return None
    try:
        r = await http.request("GET", "https://openpagerank.com/api/v1.0/getPageRank", params={"domains[]": domain},
                               headers={"API-OPR": api_key}, retries=1)
        rows = (r.json() or {}).get("response") or []
        val = rows[0].get("page_rank_decimal") if rows else None
        return float(val) if val not in (None, "") else None
    except Exception as exc:
        log.warning("open pagerank failed", extra={"data": {"domain": domain, "error": str(exc)[:100]}})
        return None


def combine(onpage: float | None, gbp: float | None, authority: float | None) -> int | None:
    parts = [(0.55, onpage), (0.35, gbp), (0.10, None if authority is None else min(1.0, authority / 5))]
    known = [(w, v) for w, v in parts if v is not None]
    if not known:
        return None
    return round(100 * sum(w * v for w, v in known) / sum(w for w, _ in known))


async def seo_audit(http: HttpClient, b, *, service: str | None, competitors: list[dict], towns: list[str],
                    opr_key: str = "") -> dict[str, Any]:
    onpage = None
    issues: list[tuple[float, str]] = []
    positives: list[str] = []
    facts: dict[str, Any] = {}
    if b.website:
        try:
            r = await http.request("GET", b.website, follow_redirects=True, retries=1)
            if r.status_code < 400 and "html" in r.headers.get("content-type", "html"):
                onpage, oi, op, facts = onpage_checks(r.text, str(r.url), city=b.city, service=service,
                                                      phone=b.phone, towns=towns)
                issues += oi
                positives += op
                base = f"{urlparse(str(r.url)).scheme}://{urlparse(str(r.url)).netloc}"
                try:
                    sm = await http.request("GET", base + "/sitemap.xml", retries=0)
                    facts["sitemap"] = sm.status_code == 200 and ("<urlset" in sm.text or "<sitemapindex" in sm.text)
                except Exception:
                    facts["sitemap"] = False
                if not facts["sitemap"]:
                    issues.append((3, "no sitemap.xml for Google to find all pages"))
            else:
                issues.append((10, f"website didn't load for the SEO check (HTTP {r.status_code})"))
        except Exception as exc:
            issues.append((10, f"website didn't load for the SEO check ({type(exc).__name__})"))
    else:
        issues.append((15, "no website, so there is nothing for Google to rank besides the listing"))
        onpage = 0.0
    gbp, gi, gp, gfacts = gbp_checks(b, competitors)
    issues += gi
    positives += gp
    facts.update(gfacts)
    authority = await open_pagerank(http, b.domain, opr_key)
    facts["open_pagerank"] = authority
    score = combine(onpage, gbp, authority)
    return {"score": score, "onpage": None if onpage is None else round(onpage * 100),
            "gbp": None if gbp is None else round(gbp * 100), "authority": authority,
            "issues": [t for _, t in sorted(issues, key=lambda x: -x[0])], "positives": positives, "facts": facts}
