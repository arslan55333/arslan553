"""Facts + copy for a preview page. Copy comes from the configured LLM, or from safe templates.

The model only gets verified facts and is told not to invent claims; a post-filter also drops
sentences with unverifiable specifics (years in business, awards, prices, licences)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from leadengine.db.models import Business
from leadengine.geo.zipdata import zip_directory
from leadengine.llm import LLM, LLMError
from leadengine.log import get_logger
from leadengine.normalize import normalize_phone

log = get_logger("preview")

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
RISKY = re.compile(r"#1\b|\$\d|\b(\d+\+?\s*years?|since \d{4}|number one|award|licen[sc]ed|insured|bonded|"
                   r"certified|guarantee[d]?|warrant(y|ies)|cheapest|lowest price|\d+% off|best in)\b", re.I)


@dataclass
class Facts:
    name: str
    category: str
    city: str
    state: str
    phone: str | None
    phone_href: str | None
    rating: float | None
    review_count: int | None
    service_area: list[str]
    hours: list[tuple[str, str]]
    reviews: list[dict[str, Any]]
    owner: str | None
    services_hint: list[str] = field(default_factory=list)
    # from the business's own website (Firecrawl site facts) — their claims, safe to show back to them
    services: list[str] = field(default_factory=list)
    founded_year: int | None = None
    license: str | None = None
    offers: list[str] = field(default_factory=list)
    address: str | None = None
    keyword: str | None = None
    niche: str = ""
    theme: str = "indigo"
    # concept notes: what this page fixes vs. the current site (shown to the owner, not to customers)
    current_site: str | None = None
    current_score: int | None = None
    current_speed: int | None = None
    current_screenshot: str | None = None
    improvements: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Copy:
    headline: str
    subheadline: str
    about: str
    services: list[dict[str, str]]
    why_us: list[str]
    faq: list[dict[str, str]]
    cta: str
    source: str  # "ai" | "template"


def normalize_hours(raw: Any) -> list[tuple[str, str]]:
    """Maps JSON dict, Places weekdayDescriptions list, or SerpAPI dict -> [(day, hours)] Monday first."""
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            day = next((d for d in DAYS if d.lower().startswith(str(k).lower()[:3])), None)
            if day:
                out[day] = v if isinstance(v, str) else ", ".join(map(str, v)) if isinstance(v, list) else str(v)
    elif isinstance(raw, list):
        for line in raw:
            if isinstance(line, str) and ":" in line:
                k, v = line.split(":", 1)
                day = next((d for d in DAYS if d.lower() == k.strip().lower()), None)
                if day:
                    out[day] = v.strip()
    return [(d, out[d].replace(" ", " ").replace(" ", " ")) for d in DAYS if d in out]


def service_area(b: Business, limit: int = 6) -> list[str]:
    names = [b.city] if b.city else []
    z = zip_directory().get(b.zip_code) if b.zip_code else None
    if z:
        for other, _ in zip_directory().nearby(z.lat, z.lng, 25):
            if other.city not in names and other.state == z.state:
                names.append(other.city)
            if len(names) >= limit:
                break
    return names


THEMES = {"plumber": "blue", "hvac": "teal", "roofing": "red", "junk removal": "green", "dumpster rental": "green",
          "electrician": "amber", "landscaping": "green", "tree service": "green", "pest control": "teal",
          "moving": "indigo", "painting": "violet", "remodeling": "slate", "house cleaning": "teal"}


def _reviews(activity: dict[str, Any] | None, audit: dict[str, Any] | None, limit: int = 6) -> list[dict[str, Any]]:
    out = list((activity or {}).get("top_reviews") or [])
    seen = {r.get("text", "")[:40] for r in out}
    for r in (audit or {}).get("good_reviews") or []:
        if r.get("text", "")[:40] not in seen:
            out.append(r)
    return out[:limit]


def improvements(web: dict[str, Any] | None, no_site: bool) -> list[str]:
    """What the concept page fixes compared with the business's current site (for the owner)."""
    if no_site:
        return ["A real website of your own for people who find you on Google and in your ads",
                "Tap-to-call and a quote form on every screen", "Service and town sections Google can rank",
                "LocalBusiness + FAQ schema so Google understands your business"]
    web = web or {}
    h, out = web.get("html") or {}, []
    perf = (web.get("pagespeed") or {}).get("performance")
    if perf is not None and perf < 70:
        out.append(f"Your site scores {perf}/100 on Google's mobile speed test — this page is a single light file")
    if not h.get("viewport"):
        out.append("Your site isn't built for phones — this one is mobile-first")
    if not h.get("tel_links"):
        out.append("Tap-to-call button (your site has none) — plus a sticky call bar on phones")
    if not (h.get("quote_form") or h.get("forms")):
        out.append("Quote form above the fold (your site has no form)")
    if not h.get("reviews_section"):
        out.append("Your real Google reviews on the page (your site shows none)")
    out += ["LocalBusiness + FAQ schema markup for Google", "A section for every town you serve"]
    return out[:6]


