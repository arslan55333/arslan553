import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from leadengine import suggest
from leadengine.config import HttpSettings
from leadengine.http import HttpClient

HS = HttpSettings(timeout_seconds=5, max_retries=0, backoff_base_seconds=0)

GOOGLE = ["dumpster r", ["dumpster rental", "dumpster rental near me", "dumpster rental jobs", "dumpster rental prices"]]
PHOTON = {"features": [
    {"properties": {"name": "Astoria", "osm_value": "suburb", "countrycode": "US", "city": "New York", "state": "New York"},
     "geometry": {"coordinates": [-73.9235, 40.7644]}},
    {"properties": {"name": "Astoria", "osm_value": "city", "countrycode": "US", "state": "Oregon"},
     "geometry": {"coordinates": [-123.8313, 46.1879]}},
    {"properties": {"name": "Astoria Blvd", "osm_value": "primary", "countrycode": "US"},
     "geometry": {"coordinates": [-73.9, 40.7]}}]}
OVERPASS = {"elements": [
    {"lat": 40.7465, "lon": -74.0014, "tags": {"name": "Chelsea", "place": "neighbourhood"}},
    {"lat": 40.7549, "lon": -73.9840, "tags": {"name": "Midtown", "place": "neighbourhood"}},
    {"lat": 40.7440, "lon": -74.0324, "tags": {"name": "Hoboken", "place": "city"}}]}


def handler(calls):
    def h(request: httpx.Request):
        calls.append(request.url.host)
        if request.url.host == "suggestqueries.google.com":
            return httpx.Response(200, content=json.dumps(GOOGLE).encode("latin-1"))
        if request.url.host == "photon.komoot.io":
            return httpx.Response(200, json=PHOTON)
        if request.url.host == "overpass-api.de":
            return httpx.Response(200, json=OVERPASS)
        return httpx.Response(404)
    return h


def http_for(calls):
    return HttpClient(HS, transport=httpx.MockTransport(handler(calls)))


def test_keyword_suggestions_mix_history_niches_and_google():
    suggest._mem.clear()
    calls = []

    async def go():
        async with http_for(calls) as http:
            return await suggest.keyword_suggestions("dumpster r", http=http, past=["Dumpster rental Brooklyn"])
    out = asyncio.run(go())
    texts = [o["text"] for o in out]
    assert texts[0] == "dumpster rental brooklyn" and out[0]["source"] == "your searches"
    assert "dumpster rental" in texts and "dumpster rental prices" in texts
    assert "dumpster rental jobs" not in texts                      # job-seeker queries filtered out
    assert asyncio.run(suggest.keyword_suggestions("plumb", online=False))[0]["text"] == "plumber"


def test_offline_places_zip_prefix_and_city():
    zips = suggest.offline_places("1000")
    assert all(z["zips"][0].startswith("1000") for z in zips) and zips[0]["type"] == "zip"
    assert suggest.offline_places("10001")[0]["zips"] == ["10001"]
    city = suggest.offline_places("Jersey City, NJ")[0]
    assert city["type"] == "city" and "07302" in city["zips"] and len(city["zips"]) > 3


def test_online_places_and_areas_map_to_zips(session_factory):
    from leadengine.db import Repository

    suggest._mem.clear()
    calls = []

    async def go():
        async with http_for(calls) as http:
            places = await suggest.online_places("Astoria", http)
            with session_factory() as s:
                areas = await suggest.areas_for_zip("10001", http=http, repo=Repository(s))
                s.commit()
            suggest._mem.clear()
            with session_factory() as s:          # second time: from the database cache, no network
                again = await suggest.areas_for_zip("10001", http=http, repo=Repository(s))
            return places, areas, again
    places, areas, again = asyncio.run(go())
    assert [p["type"] for p in places] == ["suburb", "city"]          # the street is dropped
    assert places[0]["zips"][0].startswith("111") and places[1]["zips"][0].startswith("971")   # Queens NY / Oregon
    names = {a["name"]: a for a in areas["areas"]}
    assert "Chelsea" in names and names["Chelsea"]["zips"][0].startswith("100") and names["Hoboken"]["zips"] == ["07030"]
    assert areas["areas"][0]["km"] <= areas["areas"][-1]["km"]
    assert calls.count("overpass-api.de") == 1 and {a["name"] for a in again["areas"]} == set(names)


def test_suggestion_endpoints(settings):
    from leadengine.ui.app import create_app

    suggest._mem.clear()
    calls = []
    app = create_app(settings, start_runner=False)
    app.state.http_factory = lambda: http_for(calls)
    with TestClient(app) as c:
        kw = c.get("/api/suggest/keyword?q=dumpster r").json()
        assert any(k["source"] == "google" for k in kw)
        places = c.get("/api/suggest/place?q=Astoria").json()
        assert any(p["type"] == "suburb" for p in places)
        assert c.get("/api/areas?zip=10001").json()["label"] == "New York, NY"
        assert c.get("/api/areas?zip=abc").status_code == 400
        page = c.get("/run").text
        assert 'id="kw"' in page and "/api/areas" in page and "Towns &amp; neighbourhoods" not in page


def test_suggestions_survive_being_offline():
    suggest._mem.clear()

    def down(request):
        raise httpx.ConnectError("offline")

    async def go():
        async with HttpClient(HS, transport=httpx.MockTransport(down)) as http:
            return (await suggest.keyword_suggestions("septic", http=http),
                    await suggest.online_places("Astoria", http))
    kw, places = asyncio.run(go())
    assert kw and kw[0]["source"] == "niche" and places == []
