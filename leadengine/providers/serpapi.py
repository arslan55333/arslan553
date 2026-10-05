"""SerpAPI Google Maps engine (paid, 1 credit per page of 20 results)."""

from __future__ import annotations

from typing import Any

from leadengine.errors import ProviderAuthError, ProviderError
from leadengine.models import BusinessRecord, SearchQuery, to_float, to_int
from leadengine.providers.base import Provider, ProviderResult

SEARCH_URL = "https://serpapi.com/search.json"
ACCOUNT_URL = "https://serpapi.com/account.json"
PAGE_SIZE = 20
MAX_START = 100  # Google Maps stops at ~120 results per query
_NO_RESULTS = "hasn't returned any results"


class SerpApiProvider(Provider):
    name = "serpapi"
    label = "SerpAPI (Google Maps)"
    paid = True
    wants_coordinates = True

    def configured(self) -> tuple[bool, str]:
        if not self.settings.serpapi_api_key:
            return False, "SERPAPI_API_KEY missing in .env"
        return True, "ready"

    def _params(self, query: SearchQuery, start: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "engine": "google_maps",
            "type": "search",
            # inside a grid cell the map position defines the area, so search the keyword only
            "q": query.keyword if query.zoom else f"{query.keyword} {query.location_text()}".strip(),
            "hl": "en",
            "gl": "us",
            "start": start,
            "api_key": self.settings.serpapi_api_key,
        }
        if query.has_coordinates:
            params["ll"] = f"@{query.lat},{query.lng},{query.zoom or 13}z"
        return params

    async def search(self, query: SearchQuery) -> ProviderResult:
        records: list[BusinessRecord] = []
        calls = 0
        start = 0
        exhausted = False
        while len(records) < query.max_results:
            response = await self.http.request("GET", SEARCH_URL, params=self._params(query, start))
            calls += 1
            data = self._json(response)
            error = data.get("error") if isinstance(data, dict) else None
            if error:
                if _NO_RESULTS in error:
                    self._record("google_maps", note="no results")
                    exhausted = True
                    break
                self._record("google_maps", success=False, note=error[:200])
                if response.status_code in (401, 403) or "api key" in error.lower():
                    raise ProviderAuthError(self.name, error)
                raise ProviderError(self.name, error)
            self._record("google_maps")

            page = data.get("local_results") or []
            if not page and data.get("place_results"):
                page = [data["place_results"]]  # query matched one exact place
            for item in page:
                rec = self.to_record(item, rank=len(records) + 1)
                if rec is not None:
                    records.append(rec)
            if len(page) < PAGE_SIZE or start >= MAX_START:
                exhausted = True
                break
            start += PAGE_SIZE
        return ProviderResult(records[: query.max_results], exhausted, calls)

    def to_record(self, item: dict[str, Any], rank: int) -> BusinessRecord | None:
        name = (item.get("title") or "").strip()
        if not name:
            return None
        gps = item.get("gps_coordinates") or {}
        categories = item.get("types") or ([item["type"]] if item.get("type") else [])
        place_id = item.get("place_id")
        claimed = None
        if "unclaimed_listing" in item:
            claimed = not bool(item["unclaimed_listing"])
        return BusinessRecord(
            name=name,
            provider=self.name,
            provider_id=item.get("data_id") or place_id,
            place_id=place_id,
            data_id=item.get("data_id"),
            phone=item.get("phone"),
            website=item.get("website"),
            address=item.get("address"),
            lat=to_float(gps.get("latitude")),
            lng=to_float(gps.get("longitude")),
            rating=to_float(item.get("rating")),
            review_count=to_int(item.get("reviews")),
            categories=[c for c in categories if c],
            hours=item.get("operating_hours") or item.get("hours"),
            google_maps_url=(
                f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else None
            ),
            business_status=item.get("open_state"),
            claimed=claimed,
            rank=rank,
            raw=item,
        )

    async def place(self, *, place_id: str | None = None, data_id: str | None = None) -> BusinessRecord | None:
        """Look up one business (1 credit). Used to fill gaps for shortlisted leads only."""
        params: dict[str, Any] = {
            "engine": "google_maps", "type": "place", "hl": "en", "gl": "us",
            "api_key": self.settings.serpapi_api_key,
        }
        if place_id:
            params["place_id"] = place_id
        elif data_id and ":" in data_id:
            params["data_cid"] = str(int(data_id.split(":")[1], 16))  # CID = 2nd half of the feature id
        else:
            raise ProviderError(self.name, "place lookup needs a place_id or data_id")
        response = await self.http.request("GET", SEARCH_URL, params=params)
        data = self._json(response)
        if error := data.get("error"):
            self._record("google_maps_place", success=False, note=error[:200])
            if response.status_code in (401, 403) or "api key" in error.lower():
                raise ProviderAuthError(self.name, error)
            return None
        self._record("google_maps_place")
        item = data.get("place_results")
        return self.to_record(item, rank=1) if isinstance(item, dict) else None

    async def account(self) -> dict[str, Any]:
        """Live plan / remaining searches. This endpoint is free (uses no credits)."""
        response = await self.http.request("GET", ACCOUNT_URL, params={"api_key": self.settings.serpapi_api_key})
        data = self._json(response)
        if response.status_code >= 400 or data.get("error"):
            raise ProviderError(self.name, data.get("error", f"HTTP {response.status_code}"))
        return data