def gather_facts(b: Business, activity: dict[str, Any] | None, *, site_info: dict[str, Any] | None = None,
                 reviews_audit: dict[str, Any] | None = None, website: dict[str, Any] | None = None,
                 keyword: str | None = None) -> Facts:
    from leadengine.insights import niche_for
    from leadengine.normalize import is_shared_domain

    phone_n = normalize_phone(b.phone)
    category = (b.categories or ["Local service"])[0]
    info = site_info or {}
    niche = niche_for(keyword, *(b.categories or []))["niche"]
    areas = service_area(b)
    for a in info.get("service_areas") or []:
        if isinstance(a, str) and a not in areas and len(areas) < 12:
            areas.append(a)
    no_site = not b.website or is_shared_domain(b.domain or "")
    year = info.get("founded_year")
    return Facts(
        name=b.name, category=category, city=b.city or "", state=b.state or "", phone=b.phone,
        phone_href=f"tel:+1{phone_n}" if phone_n and len(phone_n) == 10 else (f"tel:{phone_n}" if phone_n else None),
        rating=b.rating, review_count=b.review_count, service_area=areas,
        hours=normalize_hours(b.hours), reviews=_reviews(activity, reviews_audit),
        owner=b.owner_name, services_hint=[c for c in (b.categories or [])[:6]],
        services=[str(x)[:60] for x in (info.get("services") or []) if x][:8],
        founded_year=int(year) if isinstance(year, int) and 1900 < year <= 2026 else None,
        license=str(info["license_number"])[:40] if info.get("license_number") else None,
        offers=[str(x)[:80] for x in (info.get("offers") or []) if x][:4],
        address=b.address, keyword=keyword, niche=niche, theme=THEMES.get(niche, "indigo"),
        current_site=None if no_site else b.website, current_score=b.website_score,
        current_speed=((website or {}).get("pagespeed") or {}).get("performance"),
        current_screenshot=b.screenshot_path, improvements=improvements(website, no_site),
    )


