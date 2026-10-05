import asyncio

from fastapi.testclient import TestClient

from leadengine.db import Business, Repository
from leadengine.db.models import RankGrid
from leadengine.geo.rankgrid import grid_points, summarize
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.outreach import compose, facts as ofacts
from leadengine.service import LeadService


def seed(s, tmp_path):
    repo = Repository(s)
    me = repo.upsert_business(BusinessRecord(name="Acme Dumpsters", provider="t", place_id="A", website="https://acme.com",
                                             phone="2125550100", city="New York", state="NY", rating=4.6, review_count=31,
                                             lat=40.75, lng=-73.99))
    rival = repo.upsert_business(BusinessRecord(name="Rival Roll-Off", provider="t", place_id="R", rating=4.9,
                                                review_count=400, lat=40.751, lng=-73.991))
    s.flush()
    me.website_score, me.ads_status, me.best_email = 24, "Active", "owner@acme.com"
    rival.website_score = 88
    shot = tmp_path / "w.jpg"
    shot.write_bytes(b"\xff\xd8\xffjpeg")
    repo.set_enrichment(me.id, "website", {"score": 24, "grade": "F", "screenshot": str(shot),
                                           "reasons": ["slow on mobile (PageSpeed 19/100, loads in 9.0s)"],
                                           "pagespeed": {"performance": 19}})
    repo.set_enrichment(me.id, "ads", {"status": "Active", "evidence": ["seen in 5 of 6 Google searches"]})
    repo.set_enrichment(me.id, "landing", {"score": 28, "url": "https://acme.com/", "final_url": "https://acme.com/",
                                           "issues": ["no tap-to-call button for people coming from the ad on a phone"]})
    repo.set_enrichment(me.id, "seo", {"score": 33, "issues": ["no LocalBusiness schema markup"], "positives": ["has an H1"]})
    pts = [dict(p, ranks=([rival.id, me.id] if i == 4 else [rival.id])) for i, p in enumerate(grid_points(40.75, -73.99, 3, 1))]
    grid = RankGrid(keyword="dumpster rental", center_lat=40.75, center_lng=-73.99, size=3, spacing_km=1, points=pts,
                    summary=summarize(pts, {me.id: me.name, rival.id: rival.name}))
    s.add(grid)
    s.flush()
    my = next(r for r in grid.summary if r["business_id"] == me.id)
    repo.set_enrichment(me.id, "rank", {**my, "grid_id": grid.id, "keyword": "dumpster rental"})
    s.commit()
    return me.id


def test_report_contains_every_finding(settings, session_factory, tmp_path):
    with session_factory() as s:
        bid = seed(s, tmp_path)
    rows = asyncio.run(LeadService(settings, session_factory, None).build_reports([bid], deploy=False))
    r = rows[0]
    html = open(r["path"], encoding="utf-8").read()
    for text in ("Online visibility audit", "Acme Dumpsters", "Paid clicks: no tap-to-call", "scores 19/100 on Google",
                 "In the top 3 at 1 of 9 spots", "<svg", "Rival Roll-Off", "no LocalBusiness schema",
                 'content="noindex', "Not affiliated with or endorsed by Google", "img/website.jpg"):
        assert text in html, text
    assert (tmp_path / "data" / "reports" / r["slug"] / "img" / "website.jpg").exists() or \
        (settings.root / "data" / "reports" / r["slug"] / "img" / "website.jpg").exists()
    assert "Disallow: /" in open(r["path"].replace("index.html", "robots.txt")).read()
    with session_factory() as s:
        assert Repository(s).latest_enrichment(bid, "audit", fresh_only=False).payload["slug"] == r["slug"]


def test_drafts_mention_map_and_audit(settings, session_factory, tmp_path):
    with session_factory() as s:
        bid = seed(s, tmp_path)
        Repository(s).set_enrichment(bid, "audit", {"url": "https://acme-new-york-1-audit.example.app", "slug": "x"})
        s.commit()
        f = ofacts.gather(s, s.get(Business, bid), {"followup_days": [3]}, {})
    assert f.audit_url and f.map_points == 9 and f.map_top3 == 1 and f.landing_issues
    d = asyncio.run(compose.make_drafts(f, {"followup_days": [3]}, None))
    detailed = d["variants"][1]["body"]
    assert "shows in the top 3 at 1 of them" in detailed and "https://acme-new-york-1-audit.example.app" in detailed
    assert "page those ads send people to has a problem" in detailed
    assert detailed.rstrip().endswith("?")                          # the question still closes the email
    assert "audit" not in d["variants"][0]["body"]                   # short stays short


def test_lead_page_report_button(settings):
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        with app.state.sf() as s:
            b = Repository(s).upsert_business(BusinessRecord(name="Acme", provider="t", place_id="A"))
            s.commit()
            bid = b.id
        assert "Build audit report" in c.get(f"/leads/{bid}").text
        r = c.post(f"/leads/{bid}/audit", follow_redirects=False)
        assert r.status_code == 303 and "Audit reports" in c.get(r.headers["location"]).text
