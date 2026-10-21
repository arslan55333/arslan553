import asyncio
from datetime import date

import httpx
from fastapi.testclient import TestClient

from leadengine.db import Business
from leadengine.db.models import Registration
from leadengine.radar import SOURCES, fetch_new, guess_contact, normalize_row, search_words, soql
from leadengine.service import LeadService


def test_words_and_query():
    assert search_words("junk removal") == ["JUNK", "HAULING", "CLEANOUT", "DEBRIS", "RUBBISH"]
    assert "CLEAN" not in search_words("junk removal") and search_words("house cleaning")[0] == "CLEANING"
    q = soql(SOURCES["OR"], ["JUNK"], date(2026, 9, 1), 50)
    assert "upper(business_name) like '%JUNK%'" in q["$where"] and "registry_date > '2026-09-01'" in q["$where"]
    assert "PRINCIPAL PLACE OF BUSINESS" in q["$where"] and q["$order"] == "registry_date DESC"


def test_normalize_rows():
    ny = normalize_row("NY", SOURCES["NY"], {"dos_id": "8048899", "current_entity_name": "TONYS JUNK REMOVAL LLC",
                                            "initial_dos_filing_date": "2026-10-17T00:00:00.000", "county": "Suffolk",
                                            "dos_process_city": "Miller Place", "dos_process_zip": "11764",
                                            "dos_process_address_1": "12 Oak St", "dos_process_name": "ANTHONY R"})
    assert ny["name"] == "Tonys Junk Removal Llc" and ny["filed"] == "2026-10-17" and ny["zip"] == "11764"
    assert ny["contact"] == "ANTHONY R" and ny["county"] == "Suffolk"
    ct = normalize_row("CT", SOURCES["CT"], {"accountnumber": "2808432", "name": "RBM PLUMBING SERVICES LLC",
                                            "date_registration": "2026-10-16T00:00:00.000",
                                            "mailing_address": "223 N Bishop Ave , ,Bridgeport ,CT ,United States ,06610-2431"})
    assert ct["city"] == "Bridgeport" and ct["zip"] == "06610"


def test_guess_contact_keeps_only_own_site():
    res = [{"url": "https://issuu.com/x", "title": "Tonys Junk Removal LLC", "description": "filed in NY"},
           {"url": "https://www.yelp.com/search?find_desc=junk", "title": "Tonys Junk Removal", "description": ""},
           {"url": "https://m.yelp.com/biz/tonys-junk", "title": "Tonys Junk Removal - Shirley", "description": "(631) 578-5280"},
           {"url": "https://tonysjunk.com/", "title": "Tonys Junk Removal | Long Island", "description": "Call 631-578-5280"}]
    f = guess_contact(res, "Tonys Junk Removal Llc")
    assert f["website"] == "https://tonysjunk.com" and f["phones"] == ["6315785280"]
    assert [x["site"] for x in f["listings"]] == ["Yelp"]


def test_fetch_service_page_and_leads(settings, make_http):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "data.ny.gov":
            return httpx.Response(200, json=[{"dos_id": "1", "current_entity_name": "TONYS JUNK REMOVAL LLC",
                                              "initial_dos_filing_date": "2026-10-17T00:00:00.000",
                                              "dos_process_city": "Miller Place", "county": "Suffolk"}])
        return httpx.Response(200, json=[])

    async def go():
        async with make_http(handler) as http:
            rows, errors = await fetch_new(http, "junk removal", ["NY", "CO", "TX"], days=30, today=date(2026, 10, 20))
            return rows, errors
    rows, errors = asyncio.run(go())
    assert [r["name"] for r in rows] == ["Tonys Junk Removal Llc"] and errors == ["TX: no open registry yet"]
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        sf = app.state.sf
        with sf() as s:
            s.add(Registration(state="NY", reg_id="1", name="Tonys Junk Removal Llc", filed="2026-10-17", city="Miller Place",
                               keyword="junk removal", found={"website": None, "phones": ["6315785280"], "listings": []}))
            s.commit()
            rid = s.query(Registration).one().id
        page = c.get("/radar?keyword=junk+removal").text
        assert "Tonys Junk Removal" in page and "no website found" in page and "6315785280" in page
        r = c.post("/radar/leads", data={"ids": [str(rid)]}, follow_redirects=False)     # the page's own button
        assert r.status_code == 303 and "1%20added" in r.headers["location"]
        with sf() as s:
            b = s.query(Business).one()
            assert b.name == "Tonys Junk Removal Llc" and b.phone and s.query(Registration).one().business_id == b.id
        assert LeadService(settings, sf, None).radar_add_to_leads([rid]) in ([], [b.id])
        app.state.http_factory = lambda: make_http(handler)
        r = c.post("/radar/fetch", data={"keyword": "junk removal", "states": ["NY"], "days": "30"},
                   follow_redirects=False)
        assert r.status_code == 303 and "/radar?" in r.headers["location"]
