import asyncio
from datetime import timedelta

from leadengine.cache import cached_enrichment, is_fresh
from leadengine.db import Business, BusinessSource, Repository, utcnow
from leadengine.models import BusinessRecord, SearchQuery


def rec(**kw) -> BusinessRecord:
    base = dict(name="Acme Dumpsters", provider="serpapi")
    base.update(kw)
    return BusinessRecord(**base)


def test_upsert_by_place_id_merges(session_factory):
    with session_factory() as s:
        repo = Repository(s)
        a = repo.upsert_business(rec(place_id="ChIJ1", provider_id="0x1:0x2", phone="(214) 555-0199",
                                     rating=4.5, review_count=100, categories=["Dumpster rental service"],
                                     address="1 Main St, Dallas, TX 75201, United States"))
        b = repo.upsert_business(rec(place_id="ChIJ1", provider="google_places", provider_id="ChIJ1",
                                     website="https://www.acme.com/", review_count=120,
                                     categories=["Dumpster rental service", "Waste management"]))
        s.commit()
        assert a.id == b.id
        biz = s.get(Business, a.id)
        assert biz.review_count == 120          # newer value wins
        assert biz.rating == 4.5                # missing value does not erase
        assert biz.categories == ["Dumpster rental service", "Waste management"]
        assert (biz.city, biz.state, biz.zip_code) == ("Dallas", "TX", "75201")
        assert biz.domain == "acme.com" and biz.phone_norm == "2145550199"
        assert biz.dedupe_key == "2145550199|acme.com"
        assert {src.provider for src in biz.sources} == {"serpapi", "google_places"}


def test_fallback_dedupe_by_phone_and_domain(session_factory):
    with session_factory() as s:
        repo = Repository(s)
        osm = repo.upsert_business(rec(provider="osm", provider_id="osm:node/9",
                                       phone="+1 214 555 0199", website="acme.com"))
        google = repo.upsert_business(rec(place_id="ChIJ9", phone="214-555-0199", website="https://www.acme.com"))
        assert osm.id == google.id
        assert google.place_id == "ChIJ9"       # place_id attached to the existing record


def test_different_place_ids_are_not_merged(session_factory):
    with session_factory() as s:
        repo = Repository(s)
        a = repo.upsert_business(rec(place_id="ChIJ-A", phone="2145550199", website="acme.com"))
        b = repo.upsert_business(rec(place_id="ChIJ-B", phone="2145550199", website="acme.com"))
        assert a.id != b.id


def test_same_provider_id_updates_source_not_duplicates(session_factory):
    with session_factory() as s:
        repo = Repository(s)
        first = repo.upsert_business(rec(provider="osm", provider_id="osm:way/5", name="Old Name"))
        second = repo.upsert_business(rec(provider="osm", provider_id="osm:way/5", name="New Name"))
        s.commit()
        assert first.id == second.id and second.name == "New Name"
        assert s.query(BusinessSource).count() == 1


def test_search_cache_freshness(session_factory):
    q = SearchQuery(keyword="Dumpster Rental", zip_code="75201", max_results=20)
    with session_factory() as s:
        repo = Repository(s)
        biz = repo.upsert_business(rec(place_id="ChIJ1"))
        search = repo.record_search(q, "serpapi", [(biz, 1), (biz, 3)], exhausted=True, api_calls=1)
        s.commit()
        assert search.result_count == 1
        same = SearchQuery(keyword="dumpster   rental", zip_code="75201", max_results=50)
        assert repo.find_fresh_search("serpapi", same, ttl_days=14).id == search.id  # exhausted covers 50
        assert repo.find_fresh_search("google_places", same, 14) is None
        assert repo.find_fresh_search("serpapi", SearchQuery("dumpster rental", "75202"), 14) is None
        search.ran_at = utcnow() - timedelta(days=15)
        s.commit()
        assert repo.find_fresh_search("serpapi", same, ttl_days=14) is None


def test_search_cache_not_reused_when_more_results_wanted(session_factory):
    with session_factory() as s:
        repo = Repository(s)
        biz = repo.upsert_business(rec(place_id="ChIJ1"))
        repo.record_search(SearchQuery("plumber", "75201", max_results=1), "serpapi", [(biz, 1)],
                           exhausted=False, api_calls=1)
        assert repo.find_fresh_search("serpapi", SearchQuery("plumber", "75201", max_results=1), 14)
        assert repo.find_fresh_search("serpapi", SearchQuery("plumber", "75201", max_results=40), 14) is None


def test_cached_enrichment_runs_compute_once(session_factory):
    calls = []

    async def compute():
        calls.append(1)
        return {"score": 42}

    async def run():
        with session_factory() as s:
            repo = Repository(s)
            biz = repo.upsert_business(rec(place_id="ChIJ1"))
            first = await cached_enrichment(repo, biz.id, "website", 30, compute)
            second = await cached_enrichment(repo, biz.id, "website", 30, compute)
            forced = await cached_enrichment(repo, biz.id, "website", 30, compute, refresh=True)
            return first, second, forced

    first, second, forced = asyncio.run(run())
    assert first == ({"score": 42}, False)
    assert second == ({"score": 42}, True)
    assert forced == ({"score": 42}, False)
    assert len(calls) == 2


def test_expired_enrichment_is_not_used(session_factory):
    with session_factory() as s:
        repo = Repository(s)
        biz = repo.upsert_business(rec(place_id="ChIJ1"))
        row = repo.set_enrichment(biz.id, "ads", {"status": "Active"}, ttl_days=7)
        assert repo.latest_enrichment(biz.id, "ads").payload == {"status": "Active"}
        row.expires_at = utcnow() - timedelta(seconds=1)
        assert repo.latest_enrichment(biz.id, "ads") is None
        assert repo.latest_enrichment(biz.id, "ads", fresh_only=False) is not None


def test_is_fresh():
    now = utcnow()
    assert is_fresh(now - timedelta(days=1), 30, now)
    assert not is_fresh(now - timedelta(days=31), 30, now)
    assert not is_fresh(None, 30, now)


def test_list_businesses_filters(session_factory):
    with session_factory() as s:
        repo = Repository(s)
        a = repo.upsert_business(rec(place_id="A", name="A", rating=4.8, review_count=200, zip_code="75201", website="a.com"))
        repo.upsert_business(rec(place_id="B", name="B", rating=3.9, review_count=10, zip_code="75201"))
        repo.record_search(SearchQuery("roofer", "75201"), "serpapi", [(a, 1)], exhausted=True, api_calls=1)
        s.commit()
        assert [b.name for b in repo.list_businesses(min_rating=4.5)] == ["A"]
        assert [b.name for b in repo.list_businesses(min_reviews=50)] == ["A"]
        assert [b.name for b in repo.list_businesses(has_website=False)] == ["B"]
        assert [b.name for b in repo.list_businesses(keyword="Roofer")] == ["A"]
        assert len(repo.list_businesses(zip_code="75201")) == 2


def test_fill_only_does_not_overwrite(session_factory):
    with session_factory() as s:
        repo = Repository(s)
        biz = repo.upsert_business(rec(place_id="ChIJF", phone="214-555-0001", website=None, rating=4.2))
        repo.upsert_business(rec(place_id="ChIJF", name="Other Name", phone="999-999-9999",
                                 website="https://new.com", rating=1.0), fill_only=True)
        assert (biz.name, biz.phone, biz.website, biz.rating) == ("Acme Dumpsters", "214-555-0001", "https://new.com", 4.2)
