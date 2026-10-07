"""Area explorer: State → County → Town/City → ZIP, with wealth and scan coverage.

Everything comes from the bundled ZIP directory (no network, no key):
* towns are the ZIP's primary city (Google and Google Ads target by city/town);
* villages and neighbourhoods *inside* a ZIP come from OpenStreetMap on demand (``suggest.areas_for_zip``);
* "wealth" is a 0–100 national percentile of median household income and home value, so you can start with the
  areas whose owners spend more (roofing, remodeling, landscaping …);
* coverage = which ZIPs were already scanned for a keyword, when, and how many businesses / targets they gave.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
from typing import Any, Iterable

from leadengine.geo.zipdata import ZipInfo, zip_directory

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico",
}


@lru_cache(maxsize=1)
def _percentiles() -> tuple[list[int], list[int]]:
    rows = list(zip_directory().all())
    inc = sorted(z.median_household_income for z in rows if z.median_household_income)
    home = sorted(z.median_home_value for z in rows if z.median_home_value)
    return inc, home


def _pct(sorted_vals: list[int], v: int | None) -> float | None:
    if not v or not sorted_vals:
        return None
    return 100 * bisect.bisect_left(sorted_vals, v) / len(sorted_vals)


def wealth_score(income: int | None, home_value: int | None) -> int | None:
    """0–100: national percentile of income and home value (average of the two that are known)."""
    inc, home = _percentiles()
    parts = [p for p in (_pct(inc, income), _pct(home, home_value)) if p is not None]
    return round(sum(parts) / len(parts)) if parts else None


def wealth_label(score: int | None) -> str:
    if score is None:
        return ""
    return "$$$$" if score >= 85 else "$$$" if score >= 65 else "$$" if score >= 35 else "$"


def _weighted(zs: list[ZipInfo], attr: str) -> int | None:
    pairs = [(getattr(z, attr), z.population or 1) for z in zs if getattr(z, attr)]
    if not pairs:
        return None
    return round(sum(v * w for v, w in pairs) / sum(w for _, w in pairs))


def _summary(name: str, zs: list[ZipInfo], **extra: Any) -> dict[str, Any]:
    income, home = _weighted(zs, "median_household_income"), _weighted(zs, "median_home_value")
    w = wealth_score(income, home)
    zs = sorted(zs, key=lambda z: -(z.population or 0))
    return {"name": name, "zips": [z.zip for z in zs], "population": sum(z.population or 0 for z in zs),
            "income": income, "home_value": home, "wealth": w, "wealth_label": wealth_label(w),
            "lat": round(sum(z.lat for z in zs) / len(zs), 5), "lng": round(sum(z.lng for z in zs) / len(zs), 5),
            **extra}


def states() -> list[dict[str, Any]]:
    by: dict[str, list[ZipInfo]] = defaultdict(list)
    for z in zip_directory().all():
        by[z.state].append(z)
    return sorted((_summary(US_STATES.get(st, st), zs, code=st, counties=len({z.county for z in zs if z.county}))
                   for st, zs in by.items()), key=lambda s: s["name"])


def counties(state: str) -> list[dict[str, Any]]:
    by: dict[str, list[ZipInfo]] = defaultdict(list)
    for z in zip_directory().all():
        if z.state == state.upper():
            by[z.county or "(no county)"].append(z)
    return sorted((_summary(c, zs, towns=len({z.city for z in zs})) for c, zs in by.items()),
                  key=lambda c: -c["population"])


def towns(state: str, county: str | None = None) -> list[dict[str, Any]]:
    """Every town/city in a county (or the whole state), with its ZIPs."""
    by: dict[str, list[ZipInfo]] = defaultdict(list)
    for z in zip_directory().all():
        if z.state == state.upper() and (county is None or (z.county or "(no county)") == county):
            by[z.city].append(z)
    return sorted((_summary(t, zs, county=zs[0].county, state=state.upper()) for t, zs in by.items()),
                  key=lambda t: -t["population"])


def zip_rows(zips: Iterable[str]) -> list[dict[str, Any]]:
    d = zip_directory()
    out = []
    for code in zips:
        z = d.get(code)
        if z is None:
            continue
        w = wealth_score(z.median_household_income, z.median_home_value)
        out.append({"zip": z.zip, "city": z.city, "state": z.state, "county": z.county, "population": z.population,
                    "income": z.median_household_income, "home_value": z.median_home_value, "wealth": w,
                    "wealth_label": wealth_label(w), "lat": z.lat, "lng": z.lng})
    return out


# ── coverage ─────────────────────────────────────────────────────────
def coverage(session, keyword: str | None, zips: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
    """ZIP -> {"ran_at", "found", "businesses", "targets"} for scans of ``keyword`` (any keyword when None)."""
    from sqlalchemy import func, select

    from leadengine.db.models import Business, Search
    from leadengine.normalize import normalize_keyword
    from leadengine.scoring.targets import is_target

    zips = set(zips) if zips is not None else None
    stmt = select(Search.zip_code, func.max(Search.ran_at), func.sum(Search.result_count)).where(
        Search.zip_code.is_not(None), Search.result_count > 0).group_by(Search.zip_code)
    if keyword:
        stmt = stmt.where(Search.keyword_norm == normalize_keyword(keyword))
    out: dict[str, dict[str, Any]] = {}
    for z, ran, found in session.execute(stmt):
        if zips is None or z in zips:
            out[z] = {"ran_at": ran, "found": int(found or 0), "businesses": 0, "targets": 0}
    if out:
        for b in session.scalars(select(Business).where(Business.zip_code.in_(list(out)))):
            row = out[b.zip_code]
            row["businesses"] += 1
            row["targets"] += is_target(b)
    return out


def days_ago(ts: datetime | None, now: datetime | None = None) -> int | None:
    if ts is None:
        return None
    from leadengine.db.models import utcnow
    return max(0, ((now or utcnow()) - ts).days)


def with_coverage(items: list[dict[str, Any]], cov: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Adds scanned / total ZIPs, businesses and targets to towns or counties."""
    for it in items:
        hit = [cov[z] for z in it["zips"] if z in cov]
        it["scanned"] = len(hit)
        it["coverage"] = round(100 * len(hit) / len(it["zips"])) if it["zips"] else 0
        it["businesses"] = sum(h["businesses"] for h in hit)
        it["targets"] = sum(h["targets"] for h in hit)
        it["last_scan_days"] = min((days_ago(h["ran_at"]) for h in hit), default=None)
    return items
