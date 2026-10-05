import asyncio
from datetime import timedelta

import httpx
import pytest

from leadengine.db import ApiUsage, Repository, utcnow
from leadengine.errors import NetworkError, ProviderNotConfigured
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.providers.base import Provider, ProviderResult
from leadengine.service import LeadService


def run(coro):
    return asyncio.run(coro)


# ── HTTP retries ─────────────────────────────────────────────────────
def test_retries_on_429_then_succeeds(make_http):
    statuses = iter([429, 503, 200])

    def handler(request):
        return httpx.Response(next(statuses), json={})

    async def go():
        async with make_http(handler) as http:
            return await http.request("GET", "https://api.test/x")

    assert run(go()).status_code == 200


def test_gives_up_and_returns_last_error_status(make_http):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500)

    async def go():
        async with make_http(handler) as http:
            return await http.request("GET", "https://api.test/x")

    assert run(go()).status_code == 500
    assert len(calls) == 3  # 1 try + max_retries=2


def test_does_not_retry_client_errors(make_http):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401)

    async def go():
        async with make_http(handler) as http:
            return await http.request("GET", "https://api.test/x")

    assert run(go()).status_code == 401 and len(calls) == 1


def test_network_error_raises_after_retries(make_http):
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    async def go():
        async with make_http(handler) as http:
            await http.request("GET", "https://api.test/x?api_key=SECRET")

    with pytest.raises(NetworkError) as exc:
        run(go())
    assert "SECRET" not in str(exc.value) and "api.test" in str(exc.value)


# ── Service: cache-first pipeline ────────────────────────────────────
class FakeProvider(Provider):
    name = "serpapi"
    label = "fake"
    paid = True

    def __init__(self, settings, http, credits=None, records=None):
        super().__init__(settings, http, credits)
        self.calls = 0
        self.records = records or []

    async def search(self, query):
        self.calls += 1
        self._record("google_maps")
        return ProviderResult(list(self.records), exhausted=True, api_calls=1)


def make_records():
    return [
        BusinessRecord(name="A Dumpsters", provider="serpapi", place_id="ChIJA", phone="214-555-0001",
                       website="a.com", rating=4.7, review_count=150, address="1 St, Dallas, TX 75201", rank=1),
        BusinessRecord(name="B Dumpsters", provider="serpapi", place_id="ChIJB", rank=2),
    ]


def test_second_identical_search_uses_cache(settings, session_factory, credits, make_http):
    async def go():
        async with make_http(lambda r: httpx.Response(500)) as http:
            fake = FakeProvider(settings, http, credits, make_records())
            service = LeadService(settings, session_factory, http, credits, providers={"serpapi": fake})
            q = SearchQuery("Dumpster Rental", zip_code="75201", lat=32.7, lng=-96.8)
            first = await service.search(q, "serpapi")
            second = await service.search(q, "serpapi")
            forced = await service.search(q, "serpapi", refresh=True)
            return fake, first, second, forced

    fake, first, second, forced = run(go())
    assert fake.calls == 2  # first + forced refresh; the repeat was free
    assert not first.from_cache and first.api_calls == 1
    assert second.from_cache and second.api_calls == 0
    assert [b.name for b, _ in second.results] == ["A Dumpsters", "B Dumpsters"]
    assert not forced.from_cache
    assert credits.summary()[0].calls_month == 2
    with session_factory() as s:
        assert Repository(s).stats()["businesses"] == 2  # refresh merged, no duplicates


def test_service_geocodes_zip_once_and_caches_it(settings, session_factory, credits, make_http):
    geo_calls = []

    def handler(request):
        geo_calls.append(str(request.url))
        return httpx.Response(200, json=[{"lat": "32.78", "lon": "-96.80"}])

    class NeedsCoords(FakeProvider):
        needs_coordinates = True
        seen = []

        async def search(self, query):
            self.seen.append((query.lat, query.lng))
            return await super().search(query)

    async def go():
        async with make_http(handler) as http:
            p = NeedsCoords(settings, http, credits, make_records())
            service = LeadService(settings, session_factory, http, credits, providers={"serpapi": p})
            # 75201 is in the bundled ZIP data -> no network; 99999 is not -> Nominatim once, then DB cache
            await service.search(SearchQuery("roofer", zip_code="75201"), "serpapi")
            await service.search(SearchQuery("roofer", zip_code="99999"), "serpapi")
            await service.search(SearchQuery("plumber", zip_code="99999"), "serpapi")
            return p

    p = run(go())
    assert p.seen[0] == (32.7878, -96.79948)  # bundled Census centroid
    assert p.seen[1:] == [(32.78, -96.8), (32.78, -96.8)]
    assert len(geo_calls) == 1 and "postalcode=99999" in geo_calls[0]


def test_one_bad_record_does_not_break_the_run(settings, session_factory, make_http):
    records = make_records()
    records.insert(1, BusinessRecord(name=None, provider="serpapi", place_id="ChIJBAD"))  # name NOT NULL

    async def go():
        async with make_http(lambda r: httpx.Response(500)) as http:
            fake = FakeProvider(settings, http, None, records)
            service = LeadService(settings, session_factory, http, providers={"serpapi": fake})
            return await service.search(SearchQuery("x", "75201", lat=1.0, lng=2.0), "serpapi")

    outcome = run(go())
    assert outcome.skipped == 1
    assert [b.name for b, _ in outcome.results] == ["A Dumpsters", "B Dumpsters"]


def test_unconfigured_provider_raises_clear_error(settings, session_factory, make_http):
    from dataclasses import replace

    no_keys = replace(settings, serpapi_api_key="")

    async def go():
        async with make_http(lambda r: httpx.Response(200)) as http:
            await LeadService(no_keys, session_factory, http).search(SearchQuery("x", "75201"), "serpapi")

    with pytest.raises(ProviderNotConfigured, match="SERPAPI_API_KEY"):
        run(go())


# ── Credits ──────────────────────────────────────────────────────────
def test_credit_summary_month_and_free_tier(settings, session_factory, credits):
    for _ in range(3):
        credits.record("serpapi", "google_maps")
    credits.record("serpapi", "google_maps", success=False)
    credits.record("osm", "interpreter")
    with session_factory() as s:  # one call from last month
        s.add(ApiUsage(provider="serpapi", endpoint="google_maps", units=1, cost_usd=0.015,
                       at=utcnow().replace(day=1) - timedelta(days=2)))
        s.commit()

    by_name = {r.provider: r for r in credits.summary()}
    serp = by_name["serpapi"]
    assert serp.calls_month == 3 and serp.failed_month == 1 and serp.calls_total == 4
    assert serp.free_remaining == 247
    assert serp.est_cost_month_usd == pytest.approx(0.045)
    assert by_name["osm"].free_remaining is None and by_name["osm"].est_cost_month_usd == 0


@pytest.mark.parametrize("payload", [[], {"error": "x"}, [{"lat": "n/a", "lon": "1"}], "not json"])
def test_bad_geocode_response_does_not_crash(settings, session_factory, make_http, payload):
    def handler(request):
        if "nominatim" in request.url.host:
            if payload == "not json":
                return httpx.Response(200, text="<html>")
            return httpx.Response(200, json=payload)
        return httpx.Response(500)

    async def go():
        async with make_http(handler) as http:
            fake = FakeProvider(settings, http, None, make_records())
            fake.wants_coordinates = True
            service = LeadService(settings, session_factory, http, providers={"serpapi": fake})
            return await service.search(SearchQuery("x", "99999"), "serpapi")

    outcome = run(go())  # provider still runs, just without coordinates
    assert len(outcome.results) == 2
