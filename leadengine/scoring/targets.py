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
