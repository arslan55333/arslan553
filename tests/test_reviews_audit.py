import asyncio
from dataclasses import replace
from datetime import date, datetime

import httpx
from fastapi.testclient import TestClient

from leadengine.db import Business, Repository
from leadengine.enrich.ads.status import decide
from leadengine.enrich.ads.transparency import name_similarity, parse_creatives, parse_suggestions
from leadengine.enrich.reviews import analyze, review_date, suggested_reply, themes_of
from leadengine.models import BusinessRecord
from leadengine.outreach.facts import review_line
from leadengine.service import LeadService

NOW = datetime(2026, 10, 7)
LOWEST = [
    {"rating": 1, "date": "6 days ago", "text": "Two weeks no pickup, I called every day.", "author": "Sudesh K", "owner_response": False},
    {"rating": 1, "date": "a month ago", "text": "Rude worker, yelled at my mother.", "author": "Sean P", "owner_response": False},
    {"rating": 2, "date": "3 months ago", "text": "They overcharged me $200 more than quoted.", "author": "Ana", "owner_response": True},
    {"rating": 3, "date": "5 months ago", "text": "OK but late again", "author": "Bo", "owner_response": False},
]
NEWEST = [
    {"rating": 5, "date": "2 days ago", "text": "Great", "author": "A", "owner_response": False},
    {"rating": 5, "date": "3 weeks ago", "text": "Fast", "author": "B", "owner_response": False},
    {"rating": 4, "date": "2 months ago", "text": "Good", "author": "C", "owner_response": True},
    LOWEST[0],
]


def test_analyze_finds_unanswered_negatives_themes_and_speed():
    a = analyze(newest=NEWEST, lowest=LOWEST, histogram={"5": 300, "4": 40, "3": 10, "2": 6, "1": 14},
                rating=4.4, total=370, phone="(718) 555-0100", now=NOW)
    assert a["negative"] == 20 and a["negative_exact"] and a["negative_share"] == 5.4
    assert [u["author"] for u in a["unanswered_negative"]] == ["Sudesh K", "Sean P"]
    assert "(718) 555-0100" in a["unanswered_negative"][0]["reply"]
    assert a["reply_rate"] == 25 and a["negative_reply_rate"] == 33 and a["last_review_days"] == 2
    assert {t["theme"] for t in a["themes"]} >= {"late / no-show", "rude / unprofessional", "price / overcharged"}
    assert a["score"] < 60 and any("no reply from the owner" in i for i in a["issues"])
    assert "2 negative Google reviews have no reply" in review_line(a)


def test_reviews_without_histogram_and_serpapi_dates():
    a = analyze(newest=[{"rating": 5, "iso_date": "2026-10-01T10:00:00Z", "text": "x"}],
                lowest=[{"rating": 1, "text": "scam, they lied", "owner_response": True}], histogram=None,
                rating=4.9, total=12, now=NOW)
    assert a["negative"] == 1 and not a["unanswered_negative"] and a["last_review_days"] == 5
    assert any("only 12 reviews" in i for i in a["issues"])
    assert review_date({"date": "Edited a year ago"}, NOW).year == 2025
    assert themes_of("They never showed up") == ["late / no-show"]
    assert suggested_reply({"author": "Mary Lou", "text": "damaged my driveway"}, None).startswith("Hi Mary, we're sorry about the damage")


def test_review_audits_service_serpapi_fallback(settings, session_factory, make_http, credits):
    with session_factory() as s:
        b = Repository(s).upsert_business(BusinessRecord(name="Royal", provider="t", place_id="ChIJx", data_id="0x1:0x2",
                                                         rating=4.4, review_count=810, phone="7185550100"))
        s.commit()
        bid = b.id
    calls = []

    def handler(request):
        p = dict(request.url.params)
        calls.append(p.get("sort_by"))
        reviews = [{"rating": 1, "date": "6 days ago", "snippet": "Rude worker", "user": {"name": "Sean"}}] \
            if p["sort_by"] == "ratingLow" else [{"rating": 5, "iso_date": "2026-10-05T00:00:00Z", "snippet": "Great",
                                                   "user": {"name": "Al"}, "response": {"snippet": "Thanks!"}}]
        return httpx.Response(200, json={"reviews": reviews, "topics": [{"keyword": "pickups", "mentions": 17}]})

    async def go():
        async with make_http(handler) as http:
            svc = LeadService(replace(settings, sections={**settings.sections, "reviews": {"ai_summary": False}}),
                              session_factory, http, credits)
            first = await svc.review_audits([bid])
            again = await svc.review_audits([bid])
            return first, again
    first, again = asyncio.run(go())
    assert calls == ["ratingLow", "newestFirst"] and again[0]["cached"]
    r = first[0]
    assert r["source"] == "serpapi" and len(r["unanswered_negative"]) == 1 and r["google_topics"][0]["keyword"] == "pickups"
    with session_factory() as s:
        assert s.get(Business, bid).reputation_score == r["score"]
    assert sum(u.calls_month for u in credits.summary() if u.provider == "serpapi") == 2


def test_lead_page_and_report_show_reviews(settings):
    from leadengine.report.builder import ReportBuilder  # noqa: F401
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        sf = app.state.sf
        with sf() as s:
            repo = Repository(s)
            b = repo.upsert_business(BusinessRecord(name="Royal", provider="t", place_id="R1", phone="7185550100"))
            s.commit()
            audit = analyze(newest=NEWEST, lowest=LOWEST, histogram={"5": 30, "1": 3}, rating=4.4, total=33,
                            phone="7185550100", now=NOW)
            repo.set_enrichment(b.id, "reviews", audit, ttl_days=14, source="browser")
            s.commit()
            bid = b.id
        page = c.get(f"/leads/{bid}").text
        assert "Google reviews audit" in page and "Two weeks no pickup" in page and "Hi Sudesh" in page
        assert c.post(f"/leads/{bid}/reviews", follow_redirects=False).status_code == 303


def test_transparency_name_lookup_parsing_and_verdict():
    data = {"1": [{"1": {"1": "Royal Waste Services Inc.", "2": "AR11111111111111111111", "3": "US",
                         "4": {"2": {"1": "10", "2": "50"}}}},
                  {"1": {"1": "Royal Plumbing LLC", "2": "AR22222222222222222222", "3": "US"}},
                  {"2": {"1": "royalwaste.com"}}]}
    cands = parse_suggestions(data)
    assert [c["id"][:4] for c in cands] == ["AR11", "AR22"] and cands[0]["ads"] == 50
    assert name_similarity("Royal Waste Services", "Royal Waste Services Inc.") == 1.0
    assert name_similarity("Royal Waste Services", "Royal Plumbing LLC") < 0.85
    cr = parse_creatives({"1": [{"6": {"1": "1700000000"}, "7": {"1": "1790000000"}}, {"7": {"1": "1780000000"}}]},
                         date(2026, 10, 7))
    assert cr["creatives"] == 2 and cr["last_shown"] == "2026-09-21" and cr["first_shown"] == "2023-11-14"
    v = decide(serp_hits=[], serp_checked=True, site=None, maps_sponsored=False, today=date(2026, 10, 7),
               transparency={"advertiser": "Royal Waste Services Inc.", "by_name": True, "creatives": 50,
                             "last_shown": None})
    assert v.status == "Likely" and "found by name" in v.evidence[0]
    v2 = decide(serp_hits=[], serp_checked=True, site=None, maps_sponsored=False, today=date(2026, 10, 7),
                transparency={"advertiser": "X", "by_name": True, "creatives": 2, "last_shown": "2026-09-30"})
    assert v2.status == "Active"
