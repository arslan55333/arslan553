"""Owner feedback round: out-of-ZIP leads, 'not checked' vs 'no', Hot needs ad proof, clickable dashboard."""
import asyncio
from datetime import date

from fastapi.testclient import TestClient

from leadengine import crm, jobs
from leadengine.db import Business, Job, Repository
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.scoring.opportunity import score_business
from leadengine.service import LeadService, fill_location

TODAY = date(2026, 10, 1)


def add(s, name, **kw):
    b = Repository(s).upsert_business(BusinessRecord(name=name, provider="t", place_id="P-" + name, **kw))
    s.commit()
    return b


def test_fill_location_from_coordinates():
    b = Business(name="Prime Dumpster", address="91-01 120th St", lat=40.6946, lng=-73.8290)   # Richmond Hill, Queens
    assert fill_location(b) and b.zip_code.startswith("114") and b.state == "NY" and b.city
    assert not fill_location(Business(name="x"))                                                # no coordinates


def test_hot_needs_proof_of_ad_spend_and_unchecked_is_explained():
    strong = dict(name="Cardella Waste", rating=4.9, review_count=591, phone="2125550000", website="https://c.com")
    unknown = score_business(Business(**strong), today=TODAY, checked=set())
    assert unknown.label != "Hot"
    assert "email not checked yet" in unknown.reason and "ads & website not checked yet" in unknown.reason
    proven = score_business(Business(**strong, ads_status="Active", website_score=20), today=TODAY,
                            checked={"ads", "website", "emails"})
    assert proven.label == "Hot" and "no email found" in proven.reason
    no_ads = score_business(Business(**strong, ads_status="None", website_score=10), today=TODAY,
                            checked={"ads", "website", "emails"})
    assert no_ads.label != "Hot"
    relaxed = score_business(Business(**strong, ads_status="None", website_score=0), today=TODAY,
                             rules={"require_ads_for_hot": False, "hot": 60}, checked={"ads", "website", "emails"})
    assert relaxed.label == "Hot"                                   # gate can be switched off in config.toml


def seed(sf):
    with sf() as s:
        repo = Repository(s)
        a = add(s, "Prime Dumpster LLC", rating=4.6, review_count=31, lat=40.6946, lng=-73.8290, website="https://prime.com")
        b = add(s, "Tiny Co", rating=4.0, review_count=2, zip_code="10013", website="https://tiny.com")
        c = add(s, "Checked Co", rating=4.8, review_count=90, zip_code="10013", website="https://checked.com")
        repo.record_search(SearchQuery("dumpster rental", "10001"), "t", [(a, 1), (b, 2), (c, 3)], exhausted=True,
                           api_calls=1)
        repo.set_enrichment(c.id, "ads", {"status": "None", "evidence": []})
        c.ads_status = None
        repo.set_enrichment(c.id, "emails", {"emails": []})
        crm.set_status(s, c.id, "Emailed")
        s.commit()
        return a.id, b.id, c.id


def test_leads_page_explains_not_checked_and_check_buttons(settings):
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as client:
        sf = app.state.sf
        a, b, c = seed(sf)
        LeadService(settings, sf, None).rescore()
        with sf() as s:
            assert s.get(Business, a).zip_code.startswith("114")          # rescore filled the missing location
        page = client.get("/leads").text
        assert "not checked" in page and "below 20 reviews" in page and "none found" in page
        assert "Deep-check selected" in page
        r = client.post("/leads/check", data={"ids": [str(a), str(b)]}, follow_redirects=False)
        assert r.status_code == 303
        with sf() as s:
            job = s.get(Job, int(r.headers["location"].rsplit("/", 1)[1]))
            assert job.kind == "enrich" and job.params["ids"] == [a, b]
        r = client.post(f"/leads/{a}/check", follow_redirects=False)
        assert r.headers["location"].startswith("/jobs/")
        assert "Deep check" in client.get(r.headers["location"]).text
        assert "Check this lead" in client.get(f"/leads/{a}").text
        assert "select at least one" in client.post("/leads/check", data={}, follow_redirects=True).text
        # dashboard cards and pipeline tiles are links
        home = client.get("/").text
        for href in ('href="/leads?label=Hot"', 'href="/leads?ads=Active"', 'href="/leads?email=any"',
                     'href="/leads?status=Emailed"'):
            assert href in home
        emailed = client.get("/leads?status=Emailed").text
        assert "Checked Co" in emailed and "Prime Dumpster" not in emailed
        new = client.get("/leads?status=New").text
        assert "Prime Dumpster" in new and "Checked Co" not in new
        assert "area_only" in client.get("/run").text


