"""ZIP -> coordinates.

Phase 1 uses OpenStreetMap Nominatim (free, cached for a year in the DB).
Phase 2 switches to the bundled US Census ZCTA Gazetteer (offline, instant).
"""

from __future__ import annotations

from leadengine.config import Settings
from leadengine.credits import CreditTracker
from leadengine.db.repo import Repository
from leadengine.errors import NetworkError
from leadengine.http import HttpClient
from leadengine.log import get_logger

log = get_logger("geo")

DEFAULT_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


async def geocode_zip(
    zip_code: str,
    *,
    http: HttpClient,
    repo: Repository,
    settings: Settings,
    credits: CreditTracker | None = None,
) -> tuple[float, float] | None:
    """Return ``(lat, lng)`` for a US ZIP, or ``None`` if it cannot be found."""
    key = f"zip:{zip_code}"
    cached = repo.get_geo(key, settings.ttl("geocode"))
    if cached is not None:
        return cached.lat, cached.lng

    url = settings.provider("nominatim").extra.get("endpoint", DEFAULT_NOMINATIM_URL)
    params = {"postalcode": zip_code, "country": "United States", "format": "jsonv2", "limit": 1}
    try:
        response = await http.request("GET", url, params=params, headers={"User-Agent": settings.user_agent})
    except NetworkError as exc:
        log.warning("geocode failed", extra={"data": {"zip": zip_code, "error": str(exc)}})
        return None

    ok = response.status_code == 200
    if credits is not None:
        credits.record("nominatim", "search", success=ok, note=None if ok else f"HTTP {response.status_code}")
    if not ok:
        log.warning("geocode HTTP error", extra={"data": {"zip": zip_code, "status": response.status_code}})
        return None
    try:
        hit = response.json()[0]
        lat, lng = float(hit["lat"]), float(hit["lon"])
    except (ValueError, LookupError, TypeError):  # empty list, unexpected shape, bad numbers
        log.warning("geocode: no usable result", extra={"data": {"zip": zip_code}})
        return None
    repo.set_geo(key, lat, lng, hit)
    return lat, lng
