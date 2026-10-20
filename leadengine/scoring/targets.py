"""'My targets': the owner's niche — businesses paying for Google Ads whose website is missing or weak."""

from __future__ import annotations

NO_REAL_SITE = {"no_website", "facebook_only", "social_or_directory_only", "broken", "parked", "server_default_page"}


def is_target(b, weak: int = 50) -> bool:
    """Pays for Google Ads (or LSA) but has no real website, or a weak site / landing page / local SEO,
    and isn't a national chain."""
    if b.lead_label == "Skip" and "chain" in (b.lead_reason or ""):
        return False
    if not (b.ads_status in ("Active", "Likely") or b.lsa):
        return False
    if not b.website or set(b.website_flags or []) & NO_REAL_SITE:
        return True
    return any(v is not None and v < weak for v in (b.website_score, getattr(b, "landing_score", None),
                                                     getattr(b, "seo_score", None)))


def is_new_business(b, website: dict | None = None, *, max_reviews: int = 15, this_year: int | None = None,
                    domain: dict | None = None) -> bool:
    """A young business: few Google reviews AND (no website, or a website first seen in the last ~year).
    New owners are the most likely to say yes to a first proper website."""
    from datetime import date

    if (b.review_count or 0) > max_reviews:
        return False
    if b.lead_label == "Skip" and "chain" in (b.lead_reason or ""):
        return False
    if not b.website or set(b.website_flags or []) & NO_REAL_SITE:
        return True
    first = ((website or {}).get("wayback") or {}).get("first_year")
    year = this_year or date.today().year
    registered = (domain or {}).get("registered")          # RDAP: the domain was bought less than a year ago
    if registered and int(registered[:4]) >= year - 1 and (domain or {}).get("status") not in ("for_sale",):
        if (date.today() - date.fromisoformat(registered[:10])).days <= 365:
            return True
    return first is not None and first >= year - 1