def test_enrich_job_runs_each_check_and_resumes(settings, session_factory, monkeypatch):
    from leadengine import workers
    from leadengine.service import EmailRunSummary

    calls = []

    async def fake_emails(self, ids, **kw):
        calls.append(("emails", ids))
        return EmailRunSummary(with_email=1)

    async def fake_web(self, ids, **kw):
        calls.append(("website", ids))
        return [{}]

    async def fake_ads(self, ids, **kw):
        calls.append(("ads", ids))
        return [{"name": "A", "status": "Active", "evidence": ["Google search ad live now"]}]

    monkeypatch.setattr(LeadService, "find_emails", fake_emails)
    monkeypatch.setattr(LeadService, "score_websites", fake_web)
    monkeypatch.setattr(LeadService, "detect_ads", fake_ads)
    with session_factory() as s:
        bid = add(s, "A", rating=4.5, review_count=50).id
    handler = workers.make_handlers(settings, session_factory)["enrich"]
    params = {"ids": [bid]}
    job_id = jobs.enqueue(session_factory, "enrich", params)
    ctx = jobs.JobContext(session_factory, job_id, params)
    ctx.step_done("emails")                                  # finished before a crash
    out = asyncio.run(handler(ctx))
    assert [k for k, _ in calls] == ["website", "ads"] and out["ads_active"] == 1 and out["leads"] == 1


def test_scan_scope_all_checks_out_of_zip_businesses(settings, session_factory, monkeypatch):
    """Dense cities: Google returns the whole area, so by default everything that qualifies gets checked."""
    from leadengine.models import BusinessRecord as R
    from leadengine.providers.base import Provider, ProviderResult

    class Fake(Provider):
        name = label = "fake"
        max_per_query = 20

        async def search(self, q):
            return ProviderResult([R(name="Far Away Co", provider="fake", provider_id="f1", lat=40.6946, lng=-73.829,
                                     rating=4.7, review_count=80, website="https://far.com"),
                                   R(name="Near Co", provider="fake", provider_id="n1", lat=40.7506, lng=-73.9972,
                                     rating=4.7, review_count=40, website="https://near.com")], True, 1)

    seen = {}

    async def fake_ads(self, ids, **kw):
        seen.setdefault("ads", []).append(len(ids))
        return []

    monkeypatch.setattr(LeadService, "detect_ads", fake_ads)
    svc = LeadService(settings, session_factory, None)
    svc._providers["fake"] = Fake(settings, None)
    monkeypatch.setattr(LeadService, "_ready", lambda self, n: self._providers["fake"])
    out = asyncio.run(svc.discover("dumpster rental", "10001", provider_name="fake", activity=False, fill=False,
                                   emails=False, website=False, ads=True, refresh=True))
    assert out.shortlisted == 2 and seen["ads"] == [2]
    out = asyncio.run(svc.discover("dumpster rental", "10001", provider_name="fake", activity=False, fill=False,
                                   emails=False, website=False, ads=True, refresh=True, scope="area"))
    assert out.shortlisted == 1


