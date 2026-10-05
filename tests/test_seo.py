import asyncio
from datetime import date

import httpx

from leadengine.db import Business, Repository
from leadengine.enrich.seo import combine, gbp_checks, onpage_checks
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.service import LeadService

GOOD = """<html><head><title>Dumpster Rental in Dallas, TX | Acme Roll-Off</title>
<meta name="description" content="Same-day dumpster rental in Dallas.">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"LocalBusiness","name":"Acme",
"telephone":"(214) 555-0100"}]}</script></head><body>
<h1>Dumpster Rental in Dallas</h1><a href="/dumpster-rental">Dumpster rental</a><a href="/junk-removal">Junk removal</a>
<a href="/construction-dumpster-service">Construction service</a><a href="/plano-dumpster-rental">Plano</a>
<a href="/irving">Irving</a><a href="https://www.google.com/maps/place/acme">Directions</a>
<p>Call (214) 555-0100. Read what our customers say: 500 reviews.</p><p>""" + "word " * 320 + """</p>
<img src="a.jpg" alt="dumpster"></body></html>"""
BAD = """<html><head><title>Home</title><meta name="robots" content="noindex"></head><body><p>Welcome!</p>
<img src="a.jpg"><img src="b.jpg"></body></html>"""


def test_onpage_good_vs_bad():
    frac, issues, pos, facts = onpage_checks(GOOD, "https://acme.com/", city="Dallas", service="dumpster rental",
                                             phone="2145550100", towns=["Plano", "Irving"])
    assert frac > 0.95 and not issues and facts["local_schema"] and facts["service_pages"] >= 3 and facts["area_pages"] == 2
    frac2, issues2, _, _ = onpage_checks(BAD, "https://bad.com/", city="Dallas", service="dumpster rental",
                                         phone="2145550100", towns=["Plano"])
    text = " | ".join(t for _, t in issues2)
    assert frac2 < 0.2 and "noindex" in text and "LocalBusiness schema" in text and "doesn't mention Dallas" in text
    assert "phone number on the site doesn't match" in text and "only 0 service page" in text


def test_gbp_checks_against_competition():
    b = Business(name="Acme", claimed=False, photo_count=4, review_count=31, owner_response_rate=0.1,
                 recent_review_dates=["2026-09-20", "2026-01-01"], categories=["Dumpster rental service"], hours=None,
                 website="https://acme.com")
    frac, issues, _, facts = gbp_checks(b, [{"review_count": 240}, {"review_count": 300}, {"review_count": 90}],
                                        today=date(2026, 10, 1))
    text = " | ".join(t for _, t in issues)
    assert facts["competitor_median_reviews"] == 240 and "31 reviews vs about 240" in text
    assert "not claimed" in text and "only 4 photos" in text and "only 1 new review" in text and frac < 0.3
    assert combine(0.9, 0.5, None) == round(100 * (0.55 * 0.9 + 0.35 * 0.5) / 0.9)
    assert combine(None, None, None) is None


def test_seo_audits_service(settings, session_factory, make_http):
    with session_factory() as s:
        repo = Repository(s)
        me = repo.upsert_business(BusinessRecord(name="Acme", provider="t", place_id="A", website="https://acme.com/",
                                                 phone="(214) 555-0100", city="Dallas", state="TX", zip_code="75201",
                                                 review_count=31, categories=["Dumpster rental service", "Junk removal"]))
        rival = repo.upsert_business(BusinessRecord(name="Rival", provider="t", place_id="R", review_count=400))
        repo.record_search(SearchQuery("dumpster rental", "75201"), "t", [(rival, 1), (me, 2)], exhausted=True, api_calls=1)
        s.commit()
        mid = me.id

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text=BAD, headers={"content-type": "text/html"})
        return httpx.Response(404)

    async def go():
        async with make_http(handler) as http:
            svc = LeadService(settings, session_factory, http)
            first = await svc.seo_audits([mid])
            second = await svc.seo_audits([mid])            # cached
            return first, second
    first, second = asyncio.run(go())
    out = first[0]
    assert out["score"] < 40 and any("400" in i or "vs about" in i for i in out["issues"])
    assert any("sitemap" in i for i in out["issues"]) and second[0]["score"] == out["score"]
    with session_factory() as s:
        assert s.get(Business, mid).seo_score == out["score"]
