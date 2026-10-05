import asyncio
from dataclasses import replace

import httpx
import pytest

from leadengine.config import ProviderSettings
from leadengine.db import Business, Repository
from leadengine.db.models import AdSweep
from leadengine.enrich.ads import sweep as sw
from leadengine.enrich.ads.landing import message_match, page_facts, score_landing
from leadengine.enrich.ads.serp import SerpAd, SerpSnapshot
from leadengine.enrich.ads.transparency import parse_transparency_text
from leadengine.models import BusinessRecord
from leadengine.service import LeadService, resolve_place
from tests.fake_maps import FakeMaps, business

pytest.importorskip("playwright.async_api")


def run(coro):
    return asyncio.run(coro)


# ── pure logic ───────────────────────────────────────────────────────
def test_variations_use_google_ideas_first_and_drop_job_searches():
    v = sw.variations("Dumpster Rental", 5, extra=["dumpster rental prices", "dumpster rental jobs", "pizza"])
    assert v[0] == "dumpster rental" and v[1] == "dumpster rental prices" and "dumpster rental jobs" not in v
    assert len(v) == 5 and "dumpster rental near me" in v


def test_aggregate_merges_one_advertiser_across_searches():
    s1 = SerpSnapshot("dumpster rental", "Dallas, TX", "t", [
        SerpAd("search", "Same-Day Dumpsters", "acme.com", position=2, landing_url="https://acme.com/lp"),
        SerpAd("lsa", "Bob's Dumpsters LLC", None, "(214) 555-0101", "Google Guaranteed", 1)])
    s2 = SerpSnapshot("dumpster rental near me", "Plano, TX", "t", [
        SerpAd("search", "Cheap Roll-Offs", "acme.com", position=1),
        SerpAd("places", "Bobs Dumpsters", None, "214-555-0101", position=1)])
    ads = sw.aggregate([s1, s2])
    acme = next(a for a in ads if a.domain == "acme.com")
    bob = next(a for a in ads if a.domain is None)
    assert acme.hits == 2 and acme.best_position == 1 and acme.landing_urls == ["https://acme.com/lp"]
    assert acme.locations == {"Dallas, TX", "Plano, TX"} and len(acme.titles) == 2
    assert bob.hits == 2 and bob.kinds == {"lsa", "places"} and bob.name == "Bob's Dumpsters LLC"
    assert "seen in 2 of 2 Google searches" in sw.evidence_lines(acme, 2)[0]


def test_landing_page_scoring_flags_what_wastes_ad_money():
    bad = page_facts("<html><head><title>Welcome to ABC Hauling</title></head><body><nav>" + "<a href=#>x</a>" * 15
                     + "</nav><h1>Welcome to ABC Hauling</h1><p>Since 1990.</p></body></html>", "http://abc.com/")
    score, issues = score_landing(bad, ad_title="Same-Day Dumpster Rental - Free Delivery", load_ms=6000,
                                  mobile=None, pagespeed={"performance": 21})
    text = " ".join(issues)
    assert score < 40 and "homepage" in text and "doesn't match the ad" in text and "tap-to-call" in text
    assert "not built for phones" in text and "PageSpeed 21" in text and "HTTPS" in text
    good = page_facts('<html><head><meta name="viewport" content="width=device-width"><title>Same-Day Dumpster Rental'
                      '</title></head><body><h1>Same-Day Dumpster Rental in Dallas</h1><a href="tel:2145550100">Call</a>'
                      '<form><input name="name"><textarea name="message"></textarea><button>Get a free quote</button></form>'
                      '<p>500+ reviews, licensed and insured</p></body></html>', "https://abc.com/dumpster-rental")
    gscore, gissues = score_landing(good, ad_title="Same-Day Dumpster Rental", load_ms=900, mobile=None,
                                    pagespeed={"performance": 88})
    assert gscore >= 85 and not gissues
    assert message_match("Same-Day Dumpster Rental", "Welcome") == 0.0


def test_transparency_text_parsing():
    from datetime import date

    out = parse_transparency_text("ABC Hauling LLC\n~ 24 ads\nLast shown: Sep 28, 2026\nLast shown: Jul 2, 2026",
                                  date(2026, 10, 1))
    assert out == {"creatives": 24, "last_shown": "2026-09-28", "days_ago": 3}
    assert parse_transparency_text("No ads found for this advertiser", date(2026, 10, 1))["creatives"] == 0
    assert parse_transparency_text("something else entirely", date(2026, 10, 1)) is None


def test_resolve_place():
    assert resolve_place("10001") == ("New York", "NY", "New York, NY")
    assert resolve_place("Jersey City, nj") == ("Jersey City", "NJ", "Jersey City, NJ")
    assert resolve_place("Hoboken")[1] == "NJ" and resolve_place("") is None and resolve_place("99999") is None


# ── full sweep in a real browser against a local fake Google ─────────
@pytest.fixture
def fake_google():
    with FakeMaps() as fm:
        yield fm


@pytest.fixture
def sweep_settings(settings, fake_google):
    extra = {"base_url": fake_google.base_url, "contexts": 2, "max_attempts": 1, "timeout_ms": 10000}
    return replace(settings, providers={**settings.providers, "playwright": ProviderSettings(extra=extra)},
                   sections={**settings.sections, "ads": {"serp_provider": "playwright", "sweep_suggest": False,
                                                          "sweep_delay_min": 0, "sweep_delay_max": 0}})