def test_failed_google_check_is_unknown_not_no_ads(settings, session_factory, make_http):
    """Captcha / offline must never be reported as 'no ads' (owner saw Prime Dumpster marked wrongly)."""
    from dataclasses import replace

    import httpx

    from leadengine.config import ProviderSettings

    st = replace(settings, providers={**settings.providers, "playwright": ProviderSettings(
        extra={"base_url": "http://127.0.0.1:9", "max_attempts": 1, "timeout_ms": 3000})},
        sections={**settings.sections, "ads": {"serp_provider": "playwright", "gtm": False}})
    with session_factory() as s:
        b = add(s, "Prime Dumpster LLC", rating=4.6, review_count=31, city="New York", state="NY", zip_code="11418")
        bid = b.id

    async def go():
        async with make_http(lambda r: httpx.Response(404)) as http:
            svc = LeadService(st, session_factory, http)
            try:
                return await svc.detect_ads([bid], keyword="dumpster rental")
            finally:
                await svc.aclose()
    rows = asyncio.run(go())
    assert rows[0]["status"] == "Unknown" and "live Google check failed" in rows[0]["evidence"][0]
    with session_factory() as s:
        assert s.get(Business, bid).ads_status is None
    from leadengine.ui.app import create_app
    app = create_app(st, start_runner=False)
    with TestClient(app) as c:
        assert "check failed" in c.get("/leads").text


def test_sponsored_maps_listing_with_google_ad_link(settings, session_factory):
    """Owner's LoadUp case: the 'Website' of a sponsored listing is google.com/aclk?... ."""
    from leadengine.normalize import unwrap_ad_url

    aclk = ("https://www.google.com/aclk?sa=L&ai=DChsSEwjX&co=1&adurl=https://goloadup.com/dumpster-rental/"
            "%3Futm_source%3Dadwords%26utm_campaign%3DPMAX")
    assert unwrap_ad_url(aclk) == ("https://goloadup.com/dumpster-rental/?utm_source=adwords&utm_campaign=PMAX", True)
    assert unwrap_ad_url("https://www.google.com/aclk?sa=L&ai=X") == (None, True)
    assert unwrap_ad_url("https://loadup.com/")[1] is False
    svc = LeadService(settings, session_factory, None)
    with session_factory() as s:
        repo = Repository(s)
        ranked, _ = svc._store(s, repo, [BusinessRecord(name="LoadUp Junk Removal", provider="t", place_id="L",
                                                        website=aclk, rating=4.7, review_count=946)], "dumpster rental", None)
        s.commit()
        b = ranked[0][0]
        assert b.website.startswith("https://goloadup.com/") and b.domain == "goloadup.com" and b.ads_status == "Active"
        ev = repo.latest_enrichment(b.id, "ads", fresh_only=False).payload["evidence"][0]
        assert "sponsored (paid) listing on Google Maps" in ev
        # an old row saved before the fix is repaired by Re-score
        old = add(s, "Old Row", website="https://www.google.com/aclk?sa=L&adurl=https://oldrow.com/", review_count=50)
        old.website_flags, old.website_score = ["social_or_directory_only"], None
        s.commit()
        oid = old.id
    svc.rescore()
    with session_factory() as s:
        o = s.get(Business, oid)
        assert o.website == "https://oldrow.com/" and o.ads_status == "Active" and "Facebook" not in (o.lead_reason or "")


def test_national_chains_are_skipped(settings, session_factory):
    from leadengine.scoring.opportunity import chain_name

    assert chain_name(Business(name="LoadUp Junk Removal", domain="goloadup.com")) == "loadup"
    assert chain_name(Business(name="Acme", domain="www.1800gotjunk.com")) == "1-800-got-junk"
    assert chain_name(Business(name="Junk King Queens")) == "junk king"
    assert chain_name(Business(name="Kingston Hauling")) is None              # word boundaries, no false match
    assert chain_name(Business(name="X", domain="multi.com"), {"multi.com": 4}).startswith("multi.com has listings")
    with session_factory() as s:
        b = add(s, "LoadUp Junk Removal", website="https://goloadup.com/dumpster-rental/", rating=4.7, review_count=946)
        b.ads_status, b.website_score = "Active", 30
        s.commit()
        bid = b.id
    LeadService(settings, session_factory, None).rescore()
    with session_factory() as s:
        b = s.get(Business, bid)
        assert b.lead_label == "Skip" and "national chain" in b.lead_reason
