import asyncio
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient

from leadengine.db import Business, Repository
from leadengine.db.models import Enrichment, Watch, utcnow
from leadengine.enrich.firecrawl import Firecrawl
from leadengine.enrich.sitecrawl import check_page, content_gap, crawl_site, looks_js_built, summarize, topic
from leadengine.enrich.website.agency import detect_agency
from leadengine.insights import rank_history
from leadengine.models import BusinessRecord
from leadengine.scoring.targets import is_new_business
from leadengine.service import LeadService

GOOD = ("<html><head><title>{t}</title><meta name='description' content='x'><link rel='canonical' href='/'>"
        "</head><body><h1>Junk removal</h1><p>" + "word " * 400 + "</p></body></html>")
SPA = "<html><head><title>Ant's</title><script src=a.js></script></head><body><div id=root></div></body></html>"


def test_page_checks_and_summary():
    p = check_page("<html><head><title>Hi</title><meta name='robots' content='noindex,follow'></head><body><img src=a>"
                   "<h1>a</h1><h1>b</h1>short</body></html>", "https://a.com/")
    assert p["noindex"] and p["h1"] == 2 and not p["meta"] and p["img_no_alt"] == 1 and p["words"] < 10
    s = summarize([{"url": "u1", "status": 200, "ms": 100, **p}, {"url": "u2", "status": 404}])
    text = " | ".join(s["issues"])
    assert "1 broken page" in text and "noindex" in text and "more than one H1" in text and s["score"] < 80


def test_topics_and_content_gap():
    assert topic("https://a.com/services/estate-cleanout/") == "estate cleanout"
    assert topic("https://a.com/contact-us") is None and topic("https://a.com/blog/5-tips") is None
    gap = content_gap(["https://me.com/junk-removal", "https://me.com/corona"],
                      {"A": ["https://a.com/estate-cleanout", "https://a.com/astoria-junk-removal", "https://a.com/corona",
                             "https://a.com/christmas-day-hours"],
                       "B": ["https://b.com/estate-cleanout", "https://b.com/piano-removal"]}, ["Astoria", "Corona"])
    assert [r["topic"] for r in gap["missing_services"]] == ["estate cleanout", "piano removal"]
    assert gap["missing_services"][0]["competitors"] == 2 and [r["topic"] for r in gap["missing_towns"]] == ["astoria junk removal"]


def test_crawl_renders_javascript_sites_through_firecrawl(make_http):
    import json

    def handler(request):
        if request.url.host == "api.firecrawl.dev":
            body = json.loads(request.content)
            if request.url.path == "/v1/map":
                return httpx.Response(200, json={"success": True, "links": [f"https://ants.com/p{i}" for i in range(4)]})
            return httpx.Response(200, json={"success": True, "data": {"rawHtml": GOOD.format(t=body["url"]),
                                                                       "metadata": {"statusCode": 200, "creditsUsed": 1}}})
        return httpx.Response(200, text=SPA, headers={"content-type": "text/html"})

    async def go():
        async with make_http(handler) as http:
            plain = await crawl_site(http, "https://ants.com", urls=[f"https://ants.com/p{i}" for i in range(4)])
            fc = Firecrawl("fc-test", http, mode="smart")
            rendered = await crawl_site(http, "https://ants.com", firecrawl=fc)
            return plain, rendered, fc.used
    plain, rendered, used = asyncio.run(go())
    assert plain["js_site"] and not plain["rendered"] and "built with JavaScript" in plain["issues"][0]
    assert rendered["rendered"] and rendered["source"] == "firecrawl" and used == 5     # 1 map + 4 pages
    assert all(p["h1"] == 1 for p in rendered["pages"]) and not looks_js_built(rendered["pages"])


