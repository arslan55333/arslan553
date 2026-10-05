"""Ads-first discovery: search many keyword variations in many places and collect who is paying Google.

Every advertiser found is, by definition, spending money on ads right now — the best prospects for a
website / landing-page pitch. One advertiser usually shows up for several searches; we merge them by
domain (or phone / name for Local Services and Places ads).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from leadengine.enrich.ads.serp import SerpAd, SerpSnapshot, name_similarity
from leadengine.normalize import normalize_phone

TEMPLATES = ["{kw}", "{kw} near me", "emergency {kw}", "{kw} cost", "best {kw}", "cheap {kw}",
             "{kw} company", "same day {kw}", "local {kw}", "{kw} services"]


def variations(keyword: str, n: int = 6, extra: list[str] | None = None) -> list[str]:
    """Search phrases real customers use. Google autocomplete ideas (``extra``) first, then templates."""
    kw = re.sub(r"\s+", " ", keyword.strip().lower())
    out: list[str] = [kw]
    for e in extra or []:
        e = re.sub(r"\s+", " ", e.strip().lower())
        if e and kw.split()[0] in e and e not in out and not re.search(r"\b(jobs?|salary|reddit|diy)\b", e):
            out.append(e)
    for t in TEMPLATES:
        v = t.format(kw=kw)
        if v not in out:
            out.append(v)
    return out[:max(1, n)]


@dataclass
class Advertiser:
    key: str
    name: str
    domain: str | None = None
    phone: str | None = None
    kinds: set[str] = field(default_factory=set)        # search | lsa | places
    queries: set[str] = field(default_factory=set)
    locations: set[str] = field(default_factory=set)
    hits: int = 0
    best_position: int | None = None
    titles: list[str] = field(default_factory=list)
    landing_urls: list[str] = field(default_factory=list)
    badge: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("kinds", "queries", "locations"):
            d[k] = sorted(d[k])
        return d


def _key(ad: SerpAd) -> str:
    if ad.domain:
        return "d:" + ad.domain
    if ad.phone and normalize_phone(ad.phone):
        return "p:" + normalize_phone(ad.phone)
    return "n:" + re.sub(r"[^a-z0-9]+", " ", ad.title.lower()).strip()


def aggregate(snapshots: list[SerpSnapshot]) -> list[Advertiser]:
    found: dict[str, Advertiser] = {}
    for snap in snapshots:
        for ad in snap.ads:
            key = _key(ad)
            if key.startswith("n:"):                       # name-only ads (LSA/places): merge with a similar name
                for k, other in found.items():
                    if name_similarity(other.name, ad.title) >= 0.8:
                        key = k
                        break
            a = found.get(key)
            if a is None:
                a = found[key] = Advertiser(key=key, name=ad.title if ad.kind != "search" else (ad.domain or ad.title),
                                            domain=ad.domain, phone=ad.phone)
            a.hits += 1
            a.kinds.add(ad.kind)
            a.queries.add(snap.keyword)
            a.locations.add(snap.location)
            a.phone = a.phone or ad.phone
            a.domain = a.domain or ad.domain
            a.badge = a.badge or ad.badge
            if ad.kind != "search" and (a.name == a.domain or a.name.startswith("d:")):
                a.name = ad.title                           # LSA / places carry the real business name
            if ad.position and (a.best_position is None or ad.position < a.best_position):
                a.best_position = ad.position
            if ad.kind == "search" and ad.title and ad.title not in a.titles and len(a.titles) < 5:
                a.titles.append(ad.title)
            if ad.landing_url and ad.landing_url not in a.landing_urls and len(a.landing_urls) < 5:
                a.landing_urls.append(ad.landing_url)
    return sorted(found.values(), key=lambda a: (-a.hits, a.best_position or 99, a.name))


def evidence_lines(a: Advertiser, total_searches: int) -> list[str]:
    lines = [f"seen in {a.hits} of {total_searches} Google searches"
             + (f" ({', '.join(sorted(a.queries)[:3])}{' …' if len(a.queries) > 3 else ''})" if a.queries else "")]
    if "search" in a.kinds:
        lines.append("Google search ads" + (f", best position {a.best_position}" if a.best_position else ""))
    if "lsa" in a.kinds:
        lines.append("Local Services Ads" + (f" ({a.badge})" if a.badge else ""))
    if "places" in a.kinds:
        lines.append("sponsored listing in Google's map pack")
    return lines
