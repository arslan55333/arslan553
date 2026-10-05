"""Google Places API (New) Text Search.

The legacy Places API (used by v3) cannot be enabled on new Google Cloud
projects since March 2025, so this uses ``places.googleapis.com/v1``.
Asking for phone/website/rating makes each page a "Text Search Enterprise" call.
"""

from __future__ import annotations

from typing import Any

from leadengine.errors import ProviderAuthError, ProviderError
from leadengine.models import BusinessRecord, SearchQuery, to_float, to_int
from leadengine.providers.base import Provider, ProviderResult

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PAGE_SIZE = 20
MAX_PAGES = 3  # API returns at most 60 results per query
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.types",
    "places.primaryTypeDisplayName",
    "places.businessStatus",
    "places.googleMapsUri",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.rating",
    "places.userRatingCount",
    "places.regularOpeningHours",
    "nextPageToken",
])


class GooglePlacesProvider(Provider):
    name = "google_places"
    label = "Google Places API (New)"
    paid = True
    wants_coordinates = True

    def configured(self) -> tuple[bool, str]:
        if not self.settings.google_places_api_key:
            return False, "GOOGLE_PLACES_API_KEY missing in .env"
        return True, "ready"

    def _body(self, query: SearchQuery, page_size: int, token: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "textQuery": f"{query.keyword} in {query.location_text()}".strip(),
            "pageSize": page_size,
            "languageCode": "en",
            "regionCode": "US",
        }
        if query.has_coordinates:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": query.lat, "longitude": query.lng},
                    "radius": float(min(query.radius_m, 50_000)),
                }
            }
        if token:
            body["pageToken"] = token
        return body

    async def search(self, query: SearchQuery) -> ProviderResult:
        headers = {
            "X-Goog-Api-Key": self.settings.google_places_api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        }
        records: list[BusinessRecord] = []
        token: str | None = None
        calls = 0
        exhausted = False
        for _ in range(MAX_PAGES):
            page_size = min(PAGE_SIZE, query.max_results - len(records))
            response = await self.http.request(
                "POST", SEARCH_URL, json=self._body(query, page_size, token), headers=headers
            )
            calls += 1
            data = self._json(response)
            if response.status_code >= 400:
                message = (data.get("error") or {}).get("message", f"HTTP {response.status_code}")
                self._record("text_search", success=False, note=message[:200])
                if response.status_code in (401, 403):
                    raise ProviderAuthError(self.name, message)
                raise ProviderError(self.name, message)
            self._record("text_search")

            for place in data.get("places") or []:
                rec = self.to_record(place, rank=len(records) + 1)
                if rec is not None:
                    records.append(rec)
            token = data.get("nextPageToken")
            if not token:
                exhausted = True
                break
            if len(records) >= query.max_results:
                break
        else:
            exhausted = True
        return ProviderResult(records[: query.max_results], exhausted, calls)

    def to_record(self, place: dict[str, Any], rank: int) -> BusinessRecord | None:
        name = ((place.get("displayName") or {}).get("text") or "").strip()
        if not name:
            return None
        loc = place.get("location") or {}
        categories = list(place.get("types") or [])
        primary = (place.get("primaryTypeDisplayName") or {}).get("text")
        if primary:
            categories.insert(0, primary)
        hours = (place.get("regularOpeningHours") or {}).get("weekdayDescriptions")
        return BusinessRecord(
            name=name,
            provider=self.name,
            provider_id=place.get("id"),
            place_id=place.get("id"),
            phone=place.get("nationalPhoneNumber"),
            website=place.get("websiteUri"),
            address=place.get("formattedAddress"),
            lat=to_float(loc.get("latitude")),
            lng=to_float(loc.get("longitude")),
            rating=to_float(place.get("rating")),
            review_count=to_int(place.get("userRatingCount")),
            categories=categories,
            hours=hours,
            google_maps_url=place.get("googleMapsUri"),
            business_status=place.get("businessStatus"),
            rank=rank,
            raw=place,
        )
