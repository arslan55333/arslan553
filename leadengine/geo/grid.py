"""Search grid over an area, with adaptive splitting of saturated cells.

Google Maps stops at ~120 results per search. In a dense area a single search
misses businesses, so the area is cut into cells; any cell that still returns a
full page is split into 4 smaller cells and searched again.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

KM_PER_DEG_LAT = 110.574
MIN_ZOOM, MAX_ZOOM = 11, 18


def km_per_deg_lng(lat: float) -> float:
    return 111.320 * math.cos(math.radians(lat))


def zoom_for_span(lat: float, span_km: float, viewport_px: int = 1000) -> int:
    """Map zoom at which ``span_km`` fills roughly ``viewport_px`` pixels."""
    meters_per_px = max(span_km, 0.05) * 1000 / viewport_px
    z = math.log2(156_543.03 * math.cos(math.radians(lat)) / meters_per_px)
    return int(min(MAX_ZOOM, max(MIN_ZOOM, round(z))))


@dataclass(frozen=True)
class Cell:
    lat: float
    lng: float
    half_km: float          # half the side length of the square cell
    depth: int = 0

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(south, west, north, east)"""
        dlat = self.half_km / KM_PER_DEG_LAT
        dlng = self.half_km / km_per_deg_lng(self.lat)
        return (self.lat - dlat, self.lng - dlng, self.lat + dlat, self.lng + dlng)

    @property
    def zoom(self) -> int:
        return zoom_for_span(self.lat, 2 * self.half_km)

    @property
    def radius_m(self) -> int:
        """Radius of the circle that encloses the cell."""
        return int(self.half_km * 1000 * math.sqrt(2))

    def split(self) -> list["Cell"]:
        q = self.half_km / 2
        dlat = q / KM_PER_DEG_LAT
        dlng = q / km_per_deg_lng(self.lat)
        return [
            Cell(self.lat + sy * dlat, self.lng + sx * dlng, q, self.depth + 1)
            for sy in (-1, 1) for sx in (-1, 1)
        ]

    def key(self) -> str:
        return f"{self.lat:.5f},{self.lng:.5f},{self.half_km:.3f}"


def plan_cells(lat: float, lng: float, radius_km: float, cell_km: float) -> list[Cell]:
    """Square cells of about ``cell_km`` covering a circle of ``radius_km``."""
    n = max(1, math.ceil(2 * radius_km / max(cell_km, 0.2)))
    side = 2 * radius_km / n
    half = side / 2
    dlat_step = side / KM_PER_DEG_LAT
    south = lat - radius_km / KM_PER_DEG_LAT
    reach = radius_km + half * math.sqrt(2)   # keep cells that touch the circle
    cells = []
    for row in range(n):
        c_lat = south + (row + 0.5) * dlat_step
        # longitude degrees per km depend on latitude; use the row's own latitude so
        # neighbouring cells share edges exactly (Cell.bounds uses the same scale)
        dlng_step = side / km_per_deg_lng(c_lat)
        west = lng - radius_km / km_per_deg_lng(c_lat)
        for col in range(n):
            c_lng = west + (col + 0.5) * dlng_step
            dy = (c_lat - lat) * KM_PER_DEG_LAT
            dx = (c_lng - lng) * km_per_deg_lng(lat)
            if math.hypot(dx, dy) <= reach:
                cells.append(Cell(c_lat, c_lng, half))
    # nearest to the centre first, so a budget cut keeps the core of the ZIP
    return sorted(cells, key=lambda c: math.hypot(c.lat - lat, c.lng - lng))
