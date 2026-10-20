import asyncio
import json
from datetime import timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from leadengine.db import Business, Repository
from leadengine.db.models import utcnow
from leadengine.enrich.profile import parse_serp_place, profile_audit, rescue_score
from leadengine.models import BusinessRecord
from leadengine.service import LeadService

ROYAL = json.loads((Path(__file__).parent / "data" / "serp_place_royal.json").read_text())


def test_parse_real_serpapi_place():
    p = parse_serp_place(ROYAL)
    assert p["claimed"] is True and p["histogram"][1] == 113 and p["histogram"][5] == 683
    assert "Wheelchair accessible entrance" in p["attributes"] and p["hours"] and p["website"]
    unclaimed = parse_serp_place({**ROYAL, "unclaimed_listing": True, "posts": [{"date": "3 weeks ago"}]})
    assert unclaimed["claimed"] is False and unclaimed["posts"] == 1 and unclaimed["last_post_days"] == 21
    a = profile_audit(unclaimed, photo_count=4)
    assert a["issues"][0].startswith("the Google listing is NOT claimed") and a["score"] < 70


def test_audit_skips_unknown_fields_from_browser():
    a = profile_audit({"claimed": True, "categories": ["Plumber", "Drain service"], "hours": True, "website": "x",
                       "phone": "1", "description": None, "posts": None, "attributes": None})
    assert a["score"] == 100 and not a["issues"]


def test_rescue_score():
    b = Business(name="A", rating=4.7, review_count=90, website="https://a.com", last_review_at=utcnow() - timedelta(days=5))
    s, why = rescue_score(b, domain={"status": "expired_grace"}, profile={"claimed": False})
    assert s == 100 and "domain expired" in why and "listing unclaimed" in why
    fake = Business(name="F", spam_score=90)
    assert rescue_score(fake, domain={"status": "available"}, profile=None)[0] == 0


def test_place_profiles_service_and_rescue_ui(settings):
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        sf = app.state.sf
        with sf() as s:
            b = Repository(s).upsert_business(BusinessRecord(name="Royal Waste Services", provider="t", place_id="ChIJroyal",
                                                             rating=4.4, review_count=811, phone="7185262623"))
            s.commit()
            bid = b.id
        calls = []

        def handler(request):
            calls.append(request.url.params.get("place_id"))
            return httpx.Response(200, json={"place_results": {**ROYAL, "unclaimed_listing": True}})

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as raw:
                from leadengine.http import HttpClient
                http = HttpClient(settings.http, transport=httpx.MockTransport(handler))
                try:
                    return await LeadService(settings, sf, http).place_profiles([bid])
                finally:
                    await http.aclose()
        rows = asyncio.run(go())
        assert calls == ["ChIJroyal"] and rows[0]["claimed"] is False
        with sf() as s:
            assert s.get(Business, bid).claimed is False and s.get(Business, bid).profile_score < 80
        page = c.get("/rescue").text
        assert "Royal Waste Services" in page and "Unclaimed listing" in page and "claim kit" in page
        kit = c.get(f"/leads/{bid}/claim-kit").text
        assert "Own this business?" in kit and "Manager" in kit
        lead = c.get(f"/leads/{bid}").text
        assert "NOT claimed" in lead and "Claim kit for the owner" in lead
        from leadengine.outreach.compose import template_drafts
        from leadengine.outreach.facts import gather
        with sf() as s:
            body = template_drafts(gather(s, s.get(Business, bid), {}, {}), {})["variants"][1]["body"]
        assert "isn't claimed" in body
