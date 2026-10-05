"""Real headless Chromium against the local fake Maps server (tests/fake_maps.py)."""

import asyncio
from dataclasses import replace

import httpx
import pytest

from leadengine.config import ProviderSettings
from leadengine.db import Business, Repository
from leadengine.errors import ProviderBlocked
from leadengine.geo.grid import Cell
from leadengine.models import SearchQuery
from leadengine.providers.playwright_maps import PlaywrightMapsProvider
from leadengine.service import LeadService
from tests.fake_maps import FakeMaps

pw = pytest.importorskip("playwright.async_api")


def _chromium_ok() -> bool:
    async def probe():
        async with pw.async_playwright() as p:
            b = await p.chromium.launch()
            await b.close()

    try:
        asyncio.run(probe())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _chromium_ok(), reason="Chromium not available")


@pytest.fixture
def fake_maps():
    with FakeMaps() as fm:
        yield fm


@pytest.fixture
def pw_settings(settings, fake_maps):
    extra = {"base_url": fake_maps.base_url, "scroll_pause_s": 0.05, "stale_scrolls": 3, "contexts": 3,
             "details": "missing", "max_attempts": 1, "timeout_ms": 10000}
    discovery = {"provider": "playwright", "initial_cell_km": 6, "max_depth": 1, "max_cells": 10,
                 "concurrency": 2, "shortlist_min_reviews": 60, "shortlist_min_rating": 4.0,
                 "activity": True, "fill_missing": True, "fill_provider": "serpapi", "max_fill": 3}
    return replace(settings,
                   providers={**settings.providers, "playwright": ProviderSettings(extra=extra)},
                   sections={**settings.sections, "discovery": discovery})


def run(coro):
    return asyncio.run(coro)


def test_search_merges_json_and_dom(pw_settings, make_http, fake_maps):
    async def go():
        async with make_http(lambda r: httpx.Response(500)) as http:
            p = PlaywrightMapsProvider(pw_settings, http)
            try:
                return await p.search(SearchQuery("dumpster rental", zip_code="75201", max_results=50))
            finally:
                await p.aclose()

    result = run(go())
    assert len(result.records) == 23 and result.exhausted          # list ended
    assert [r.rank for r in result.records] == list(range(1, 24))
    first = result.records[0]
    assert first.sponsored and not any(r.sponsored for r in result.records[1:])
    assert first.place_id.startswith("ChIJ") and first.data_id.startswith("0x")
    assert first.address == "101 Elm St, Dallas, TX 75201" and first.lat is not None
    assert first.categories[0] == "Dumpster rental service"
    # images were blocked by the browser, so the server never saw image requests
    assert not any(r.endswith(".png") for r in fake_maps.requests)
    assert any("tbm=map" in r for r in fake_maps.requests)          # XHR pages were loaded by scrolling


def test_saturated_cell_is_not_exhausted(pw_settings, make_http):
    cell = Cell(32.78, -96.80, half_km=6)                            # zoom 13 -> fake returns 130, no end
    async def go():
        async with make_http(lambda r: httpx.Response(500)) as http:
            p = PlaywrightMapsProvider(pw_settings, http)
            try:
                q = SearchQuery("dumpster rental", lat=cell.lat, lng=cell.lng, zoom=cell.zoom, max_results=40)
                return await p.search(q)
            finally:
                await p.aclose()

    result = run(go())
    assert len(result.records) == 40 and not result.exhausted


def test_place_details_reviews_claim_photos(pw_settings, make_http):
    async def go():
        async with make_http(lambda r: httpx.Response(500)) as http:
            p = PlaywrightMapsProvider(pw_settings, http)
            try:
                res = await p.search(SearchQuery("dumpster rental", zip_code="75201", max_results=5))
                return await p.place_details(res.records[2].google_maps_url, reviews=True)  # business #3: unclaimed
            finally:
                await p.aclose()

    d = run(go())
    assert d["claimed"] is False and d["photo_count"] == 43
    assert d["phone"] == "+12145551003" and d["website"].startswith("https://pros")
    assert len(d["recent_review_dates"]) == 4 and d["owner_response_rate"] == 0.75   # "Newest" sort applied
    assert d["json"]["place_id"].startswith("ChIJ")


def test_captcha_page_raises_blocked(pw_settings, make_http):
    async def go():
        async with make_http(lambda r: httpx.Response(500)) as http:
            p = PlaywrightMapsProvider(pw_settings, http)
            try:
                await p.search(SearchQuery("blockme", zip_code="75201"))
            finally:
                await p.aclose()

    with pytest.raises(ProviderBlocked):
        run(go())


def test_no_results_is_empty_not_error(pw_settings, make_http):
    async def go():
        async with make_http(lambda r: httpx.Response(500)) as http:
            p = PlaywrightMapsProvider(pw_settings, http)
            try:
                return await p.search(SearchQuery("nothing", zip_code="75201"))
            finally:
                await p.aclose()

    assert run(go()).records == []


def test_discover_end_to_end_with_activity_fill_and_cache(pw_settings, session_factory, credits, make_http):
    serp_calls = []

    def serpapi(request: httpx.Request) -> httpx.Response:
        if "serpapi.com" not in request.url.host:   # website crawls for emails: sites are offline here
            return httpx.Response(404, text="not found")
        serp_calls.append(str(request.url))
        return httpx.Response(200, json={"place_results": {
            "title": "Filled Name", "place_id": request.url.params.get("place_id"),
            "phone": "(214) 555-9999", "website": "https://filled.example.com", "reviews": 999}})

    async def go():
        async with make_http(serpapi) as http:
            service = LeadService(pw_settings, session_factory, http, credits)
            try:
                first = await service.discover("dumpster rental", "75201")
                second = await service.discover("dumpster rental", "75201")
                return first, second
            finally:
                await service.aclose()

    first, second = run(go())
    assert not first.from_cache and first.grid.cells_run == 1 and first.grid.cells_split == 0
    assert len(first.results) == 12 and first.sponsored == 1
    # shortlist = >=60 reviews and >=4.0 stars -> fake businesses #8..#12
    assert first.shortlisted == 5 and first.activity_checked == 5
    # #8 and #12 have no website, #10 no phone -> exactly 3 paid lookups (max_fill = 3)
    assert first.paid_calls == 3 and first.filled == 3
    assert second.from_cache and second.activity_cached == second.shortlisted and second.activity_checked == 0
    assert second.paid_calls == 0      # gaps already filled -> no credits spent on the repeat run
    with session_factory() as s:
        repo = Repository(s)
        shortlisted = [b for b in s.query(Business).all() if (b.review_count or 0) >= 60 and (b.rating or 0) >= 4]
        assert all(b.recent_review_dates and b.owner_response_rate == 0.75 for b in shortlisted)
        assert all(b.claimed is not None and b.photo_count for b in shortlisted)
        assert repo.stats()["businesses"] == 12
        filled = [b for b in shortlisted if b.phone == "(214) 555-9999"]
        assert len(filled) == 1                                    # only #10 lacked a phone; others kept theirs
        assert not any(b.name == "Filled Name" for b in shortlisted)   # gap fill never overwrites
        assert sum(1 for b in shortlisted if b.website == "https://filled.example.com") == 2  # #8, #12
    # paid API only used for shortlisted businesses that were missing phone or website
    assert len(serp_calls) == first.paid_calls <= 3
    # emails step ran for the shortlist (fake business websites are unreachable in this test)
    assert first.emails is not None and first.emails.unreachable == first.shortlisted
    assert second.emails.cached == second.shortlisted and second.emails.checked == 0
