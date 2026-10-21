import asyncio
from datetime import datetime

import httpx
from fastapi.testclient import TestClient

from leadengine.db import Business, Repository
from leadengine.enrich.domains import check_domain, classify, name_guesses, on_builder, read_page, registrable
from leadengine.models import BusinessRecord
from leadengine.outreach.facts import domain_line
from leadengine.service import LeadService

NOW = datetime(2026, 10, 20)


def rdap(status=(), exp="2027-01-01T00:00:00Z", reg="2015-01-01T00:00:00Z", related=None):
    d = {"status": list(status), "events": [{"eventAction": "registration", "eventDate": reg},
                                            {"eventAction": "expiration", "eventDate": exp}],
         "entities": [{"roles": ["registrar"], "vcardArray": ["vcard", [["fn", {}, "text", "GoDaddy"]]]}]}
    if related:
        d["links"] = [{"rel": "related", "href": related}]
    return d


def test_classify_rules():
    page_ok = {"loads": True}
    assert classify(registered=False, statuses=[], expires=None, registered_at=None, page=None) == "available"
    assert classify(registered=None, statuses=[], expires=None, registered_at=None, page=None) == "unknown"
    for st, want in (("client hold", "expired_grace"), ("redemption period", "redemption"),
                     ("pending delete", "pending_delete")):
        assert classify(registered=True, statuses=[st], expires=None, registered_at=None, page=page_ok, now=NOW) == want
    assert classify(registered=True, statuses=[], expires=datetime(2026, 10, 1), registered_at=None, page=page_ok,
                    now=NOW) == "expired_grace"
    assert classify(registered=True, statuses=[], expires=datetime(2026, 11, 30), registered_at=None, page=page_ok,
                    now=NOW) == "expiring_soon"
    assert classify(registered=True, statuses=[], expires=datetime(2028, 1, 1), registered_at=None,
                    page=read_page(200, "https://x.com", "<p>This domain is for sale! Make an offer on this domain</p>"),
                    now=NOW) == "for_sale"
    assert classify(registered=True, statuses=[], expires=datetime(2028, 1, 1), registered_at=None,
                    page=read_page(403, "https://x.com", "Attention required"), now=NOW) == "ok"     # Cloudflare
    assert classify(registered=True, statuses=[], expires=datetime(2028, 1, 1), registered_at=None,
                    page={"loads": False}, now=NOW) == "site_down"


def test_helpers():
    assert name_guesses("Ant's Junk Removal LLC", "Queens")[:3] == ["antsjunkremoval.com", "ants-junk-removal.com",
                                                                     "antsjunkremovalqueens.com"]
    assert registrable("www.shop.acme.com") == "acme.com" and registrable("a.b.co.uk") == "b.co.uk"
    assert on_builder("action-pro.ueniweb.com") == "ueniweb.com" and on_builder("acme.com") is None
    assert "renew it until about 2026-12-15" in domain_line({"status": "expired_grace", "domain": "a.com",
                                                             "owner_can_renew_until": "2026-12-15"})
    assert domain_line({"status": "ok"}) is None


def handler_for(state):
    def handler(request):
        url = str(request.url)
        if url.startswith("https://rdap.org/domain/free.com"):
            return httpx.Response(302, headers={"location": "https://rdap.verisign.com/com/v1/domain/free.com"})
        if "rdap.verisign.com/com/v1/domain/free.com" in url:
            return httpx.Response(404)
        if url.startswith("https://rdap.org/domain/odd.us"):
            return httpx.Response(404)                  # rdap.org doesn't know .us: no redirect
        if url.startswith("https://rdap.org/domain/dead.com"):
            return httpx.Response(200, json=rdap(exp="2027-10-01T00:00:00Z", related="https://rdap.reg.com/dead.com"))
        if url.startswith("https://rdap.reg.com/dead.com"):   # registrar: owner only paid until Oct 1 2026
            return httpx.Response(200, json=rdap(status=["client hold"], exp="2026-10-01T00:00:00Z"))
        if "web.archive.org/cdx" in url:
            return httpx.Response(200, json=[["timestamp"], ["20190501000000"], ["20260801000000"]])
        if "dead.com" in url:
            return httpx.Response(200, text="<h1>This domain has expired. Renew this domain now</h1>")
        return httpx.Response(404)
    return handler


def test_check_domain_end_to_end(make_http, monkeypatch):
    from leadengine.enrich import domains

    async def no_whois(domain, **kw):
        return None                                    # never reach a real WHOIS server from tests (port 43)

    monkeypatch.setattr(domains, "whois_registered", no_whois)

    async def go():
        async with make_http(handler_for({})) as http:
            return (await check_domain(http, "dead.com", now=NOW), await check_domain(http, "free.com", now=NOW),
                    await check_domain(http, "odd.us", now=NOW), await check_domain(http, "x.ueniweb.com", now=NOW))
    dead, free, odd, builder = asyncio.run(go())
    assert dead["status"] == "expired_grace" and dead["expires"] == "2026-10-01" and dead["registrar"] == "GoDaddy"
    assert dead["owner_can_renew_until"] == "2026-12-15" and dead["wayback"]["first_year"] == 2019
    assert free["status"] == "available" and odd["status"] == "unknown" and builder["status"] == "no_own_domain"


def test_service_rescue_page_and_draft(settings, session_factory, make_http):
    with session_factory() as s:
        repo = Repository(s)
        b = repo.upsert_business(BusinessRecord(name="Dead Haul", provider="t", place_id="D", website="https://dead.com",
                                                review_count=80, rating=4.8, phone="7185550100"))
        s.commit()
        bid = b.id

    async def go():
        async with make_http(handler_for({})) as http:
            return await LeadService(settings, session_factory, http).domain_checks([bid])
    rows = asyncio.run(go())
    assert rows[0]["status"] == "expired_grace"
    with session_factory() as s:
        assert s.get(Business, bid).domain_status == "expired_grace"
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        sf = app.state.sf
        with sf() as s:
            repo = Repository(s)
            b2 = repo.upsert_business(BusinessRecord(name="Dead Haul", provider="t", place_id="D2", website="https://dead.com",
                                                     review_count=80, rating=4.8))
            s.commit()
            repo.set_enrichment(b2.id, "domain", rows[0], ttl_days=7)
            s.commit()
            bid2 = b2.id
        page = c.get("/rescue").text
        assert "Dead Haul" in page and "Expired — grace period" in page and "2026-12-15" in page
        assert "Expired — grace period" in c.get(f"/leads/{bid2}").text
        c.post("/rescue/monitor")
        assert "Weekly domain monitor on" in c.get("/rescue").text
        from leadengine.outreach.compose import template_drafts
        from leadengine.outreach.facts import gather
        with sf() as s:
            f = gather(s, s.get(Business, bid2), {}, {})
        assert "is offline because the domain expired" in template_drafts(f, {})["variants"][0]["body"]
