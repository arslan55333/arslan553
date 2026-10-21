import asyncio

from fastapi.testclient import TestClient

from leadengine.db import Business, Repository
from leadengine.geo.explorer import hunt_zips
from leadengine.insights import hunt_score
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.service import LeadService


def test_hunt_score_orders_the_best_lead_first():
    ads_no_site = Business(name="A", ads_status="Active", website=None, rating=4.8, review_count=60)
    good_site = Business(name="B", website="https://b.com", website_score=90, rating=4.8, review_count=60)
    fake = Business(name="C", ads_status="Active", spam_score=80)
    s1, why1 = hunt_score(ads_no_site, domain=None, profile={"claimed": False},
                          market={"weakness": 70, "label": "easy"})
    s2, _ = hunt_score(good_site, domain=None, profile=None, market=None)
    assert s1 >= 80 and "runs Google Ads" in why1 and "no real website" in why1 and "easy market" in why1
    assert "Google listing unclaimed" in why1
    assert s2 == 0                                               # good reviews alone: nothing to pitch
    assert hunt_score(fake, domain=None, profile=None, market=None)[0] == 0
    dying = Business(name="D", website="https://d.com", website_score=80)
    sd, whyd = hunt_score(dying, domain={"status": "redemption", "label": "Redemption"}, profile=None, market=None)
    assert sd == 20 and "redemption" in whyd


def test_hunt_zips_picks_one_zip_per_big_town():
    z = hunt_zips("NY", "Queens", 5)
    assert len(z) == 5 and len(set(z)) == 5 and all(len(x) == 5 for x in z)
    assert hunt_zips("NY", None, 3)                              # whole state works too


def test_hunt_pipeline_and_pages(settings, session_factory):
    with session_factory() as s:
        repo = Repository(s)
        rows = []
        for i, (rv, site) in enumerate([(80, None), (45, "https://okhaul.com"), (3, None), (30, None)]):
            b = repo.upsert_business(BusinessRecord(name=f"Corona Haul {i}", provider="t", place_id=f"H{i}",
                                                    review_count=rv, rating=4.7, zip_code="11368", website=site))
            rows.append((b, i + 1))
        repo.record_search(SearchQuery("junk removal", "11368"), "t", rows, exhausted=True, api_calls=0)
        s.commit()
        ids = [b.id for b, _ in rows]

    svc = LeadService(settings, session_factory, None)
    calls = []

    async def fake_market_scan(keyword, zips, **kw):
        calls.append(("market", tuple(zips)))
        return {}

    async def fake_domain_checks(bids, **kw):
        calls.append(("domain", tuple(bids), kw.get("guesses")))
        return []

    async def fake_detect_ads(bids, **kw):
        calls.append(("ads", tuple(bids)))
        with session_factory() as s:
            s.get(Business, ids[0]).ads_status = "Active"
            s.commit()
        return []

    svc.market_scan, svc.domain_checks, svc.detect_ads = fake_market_scan, fake_domain_checks, fake_detect_ads
    found = []
    out = asyncio.run(svc.hunt("junk removal", ["11368"], on_found=found.extend))
    assert out["markets"] == 1 and out["checked"] == 3          # the 3-review listing is filtered out
    assert calls[1][2] is False                                   # no name guessing in bulk hunts
    names = [r["name"] for r in out["leads"]]
    assert names[0] == "Corona Haul 0" and "Corona Haul 2" not in names
    assert "runs Google Ads" in out["leads"][0]["why"] and found

    from leadengine import jobs
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        sf = app.state.sf
        job_id = jobs.enqueue(sf, "hunt", {"keyword": "junk removal", "zips": ["11368"], "state": "NY",
                                           "county": "Queens"})
        with sf() as s:
            from leadengine.db import Job
            j = s.get(Job, job_id)
            j.status, j.result = "done", {**out, "leads": [{**r} for r in out["leads"]]}
            s.commit()
        page = c.get("/hunt?state=NY&county=Queens").text
        assert "Lead Hunter" in page and "Corona Haul 0" in page and "runs Google Ads" in page
        assert "Queens County, NY" in page
        r = c.post("/hunt/run", data={"keyword": "junk removal", "state": "NY", "county": "Queens", "max_zips": "3",
                                      "ads": "true"}, follow_redirects=False)
        assert r.status_code == 303
        with sf() as s:
            j = s.get(Job, int(r.headers["location"].rsplit("/", 1)[1]))
            assert j.kind == "hunt" and len(j.params["zips"]) == 3 and j.params["ads"] is True
        assert c.post("/hunt/run", data={"keyword": "x"}).status_code == 400
        assert "🎯 Hunt" in c.get("/").text


def test_market_scan_retries_an_empty_search(settings, session_factory):
    from types import SimpleNamespace

    svc = LeadService(settings, session_factory, None)
    seen = []

    async def fake_search(query, provider_name=None, *, refresh=False):
        seen.append(refresh)
        return SimpleNamespace(results=[] if len(seen) == 1 else [])

    svc.search = fake_search
    asyncio.run(svc.market_scan("junk removal", ["11372"]))
    assert seen == [False, True]                                 # one forced retry, not a loop