def _clean(text: str) -> str:
    """Drop sentences that make claims we cannot verify."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept = [s for s in sentences if not RISKY.search(s)]
    return " ".join(kept).strip()


def faq_items(f: Facts, where: str) -> list[dict[str, str]]:
    offers = " ".join(f.offers).lower()
    items = [{"q": "Which areas do you serve?", "a": f"We work across {', '.join(f.service_area[:6]) or where}."}]
    if f.services:
        items.append({"q": "What services do you offer?", "a": ", ".join(f.services[:8]) + "."})
    items.append({"q": "How do I get a quote?", "a": "Call us or use the quote form and we'll get back to you quickly."})
    if "free" in offers and "quote" in offers:
        items.append({"q": "Are quotes free?", "a": "Yes — quotes are free, with no obligation."})
    if "same" in offers and "day" in offers:
        items.append({"q": "Can you come the same day?", "a": "Often, yes — call us early and we'll do our best."})
    if f.founded_year:
        items.append({"q": "How long have you been in business?", "a": f"We've been serving the area since {f.founded_year}."})
    items.append({"q": "When are you open?", "a": "See our hours on this page — or call and we'll get back to you."})
    return items[:6]


def template_copy(f: Facts) -> Copy:
    where = f"{f.city}, {f.state}" if f.city else "your area"
    town = f.city or "the area"
    blurbs = ["{s} for homes and businesses across {town} — call for a clear quote.",
              "Fast, tidy {sl} with a clear price before we start.",
              "Book {sl} at a time that suits you — we keep you updated.",
              "Need {sl} in {town}? Tell us what you need and we'll take it from there.",
              "{s} done properly by a local team that answers the phone.",
              "From small jobs to big ones — {sl} across {town} and nearby."]
    services = [{"name": s, "blurb": blurbs[i % len(blurbs)].format(s=s, sl=s.lower(), town=town)}
                for i, s in enumerate((f.services or f.services_hint or [f.category])[:6])]
    rating_line = (f"Rated {f.rating:.1f} stars by {f.review_count} customers on Google."
                   if f.rating and f.review_count else "Trusted by local customers.")
    return Copy(
        headline=f"{f.category} in {where}",
        subheadline=f"{f.name} helps homes and businesses in {town} with {f.category.lower()}. {rating_line}",
        about=(f"{f.name} serves {', '.join(f.service_area[:4]) or where}. Call or request a quote and we'll "
               "get back to you quickly with a clear plan and price."),
        services=services,
        why_us=["Quick response times", "Clear, upfront quotes", "Friendly local team",
                f"{f.review_count} Google reviews" if f.review_count else "Real customer reviews"],
        faq=faq_items(f, where),
        cta="Get a Free Quote",
        source="template",
    )


PROMPT = """Write homepage copy for a modern one-page website for this local service business.
FACTS (the only facts you may use):
{facts}

Rules:
- Use ONLY the facts above. Do not invent years in business, licences, insurance, awards, guarantees,
  prices, discounts, staff names, or reviews.
- Friendly, confident, plain American English. Short sentences. No hype words like "best" or "#1".
- Services: if "services" is in the facts, use exactly those (up to 6); otherwise 3-6 items based on the business
  category / categories. Each with a one-sentence benefit.
- If "keyword" is given, use it naturally in the headline together with the city.
Return JSON with exactly these keys:
{{"headline": "<max 8 words>", "subheadline": "<max 25 words>", "about": "<2-3 sentences>",
 "services": [{{"name": "...", "blurb": "..."}}], "why_us": ["<4 short points>"],
 "faq": [{{"q": "...", "a": "..."}}, ... 4 items], "cta": "<2-4 words>"}}"""


async def ai_copy(llm: LLM, f: Facts) -> Copy:
    facts = {k: v for k, v in f.as_dict().items() if k not in (
        "reviews", "phone_href", "current_site", "current_score", "current_speed", "current_screenshot",
        "improvements", "theme", "founded_year", "license", "offers")}
    data = await llm.complete_json(PROMPT.format(facts=facts), max_tokens=2500)
    fallback = template_copy(f)

    def text(key: str, default: str, limit: int) -> str:
        value = _clean(str(data.get(key) or ""))[:limit]
        return value or default

    services = [{"name": str(s.get("name", ""))[:60], "blurb": _clean(str(s.get("blurb", "")))[:200]}
                for s in (data.get("services") or []) if isinstance(s, dict) and s.get("name")][:6]
    faq = [{"q": str(q.get("q", ""))[:140], "a": _clean(str(q.get("a", "")))[:400]}
           for q in (data.get("faq") or []) if isinstance(q, dict) and q.get("q") and _clean(str(q.get("a", "")))][:5]
    why = [_clean(str(w))[:80] for w in (data.get("why_us") or []) if _clean(str(w))][:5]
    return Copy(
        headline=text("headline", fallback.headline, 90),
        subheadline=text("subheadline", fallback.subheadline, 220),
        about=text("about", fallback.about, 600),
        services=services or fallback.services,
        why_us=why or fallback.why_us,
        faq=faq or fallback.faq,
        cta=text("cta", fallback.cta, 30),
        source="ai",
    )


async def make_copy(f: Facts, llm: LLM | None) -> Copy:
    if llm is None:
        return template_copy(f)
    try:
        return await ai_copy(llm, f)
    except (LLMError, ValueError, TypeError) as exc:
        log.warning("AI copy failed, using template copy", extra={"data": {"name": f.name, "error": str(exc)[:150]}})
        return template_copy(f)
