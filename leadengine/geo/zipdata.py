"""Offline US ZIP directory (bundled ``data/us_zips.csv.gz``): centroid, land area,
city, metro, population and income. No network, instant lookups."""

from __future__ import annotations

import csv
import gzip
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "us_zips.csv.gz"
SQMI_TO_KM2 = 2.589988
DEFAULT_RADIUS_KM = 3.0   # when land area is unknown
MIN_RADIUS_KM = 1.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _num(value: str, cast=float):
    try:
        return cast(value) if value not in ("", None) else None
    except ValueError:
        return None


@dataclass(frozen=True)
class ZipInfo:
    zip: str
    lat: float
    lng: float
    land_sqmi: float | None
    city: str
    state: str
    county: str
    metro_area: str
    timezone: str
    population: int | None
    households: int | None
    median_household_income: int | None
    median_home_value: int | None

    @property
    def land_km2(self) -> float | None:
        return self.land_sqmi * SQMI_TO_KM2 if self.land_sqmi else None

    @property
    def radius_km(self) -> float:
        """Radius of a circle with the ZIP's land area (a good search footprint)."""
        if not self.land_km2:
            return DEFAULT_RADIUS_KM
        return max(MIN_RADIUS_KM, math.sqrt(self.land_km2 / math.pi))

    @property
    def label(self) -> str:
        return f"{self.city}, {self.state}"


class ZipDirectory:
    def __init__(self, rows: dict[str, ZipInfo]) -> None:
        self._rows = rows

    @classmethod
    def load(cls, path: Path = DATA_FILE) -> "ZipDirectory":
        rows: dict[str, ZipInfo] = {}
        with gzip.open(path, "rt", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["zip"]] = ZipInfo(
                    zip=r["zip"], lat=float(r["lat"]), lng=float(r["lng"]),
                    land_sqmi=_num(r["land_sqmi"]), city=r["city"], state=r["state"],
                    county=r["county"], metro_area=r["metro_area"], timezone=r["timezone"],
                    population=_num(r["population"], int), households=_num(r["households"], int),
                    median_household_income=_num(r["median_household_income"], int),
                    median_home_value=_num(r["median_home_value"], int),
                )
        return cls(rows)

    def __len__(self) -> int:
        return len(self._rows)

    def all(self):
        return self._rows.values()

    def get(self, zip_code: str) -> ZipInfo | None:
        return self._rows.get(zip_code)

    def nearby(self, lat: float, lng: float, radius_km: float) -> list[tuple[ZipInfo, float]]:
        """ZIPs whose centroid lies within ``radius_km``, nearest first."""
        dlat = radius_km / 110.574
        out = []
        for z in self._rows.values():
            if abs(z.lat - lat) > dlat:
                continue
            d = haversine_km(lat, lng, z.lat, z.lng)
            if d <= radius_km:
                out.append((z, d))
        return sorted(out, key=lambda t: t[1])

    def nearest(self, lat: float, lng: float, max_km: float = 8.0) -> ZipInfo | None:
        """ZIP whose centre is closest to a point (fills missing city/ZIP for scraped businesses)."""
        best = self.nearby(lat, lng, max_km)
        return best[0][0] if best else None

    def by_city(self, city: str, state: str | None = None) -> list[ZipInfo]:
        """ZIPs whose primary city matches (case-insensitive), most populated first."""
        c = city.strip().lower()
        st = (state or "").strip().upper()
        rows = [z for z in self._rows.values() if z.city.lower() == c and (not st or z.state == st)]
        return sorted(rows, key=lambda z: -(z.population or 0))

    def towns(self, zip_code: str, extra_km: float = 0.0) -> list[str]:
        """Town/city names covering a ZIP: its own city first, then cities of ZIPs centred inside it.

        Google Ads are usually targeted by city, so searching "keyword + town" finds
        advertisers a pure ZIP search can miss.
        """
        z = self.get(zip_code)
        if z is None:
            return []
        names = [z.label]
        for other, _ in self.nearby(z.lat, z.lng, z.radius_km + extra_km):
            if other.label not in names and other.state == z.state:
                names.append(other.label)
        return names


@lru_cache(maxsize=1)
def zip_directory() -> ZipDirectory:
    """Shared, lazily loaded directory (~33k rows, loads in well under a second)."""
    return ZipDirectory.load()
