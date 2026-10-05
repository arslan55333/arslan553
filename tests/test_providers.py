import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from leadengine.errors import ProviderAuthError, ProviderError
from leadengine.models import SearchQuery
from leadengine.providers.google_places import GooglePlacesProvider
from leadengine.providers.osm import OsmProvider, build_query
from leadengine.providers.selenium_maps import parse_place_url, unwrap_google_redirect
from leadengine.providers.serpapi import SerpApiProvider


def serp_item(i: int) -> dict:
    return {
        "position": i, "title": f"Biz {i}", "place_id": f"ChIJ{i}", "data_id": f"0x{i}:0x{i}",
        "gps_coordinates": {"latitude": 32.78, "longitude": -96.8}, "rating": 4.6, "reviews": "1,234",
        "type": "Dumpster rental service", "address": f"{i} Elm St, Dallas, TX 75201",
        "phone": "(214) 555-0199", "website": f"https://biz{i}.com", "unclaimed_listing": i == 2,
    }


def run(coro):
    return asyncio.run(coro)


# ── SerpAPI ──────────────────────────────────────────────────────────
def test_serpapi_paginates_and_maps_fields(settings, make_http, credits):
    seen_starts = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        start = int(params["start"][0])
        seen_starts.append(start)
        assert params["engine"] == ["google_maps"] and params["api_key"] == ["test-serp-key"]
        assert params["ll"] == ["@32.7,-96.8,13z"]
        count = 20 if start == 0 else 5
        return httpx.Response(200, json={"local_results": [serp_item(start + i + 1) for i in range(count)]})

    async def go():
        async with make_http(handler) as http:
            q = SearchQuery("dumpster rental", zip_code="75201", lat=32.7, lng=-96.8, max_results=40)
            return await SerpApiProvider(settings, http, credits).search(q)

    result = run(go())
    assert seen_starts == [0, 20]
    assert result.api_calls == 2 and result.exhausted
    assert len(result.records) == 25
    first, second = result.records[0], result.records[1]
    assert first.place_id == "ChIJ1" and first.provider_id == "0x1:0x1"
    assert first.review_count == 1234 and first.rating == 4.6
    assert first.categories == ["Dumpster rental service"] and first.claimed is True
    assert second.claimed is False
    assert [r.rank for r in result.records[:3]] == [1, 2, 3]
    assert credits.summary()[0].calls_month == 2


def test_serpapi_stops_at_max_results(settings, make_http):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"local_results": [serp_item(i) for i in range(20)]})

    async def go():
        async with make_http(handler) as http:
            return await SerpApiProvider(settings, http).search(SearchQuery("x", "75201", max_results=10))

    result = run(go())
    assert len(calls) == 1 and len(result.records) == 10 and not result.exhausted


def test_serpapi_no_results_is_not_an_error(settings, make_http):
    def handler(request):
        return httpx.Response(200, json={"error": "Google hasn't returned any results for this query."})

    async def go():
        async with make_http(handler) as http:
            return await SerpApiProvider(settings, http).search(SearchQuery("zzz", "75201"))

    result = run(go())
    assert result.records == [] and result.exhausted


def test_serpapi_bad_key(settings, make_http, credits):
    def handler(request):
        return httpx.Response(401, json={"error": "Invalid API key. Your API key should be here: ..."})

    async def go():
        async with make_http(handler) as http:
            await SerpApiProvider(settings, http, credits).search(SearchQuery("x", "75201"))

    with pytest.raises(ProviderAuthError):
        run(go())
    summary = credits.summary()[0]
    assert summary.calls_month == 0 and summary.failed_month == 1 and summary.est_cost_month_usd == 0


# ── Google Places (New) ──────────────────────────────────────────────
def place(i: int) -> dict:
    return {
        "id": f"ChIJp{i}", "displayName": {"text": f"Place {i}"},
        "formattedAddress": f"{i} Main St, Dallas, TX 75201, USA",
        "location": {"latitude": 32.7, "longitude": -96.8}, "types": ["plumber", "point_of_interest"],
        "primaryTypeDisplayName": {"text": "Plumber"}, "nationalPhoneNumber": "(214) 555-0100",
        "websiteUri": "https://p.com", "rating": 4.9, "userRatingCount": 87,
        "regularOpeningHours": {"weekdayDescriptions": ["Monday: 8 AM - 5 PM"]},
        "googleMapsUri": "https://maps.google.com/?cid=1", "businessStatus": "OPERATIONAL",
    }


def test_google_places_pages_with_token(settings, make_http, credits):
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Goog-Api-Key"] == "test-places-key"
        assert "places.websiteUri" in request.headers["X-Goog-FieldMask"]
        body = json.loads(request.content)
        bodies.append(body)
        if "pageToken" not in body:
            return httpx.Response(200, json={"places": [place(i) for i in range(20)], "nextPageToken": "T2"})
        return httpx.Response(200, json={"places": [place(i) for i in range(20, 23)]})

    async def go():
        async with make_http(handler) as http:
            q = SearchQuery("plumber", zip_code="75201", lat=32.7, lng=-96.8, max_results=60)
            return await GooglePlacesProvider(settings, http, credits).search(q)

    result = run(go())
    assert len(result.records) == 23 and result.api_calls == 2 and result.exhausted
    assert bodies[0]["textQuery"] == "plumber in 75201"
    assert bodies[0]["locationBias"]["circle"]["radius"] == 8000.0
    assert bodies[1]["pageToken"] == "T2"
    r = result.records[0]
    assert r.place_id == "ChIJp0" and r.categories[0] == "Plumber"
    assert r.review_count == 87 and r.hours == ["Monday: 8 AM - 5 PM"]