def test_ads_sweep_end_to_end(sweep_settings, session_factory, make_http, fake_google):
    b2, b3 = business(2), business(3)
    ad_domain = "pros" + b2["website"].split("pros", 1)[1].split("/")[0]

    def site(request):
        host = request.url.host
        if host == ad_domain:
            return httpx.Response(200, text="<html><head><title>Welcome</title></head><body><h1>Welcome to our site</h1>"
                                            "<p>Family business.</p></body></html>", headers={"content-type": "text/html"})
        if host == "bigchain.com":
            return httpx.Response(200, text='<html><head><title>BigChain Dumpsters | Home</title><meta name="viewport" '
                                            'content="width=device-width"></head><body><h1>BigChain Dumpsters</h1>'
                                            '<a href="tel:8005550000">Call</a></body></html>',
                                  headers={"content-type": "text/html"})
        return httpx.Response(404)

    with session_factory() as db:
        repo = Repository(db)
        existing = repo.upsert_business(BusinessRecord(name=b2["name"], provider="t", place_id=b2["place_id"],
                                                        website=b2["website"], phone=b2["phone"], rating=4.6,
                                                        review_count=88, city="Dallas", state="TX"))
        lsa_biz = repo.upsert_business(BusinessRecord(name=b3["name"], provider="t", place_id=b3["place_id"],
                                                      phone=b3["phone"], city="Dallas", state="TX"))
        db.commit()
        existing_id, lsa_id = existing.id, lsa_biz.id

    async def go():
        async with make_http(site) as http:
            svc = LeadService(sweep_settings, session_factory, http)
            try:
                first = await svc.ads_sweep("dumpster rental", ["75201"], variations=2, landing=True, deep=False)
                second = await svc.ads_sweep("dumpster rental", ["75201"], variations=2, landing=False, deep=False)
                return first, second
            finally:
                await svc.aclose()

    first, second = run(go())
    assert first["searches"] == 3 and first["failed"] == 0 and first["advertisers"] == 5
    assert len(fake_google.serp_queries) == 3                       # second sweep came from the cache
    assert second["new"] == 0
    with session_factory() as db:
        sweep = db.get(AdSweep, first["sweep_id"])
        by = {a["domain"] or a["name"]: a for a in sweep.advertisers}
        assert by[ad_domain]["business_id"] == existing_id and by[ad_domain]["hits"] == 3
        assert by[ad_domain]["landing_url"].endswith("/landing")
        assert by[b3["name"]]["business_id"] == lsa_id and by[b3["name"]]["kinds"] == ["lsa"]
        herman = by["hermans.example.com"]                             # map-pack ad with a website link
        assert herman["kinds"] == ["places"] and herman["name"] == "Herman's Recycling"
        assert not any(a["name"] == "Organic Pack Business" for a in sweep.advertisers)   # not sponsored
        ex = db.get(Business, existing_id)
        assert ex.ads_status == "Active" and ex.landing_score is not None and ex.landing_score < 60
        assert db.get(Business, lsa_id).lsa is True
        chain = db.get(Business, by["bigchain.com"]["business_id"])
        assert chain.name == "BigChain Dumpsters" and chain.website == "https://bigchain.com"   # name from its page
        land = Repository(db).latest_enrichment(existing_id, "landing", fresh_only=False).payload
        assert any("doesn't match the ad" in i for i in land["issues"])
        ads = Repository(db).latest_enrichment(existing_id, "ads", fresh_only=False).payload
        assert ads["status"] == "Active" and "seen in 3 of 3 Google searches" in ads["evidence"][0]


def test_ads_pages(settings, session_factory):
    from fastapi.testclient import TestClient

    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        sf = app.state.sf
        with sf() as db:
            b = Repository(db).upsert_business(BusinessRecord(name="Acme", provider="t", place_id="A1"))
            db.flush()
            Repository(db).set_enrichment(b.id, "landing", {"score": 31, "issues": ["no tap-to-call button"],
                                                            "final_url": "https://acme.com/"})
            db.add(AdSweep(keyword="dumpster rental", locations=["Dallas, TX"], searches=6, failed=0,
                           advertisers=[{"business_id": b.id, "name": "Acme", "domain": "acme.com", "kinds": ["search"],
                                         "hits": 4, "new": True}]))
            db.commit()
            bid = b.id
        assert "Past sweeps" in c.get("/ads").text
        detail = c.get("/ads/1").text
        assert "Acme" in detail and "no tap-to-call" in detail and ">new<" in detail
        assert "Ad landing page" in c.get(f"/leads/{bid}").text
        r = c.post("/ads", data={"keyword": "dumpster rental", "places": "New York, NY\n10001 07302", "variations": 4},
                   follow_redirects=False)
        assert r.status_code == 303
        from leadengine.db import Job
        with sf() as db:
            job = db.get(Job, int(r.headers["location"].rsplit("/", 1)[1]))
            assert job.kind == "sweep" and job.params["locations"] == ["New York, NY", "10001", "07302"]
        assert "Ads sweep" in c.get(r.headers["location"]).text
