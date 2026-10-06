"""Combine ad signals into ``ads_status`` = Active / Likely / Past / None and ``lsa`` yes/no."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from leadengine.enrich.ads.serp import SerpAd
from leadengine.enrich.ads.site_tags import SiteAdSignals
from leadengine.normalize import normalize_domain


@dataclass
class AdsVerdict:
    status: str                    # Active | Likely | Past | None
    lsa: bool
    confidence: int                # 0-100
    evidence: list[str] = field(default_factory=list)
    meta_ads: bool = False         # Meta (Facebook/Instagram) pixel -> likely runs Meta ads
    google_ads_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_transparency(data: dict[str, Any], domain: str | None, today: date) -> dict[str, Any] | None:
    """SerpAPI google_ads_transparency_center response -> {last_shown, first_shown, creatives}."""
    creatives = data.get("ad_creatives") or []
    if domain:
        own = [c for c in creatives if normalize_domain(c.get("target_domain") or "") in (domain, None)]
        creatives = own or creatives

    def to_date(v) -> date | None:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc).date()
        if isinstance(v, str) and len(v) >= 10:
            try:
                return date.fromisoformat(v[:10])
            except ValueError:
                return None
        return None

    lasts = [d for d in (to_date(c.get("last_shown")) for c in creatives) if d]
    firsts = [d for d in (to_date(c.get("first_shown")) for c in creatives) if d]
    if not creatives:
        return {"creatives": 0, "last_shown": None, "first_shown": None}
    return {"creatives": len(creatives), "last_shown": max(lasts).isoformat() if lasts else None,
            "first_shown": min(firsts).isoformat() if firsts else None,
            "advertiser": creatives[0].get("advertiser")}


def decide(
    *,
    serp_hits: list[SerpAd],
    serp_checked: bool,
    site: SiteAdSignals | None,
    maps_sponsored: bool,
    transparency: dict[str, Any] | None,
    today: date,
) -> AdsVerdict:
    evidence: list[str] = []
    lsa_hits = [a for a in serp_hits if a.kind == "lsa"]
    search_hits = [a for a in serp_hits if a.kind == "search"]
    status, confidence = "None", 20 if serp_checked else 10

    if search_hits:
        a = search_hits[0]
        evidence.append(f"Google search ad live now (position {a.position}: \"{a.title[:60]}\")")
    if lsa_hits:
        a = lsa_hits[0]
        evidence.append(f"Local Services Ad live now" + (f" ({a.badge})" if a.badge else ""))
    if maps_sponsored:
        evidence.append("shown as a Sponsored listing on Google Maps")
    recent_transparency = False
    if transparency and transparency.get("last_shown"):
        days = (today - date.fromisoformat(transparency["last_shown"])).days
        recent_transparency = days <= 30
        evidence.append(f"Ads Transparency Center: {transparency['creatives']} ads, last shown "
                        f"{transparency['last_shown']} ({days} days ago)")

    if search_hits or lsa_hits or maps_sponsored or recent_transparency:
        status, confidence = "Active", 95 if (search_hits or lsa_hits) else 85
    elif transparency and transparency.get("last_shown"):
        status, confidence = "Past", 80

    if site is not None:
        if site.google_ads_ids:
            evidence.append("Google Ads tag on website: " + ", ".join(site.google_ads_ids[:3])
                            + (" (inside Google Tag Manager)" if site.ads_in_gtm else ""))
        elif site.conversion_tag:
            evidence.append("Google Ads conversion tracking code on website")
        if site.remarketing_tag:
            evidence.append("Google remarketing tag on website")
        if site.call_tracking:
            evidence.append("call tracking: " + ", ".join(site.call_tracking))
        if site.meta_pixel:
            evidence.append("Meta (Facebook/Instagram) pixel on website")
        if status == "None":
            if site.google_ads_evidence and not any("Google Ads" in e or "remarketing" in e for e in evidence):
                evidence.append("Google Ads tracking on website (gclid / ads scripts)")
            if site.google_ads_evidence:
                status, confidence = "Likely", 70 if site.google_ads_ids else 60
            elif site.call_tracking and site.gclid_handling:
                status, confidence = "Likely", 55
            elif site.call_tracking:
                status, confidence = "Likely", 40

    return AdsVerdict(
        status=status,
        lsa=bool(lsa_hits),
        confidence=confidence,
        evidence=evidence,
        meta_ads=bool(site and site.meta_pixel),
        google_ads_ids=(site.google_ads_ids if site else []),
    )
