"""ZIP prioritisation: which ZIPs to scan first for a keyword (Census population, offline)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from leadengine.geo.zipdata import ZipDirectory, ZipInfo


@dataclass
class ZipPick:
    info: ZipInfo
    score: float
    distance_km: float | None
    scanned: bool
    why: str


def prioritise_zips(directory: ZipDirectory, *, state: str | None = None, near_zip: str | None = None,
                    radius_km: float = 40.0, scanned: set[str] | None = None, top: int = 20,
                    min_population: int = 2000) -> list[ZipPick]:
    """Rank ZIPs by population (log scale) with a bonus for not-yet-scanned ZIPs and a small
    preference for ZIPs close to ``near_zip``. Tiny / PO-box ZIPs are skipped."""
    scanned = scanned or set()
    if near_zip:
        center = directory.get(near_zip)
        if center is None:
            raise ValueError(f"unknown ZIP {near_zip}")
        pool = directory.nearby(center.lat, center.lng, radius_km)
    else:
        pool = [(z, None) for z in directory._rows.values()]
    picks: list[ZipPick] = []
    for z, dist in pool:
        if state and z.state != state.upper():
            continue
        pop = z.population or 0
        if pop < min_population:
            continue
        score = math.log10(pop)
        why = [f"pop {pop:,}"]
        if z.median_household_income:
            score += 0.3 * min(1.0, z.median_household_income / 120_000)
            why.append(f"income ${z.median_household_income:,}")
        if dist is not None:
            score -= 0.2 * dist / max(radius_km, 1)
        done = z.zip in scanned
        if done:
            score -= 2
            why.append("already scanned")
        picks.append(ZipPick(z, round(score, 3), round(dist, 1) if dist is not None else None, done, ", ".join(why)))
    return sorted(picks, key=lambda p: -p.score)[:top]
