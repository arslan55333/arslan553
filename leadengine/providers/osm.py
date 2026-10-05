"""OpenStreetMap Overpass (free). Low coverage for US service businesses, no ratings,
but useful as a zero-cost discovery source."""

from __future__ import annotations

from typing import Any

from leadengine.errors import ProviderError
from leadengine.models import BusinessRecord, SearchQuery, to_float
from leadengine.providers.base import Provider, ProviderResult

DEFAULT_ENDPOINT = "https://overpass-api.de/api/interpreter"
_REGEX_SPECIAL = set(r".^$*+?()[]{}|\\")


def _escape(text: str) -> str:
    """Escape for a POSIX regex inside an Overpass QL double-quoted string."""
    out = "".join("\\\\" + ch if ch in _REGEX_SPECIAL else ch for ch in text)
    return out.replace('"', "")


def build_query(keyword: str, lat: float, lng: float, radius_m: int, limit: int) -> str:
    phrase = _escape(" ".join(keyword.lower().split()))
    tag_value = phrase.replace(" ", "_")
    around = f"(around:{int(radius_m)},{lat},{lng})"
    return (
        "[out:json][timeout:25];\n(\n"
        f'  nwr{around}["name"~"{phrase}",i];\n'
        f'  nwr{around}["shop"~"{tag_value}",i];\n'
        f'  nwr{around}["craft"~"{tag_value}",i];\n'
        f'  nwr{around}["office"~"{tag_value}",i];\n'
        f'  nwr{around}["amenity"~"{tag_value}",i];\n'
        ");\n"
        f"out center tags {int(limit)};"
    )


class OsmProvider(Provider):
    name = "osm"
    label = "OpenStreetMap Overpass (free)"
    needs_coordinates = True

    @property
    def endpoint(self) -> str:
        return self.config.extra.get("endpoint", DEFAULT_ENDPOINT)

    async def search(self, query: SearchQuery) -> ProviderResult:
        if not query.has_coordinates:
            raise ProviderError(self.name, "needs coordinates (ZIP could not be geocoded)")
        ql = build_query(query.keyword, query.lat, query.lng, query.radius_m, query.max_results)
        response = await self.http.request("POST", self.endpoint, data={"data": ql})
        if response.status_code >= 400:
            self._record("interpreter", success=False, note=f"HTTP {response.status_code}")
            raise ProviderError(self.name, f"Overpass returned HTTP {response.status_code}")
        data = self._json(response)
        self._record("interpreter")

        records: list[BusinessRecord] = []
        for el in data.get("elements") or []:
            rec = self.to_record(el, rank=len(records) + 1)
            if rec is not None:
                records.append(rec)
        return ProviderResult(records[: query.max_results], True, 1)

    def to_record(self, el: dict[str, Any], rank: int) -> BusinessRecord | None:
        tags = el.get("tags") or {}
        name = (tags.get("name") or "").strip()
        if not name:
            return None
        center = el.get("center") or {}
        street = " ".join(p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p)
        city, state, postcode = tags.get("addr:city"), tags.get("addr:state"), tags.get("addr:postcode")
        address = ", ".join(p for p in (street, city, f"{state or ''} {postcode or ''}".strip()) if p) or None
        categories = [f"{k}={tags[k]}" for k in ("shop", "craft", "office", "amenity") if tags.get(k)]
        return BusinessRecord(
            name=name,
            provider=self.name,
            provider_id=f"osm:{el.get('type')}/{el.get('id')}",
            phone=tags.get("phone") or tags.get("contact:phone"),
            website=tags.get("website") or tags.get("contact:website"),
            address=address,
            city=city,
            state=state,
            zip_code=(postcode or "")[:5] or None,
            lat=to_float(el.get("lat", center.get("lat"))),
            lng=to_float(el.get("lon", center.get("lon"))),
            categories=categories,
            hours=tags.get("opening_hours"),
            rank=rank,
            raw=el,
        )
