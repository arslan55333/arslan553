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


def gather_facts(b: Business, activity: dict[str, Any] | None) -> Facts:
    phone_n = normalize_phone(b.phone)
    category = (b.categories or ["Local service"])[0]
    return Facts(
        name=b.name, category=category, city=b.city or "", state=b.state or "", phone=b.phone,
        phone_href=f"tel:+1{phone_n}" if phone_n and len(phone_n) == 10 else (f"tel:{phone_n}" if phone_n else None),
        rating=b.rating, review_count=b.review_count, service_area=service_area(b),
        hours=normalize_hours(b.hours), reviews=list((activity or {}).get("top_reviews") or [])[:3],
        owner=b.owner_name, services_hint=[c for c in (b.categories or [])[:6]],
    )


def _clean(text: str) -> str:
    """Drop sentences that make claims we cannot verify."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept = [s for s in sentences if not RISKY.search(s)]
    return " ".join(kept).strip()


def template_copy(f: Facts) -> Copy:
    where = f"{f.city}, {f.state}" if f.city else "your area"
    town = f.city or "the area"
    services = [{"name": s, "blurb": f"{s} for homes and businesses across {town} — call for a clear quote."}
                for s in (f.services_hint or [f.category])[:4]]
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
        faq=[{"q": f"Which areas do you serve?", "a": f"We work across {', '.join(f.service_area[:5]) or where}."},
             {"q": "How do I get a quote?", "a": "Call us or use the request form and we'll reply quickly."},
             {"q": "When are you open?", "a": "See our hours below — call anytime and we'll get back to you."}],
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
- Services: 3-6 items based on the business category / categories, each with a one-sentence benefit.
Return JSON with exactly these keys:
{{"headline": "<max 8 words>", "subheadline": "<max 25 words>", "about": "<2-3 sentences>",
 "services": [{{"name": "...", "blurb": "..."}}], "why_us": ["<4 short points>"],
 "faq": [{{"q": "...", "a": "..."}}, ... 4 items], "cta": "<2-4 words>"}}"""


async def ai_copy(llm: LLM, f: Facts) -> Copy:
    facts = {k: v for k, v in f.as_dict().items() if k not in ("reviews", "phone_href")}
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