def test_google_places_permission_error(settings, make_http):
    def handler(request):
        return httpx.Response(403, json={"error": {"message": "Places API (New) has not been used in project"}})

    async def go():
        async with make_http(handler) as http:
            await GooglePlacesProvider(settings, http).search(SearchQuery("x", "75201"))

    with pytest.raises(ProviderAuthError, match="has not been used"):
        run(go())


# ── OpenStreetMap ────────────────────────────────────────────────────
def test_osm_query_and_parse(settings, make_http):
    def handler(request: httpx.Request) -> httpx.Response:
        ql = parse_qs(request.content.decode())["data"][0]
        assert '["name"~"lawn care",i]' in ql and '["craft"~"lawn_care",i]' in ql
        return httpx.Response(200, json={"elements": [
            {"type": "node", "id": 1, "lat": 32.7, "lon": -96.8,
             "tags": {"name": "Green Lawn Care", "phone": "+1 214 555 0101", "website": "greenlawn.com",
                      "addr:housenumber": "5", "addr:street": "Oak St", "addr:city": "Dallas",
                      "addr:state": "TX", "addr:postcode": "75201", "craft": "gardener"}},
            {"type": "way", "id": 2, "center": {"lat": 32.71, "lon": -96.81}, "tags": {"name": "Way Biz"}},
            {"type": "node", "id": 3, "tags": {"shop": "no_name"}},
        ]})

    async def go():
        async with make_http(handler) as http:
            q = SearchQuery("Lawn Care", zip_code="75201", lat=32.7, lng=-96.8)
            return await OsmProvider(settings, http).search(q)

    result = run(go())
    assert [r.name for r in result.records] == ["Green Lawn Care", "Way Biz"]
    first = result.records[0]
    assert first.provider_id == "osm:node/1" and first.zip_code == "75201"
    assert first.address == "5 Oak St, Dallas, TX 75201" and first.categories == ["craft=gardener"]
    assert result.records[1].lat == 32.71


def test_osm_query_escapes_regex():
    ql = build_query('a.b (c) "d"', 1.0, 2.0, 500, 10)
    assert '\\\\.' in ql and '\\\\(' in ql and '"d"' not in ql


def test_osm_requires_coordinates(settings, make_http):
    async def go():
        async with make_http(lambda r: httpx.Response(200, json={})) as http:
            await OsmProvider(settings, http).search(SearchQuery("x", "75201"))

    with pytest.raises(ProviderError):
        run(go())


# ── Selenium helpers (browser itself is not run in tests) ────────────
def test_parse_place_url():
    url = ("https://www.google.com/maps/place/Acme/data=!4m7!3m6!1s0x864c19f77b45974b:0xb9ec9ba4f647678f"
           "!8m2!3d32.7767!4d-96.797!16s%2Fg%2F11b6!19sChIJS5dFe_cZTIYRj2dH9qSb7Lk?authuser=0")
    ids = parse_place_url(url)
    assert ids == {"place_id": "ChIJS5dFe_cZTIYRj2dH9qSb7Lk", "data_id": "0x864c19f77b45974b:0xb9ec9ba4f647678f",
                   "lat": 32.7767, "lng": -96.797}
    assert parse_place_url("")["place_id"] is None


def test_unwrap_google_redirect():
    assert unwrap_google_redirect("https://www.google.com/url?q=https://acme.com/&sa=U") == "https://acme.com/"
    assert unwrap_google_redirect("https://acme.com") == "https://acme.com"


def test_grid_cells_send_zoom_and_bounds(settings, make_http):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "serpapi" in request.url.host:
            seen["serp"] = parse_qs(request.url.query.decode())
            return httpx.Response(200, json={"local_results": []})
        seen["places"] = json.loads(request.content)
        return httpx.Response(200, json={"places": []})

    q = SearchQuery("roofer", zip_code="75201", lat=32.7, lng=-96.8, zoom=15, bounds=(32.6, -96.9, 32.8, -96.7))

    async def go():
        async with make_http(handler) as http:
            await SerpApiProvider(settings, http).search(q)
            await GooglePlacesProvider(settings, http).search(q)

    run(go())
    assert seen["serp"]["q"] == ["roofer"] and seen["serp"]["ll"] == ["@32.7,-96.8,15z"]
    rect = seen["places"]["locationRestriction"]["rectangle"]
    assert seen["places"]["textQuery"] == "roofer"
    assert rect["low"] == {"latitude": 32.6, "longitude": -96.9} and "locationBias" not in seen["places"]