def test_agency_and_new_business():
    assert detect_agency("<footer>Website Design by Acme Digital Co. | Privacy</footer>")["name"] == "Acme Digital Co"
    assert detect_agency("<footer>Powered by Wix.com</footer>")["kind"] == "builder"
    assert detect_agency('<link href="https://x.scorpioncms.com/a.css">')["name"] == "Scorpion"
    assert detect_agency("<footer>Serving NYC by appointment</footer>") is None
    assert is_new_business(Business(name="a", review_count=3, website=None))
    assert not is_new_business(Business(name="b", review_count=300, website=None))
    young = Business(name="c", review_count=4, website="https://c.com", website_flags=[])
    assert is_new_business(young, {"wayback": {"first_year": 2026}}, this_year=2026)
    assert not is_new_business(young, {"wayback": {"first_year": 2015}}, this_year=2026)


def test_rank_history_and_rank_watch_alert(settings, session_factory, monkeypatch):
    with session_factory() as s:
        b = Repository(s).upsert_business(BusinessRecord(name="Corona Junk", provider="t", place_id="C"))
        s.commit()
        bid = b.id
        s.add(Enrichment(business_id=bid, kind="rank", payload={"keyword": "junk removal", "solv": 40, "avg_rank": 4.0,
                                                                 "top3": 20, "points": 49},
                         fetched_at=utcnow() - timedelta(days=7)))
        s.add(Watch(kind="rank", keyword="junk removal", business_id=bid, locations=[], every_days=7, active=True))
        s.commit()
    svc = LeadService(settings, session_factory, None)

    async def fake_grid(keyword, *, business_id=None, **kw):
        with session_factory() as s:
            Repository(s).set_enrichment(business_id, "rank", {"keyword": keyword, "solv": 18, "avg_rank": 7.5,
                                                               "top3": 9, "points": 49})
            s.commit()
        return {}
    monkeypatch.setattr(svc, "rank_grid", fake_grid)
    out = asyncio.run(svc.run_watches())
    assert out["alerts"] == 1
    with session_factory() as s:
        hist = rank_history(s, bid)
        assert [h["solv"] for h in hist] == [40, 18] and hist[-1]["solv_change"] == -22 and hist[-1]["rank_change"] == -3.5
        from sqlalchemy import select
        from leadengine.db.models import Alert
        a = s.scalars(select(Alert)).one()
        assert a.kind == "rank_drop" and "dropped from 40% to 18%" in a.message


def test_ui_site_audit_card_track_rank_and_new_filter(settings):
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        sf = app.state.sf
        with sf() as s:
            repo = Repository(s)
            b = repo.upsert_business(BusinessRecord(name="Fresh Haul", provider="t", place_id="F", review_count=2,
                                                    website="https://fresh.com"))
            s.commit()
            repo.set_enrichment(b.id, "site_audit", {"score": 61, "checked": 3, "pages_known": 3, "source": "sitemap",
                                                     "broken": 1, "rendered": False, "issues": ["1 broken page(s)"],
                                                     "pages": [{"url": "https://fresh.com/x", "status": 404}],
                                                     "content_gap": {"missing_services": [{"topic": "estate cleanout",
                                                                     "competitors": 2, "example": "https://a.com/e"}],
                                                                     "missing_towns": [], "competitors_checked": 2}})
            repo.set_enrichment(b.id, "rank", {"keyword": "junk removal", "solv": 10, "avg_rank": 9, "top3": 5,
                                               "points": 49, "found": 30, "grid_id": None})
            repo.set_enrichment(b.id, "website", {"wayback": {"first_year": utcnow().year}, "flags": []})
            s.commit()
            bid = b.id
        page = c.get(f"/leads/{bid}").text
        assert "Full site audit" in page and "estate cleanout" in page and "Track weekly" in page
        c.post(f"/leads/{bid}/track-rank", data={"keyword": "junk removal"})
        assert "Tracked weekly" in c.get(f"/leads/{bid}").text
        assert "Fresh Haul" in c.get("/leads?new=1").text and "New businesses: 1" in c.get("/").text
        assert "rank map" in c.get("/alerts").text
