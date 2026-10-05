"""Advertising fingerprints in a website's own code (free).

Google Ads needs a conversion / remarketing tag on the advertiser's site to track
calls and leads, so ``AW-123456789`` in the page (or inside the site's Google Tag
Manager container) is strong evidence the business runs - or has run - Google Ads.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from leadengine.http import HttpClient
from leadengine.log import get_logger

log = get_logger("ads.site")

GTM_URL = "https://www.googletagmanager.com/gtm.js"
AW_RE = re.compile(r"\bAW-(\d{6,12})\b")
GTM_RE = re.compile(r"\bGTM-[A-Z0-9]{4,10}\b")
CONVERSION_RE = re.compile(r"googleadservices\.com/pagead/conversion(?:_async)?\.js|google_conversion_id\s*[=:]\s*\d+|"
                           r"gtag\(\s*['\"]event['\"]\s*,\s*['\"]conversion['\"]", re.I)
REMARKETING_RE = re.compile(r"googleads\.g\.doubleclick\.net/pagead/viewthroughconversion|google_remarketing_only", re.I)
GCLID_RE = re.compile(r"\bgclid\b|_gcl_aw|gclsrc", re.I)
META_PIXEL_RE = re.compile(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d{10,20})['\"]|connect\.facebook\.net/[\w_]+/fbevents\.js", re.I)
BING_RE = re.compile(r"bat\.bing\.com/bat\.js|\bti\s*:\s*['\"]?\d{6,}", re.I)
CALL_TRACKING = {
    "CallRail": re.compile(r"cdn\.callrail\.com|callrail\.com/companies", re.I),
    "CallTrackingMetrics": re.compile(r"tctm\.co|calltrackingmetrics", re.I),
    "WhatConverts": re.compile(r"whatconverts\.com", re.I),
    "Invoca": re.compile(r"invoca\.net|invocacdn", re.I),
    "Marchex": re.compile(r"marchex\.io|voicestar", re.I),
}


@dataclass
class SiteAdSignals:
    google_ads_ids: list[str] = field(default_factory=list)       # AW-... found on the page or in GTM
    conversion_tag: bool = False
    remarketing_tag: bool = False
    gtm_containers: list[str] = field(default_factory=list)
    ads_in_gtm: bool = False
    gclid_handling: bool = False
    call_tracking: list[str] = field(default_factory=list)
    meta_pixel: bool = False
    meta_pixel_ids: list[str] = field(default_factory=list)
    microsoft_ads: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def google_ads_evidence(self) -> bool:
        return bool(self.google_ads_ids or self.conversion_tag or self.remarketing_tag or self.ads_in_gtm)


def scan_html(html: str) -> SiteAdSignals:
    s = SiteAdSignals()
    s.google_ads_ids = sorted({f"AW-{m}" for m in AW_RE.findall(html)})
    s.conversion_tag = bool(CONVERSION_RE.search(html)) or bool(s.google_ads_ids)
    s.remarketing_tag = bool(REMARKETING_RE.search(html))
    s.gtm_containers = sorted(set(GTM_RE.findall(html)))
    s.gclid_handling = bool(GCLID_RE.search(html))
    s.call_tracking = [name for name, rx in CALL_TRACKING.items() if rx.search(html)]
    pixel = META_PIXEL_RE.findall(html)
    s.meta_pixel = bool(META_PIXEL_RE.search(html))
    s.meta_pixel_ids = sorted({p for p in pixel if p})
    s.microsoft_ads = bool(BING_RE.search(html))
    return s


async def scan_gtm(http: HttpClient, signals: SiteAdSignals, max_containers: int = 2) -> SiteAdSignals:
    """Open the site's GTM container(s): Google Ads tags configured in GTM never appear in the HTML."""
    for container in signals.gtm_containers[:max_containers]:
        try:
            r = await http.request("GET", GTM_URL, params={"id": container}, timeout=15, retries=1)
        except Exception as exc:
            log.debug("gtm fetch failed", extra={"data": {"id": container, "error": type(exc).__name__}})
            continue
        if r.status_code != 200:
            continue
        js = r.text
        ids = {f"AW-{m}" for m in AW_RE.findall(js)}
        if ids or "googleadservices" in js or '"vtp_conversionId"' in js or "awct" in js:
            signals.ads_in_gtm = True
            signals.google_ads_ids = sorted(set(signals.google_ads_ids) | ids)
        if REMARKETING_RE.search(js) or '"sp"' in js and "vtp_conversionId" in js:
            signals.remarketing_tag = True
        if META_PIXEL_RE.search(js) or "fbevents.js" in js:
            signals.meta_pixel = True
        signals.call_tracking = sorted(set(signals.call_tracking) | {n for n, rx in CALL_TRACKING.items() if rx.search(js)})
    return signals
