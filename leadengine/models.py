"""Provider-neutral data objects passed between providers and storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SearchQuery:
    """What to look for and where. Give a ZIP, a free-text location, or both."""

    keyword: str
    zip_code: str | None = None
    location: str | None = None
    lat: float | None = None
    lng: float | None = None
    radius_m: int = 8000
    max_results: int = 20
    zoom: int | None = None                                          # map zoom for this search cell
    bounds: tuple[float, float, float, float] | None = None          # (south, west, north, east)

    def location_text(self) -> str:
        """Human-readable place used inside search queries."""
        parts = [p for p in (self.location, self.zip_code) if p]
        return " ".join(parts)

    @property
    def has_coordinates(self) -> bool:
        return self.lat is not None and self.lng is not None


@dataclass
class BusinessRecord:
    """One business as returned by a provider, before it is merged into the DB."""

    name: str
    provider: str
    provider_id: str | None = None
    place_id: str | None = None
    data_id: str | None = None          # Google feature id "0x..:0x.." (CID is the 2nd part)
    phone: str | None = None
    website: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    lat: float | None = None
    lng: float | None = None
    rating: float | None = None
    review_count: int | None = None
    categories: list[str] = field(default_factory=list)
    hours: Any = None
    google_maps_url: str | None = None
    business_status: str | None = None
    claimed: bool | None = None
    photo_count: int | None = None
    last_review_at: datetime | None = None
    recent_review_dates: list[str] | None = None
    owner_response_rate: float | None = None
    sponsored: bool = False              # shown as an ad ("Sponsored") in the results
    rank: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def to_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
