import asyncio
from dataclasses import replace

import httpx

from leadengine.db import Business, Repository
from leadengine.enrich.citations import analyze, check_listing, directory_of, phones_in, street_key
from leadengine.insights import competitor_gap
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.outreach.facts import nap_line
from leadengine.service import LeadService

ADDR = "168-46 Douglas Ave, Jamaica, NY 11433"
RESULTS = [
    {"q": "phone", "url": "https://m.yelp.com/biz/royal-waste-jamaica-3", "title": "Royal Waste Services - Jamaica",
     "description": "Royal Waste Services, 187-40 Hollis Ave, Jamaica, NY 11423 (718) 526-2623"},
    {"q": "phone", "url": "https://www.bbb.org/us/ny/hollis/profile/royal-waste", "title": "Royal Waste Services | BBB",
     "description": "Phone: (718) 526-2623 168-46 Douglas Ave Jamaica NY 11433"},
    {"q": "phone", "url": "https://www.facebook.com/groups/queens/posts/1", "title": "group", "description": "call 718-526-2623"},
    {"q": "phone", "url": "https://www.instagram.com/p/xyz/", "title": "Royal Waste", "description": "718-526-2623 1 Main St 10001"},
    {"q": "phone", "url": "https://www.whitepages.com/phone/1-718-526", "title": "lookup", "description": "718-526-2623 11590"},
    {"q": "name", "url": "https://www.yellowpages.com/royal-waste-texas", "title": "Royal Plumbing Dallas",
     "description": "Royal Plumbing (214) 555-0100 Dallas TX 75201"},
    {"q": "name", "url": "https://www.angi.com/companylist/royal-waste-services", "title": "Royal Waste Services | Angi",
     "description": "Royal Waste Services (718) 555-0199 Jamaica NY"},
    {"q": "phone", "url": "https://royalwaste.com/contact", "title": "Contact", "description": "718-526-2623"},
]


def test_parsers():
    assert directory_of("https://m.yelp.com/biz/x") == ("Yelp", True)
    assert directory_of("https://maps.apple.com/?q=x")[0] == "Apple Maps" and directory_of("https://www.apple.com/")[0] is None
    assert phones_in("Call (718) 526-2623 or 718.555.0100") == {"7185262623", "7185550100"}
    assert street_key(ADDR) == ("168-46", "11433")


def test_analyze_finds_mismatches_and_ignores_noise():
    out = analyze(RESULTS, phone="(718) 526-2623", address=ADDR, own_domain="royalwaste.com", queries=2,
                  source="firecrawl", name="Royal Waste Services")
    sites = {x["site"]: x for x in out["listings"]}
    assert "whitepages.com" not in sites and "Yellow Pages" not in sites          # people-search + look-alike skipped
    assert "royalwaste.com" not in sites and sites["Instagram"]["issues"] == []    # own site + social post
    assert sites["Yelp"]["zip_ok"] is False and "11423" in sites["Yelp"]["issues"][0]
    assert sites["BBB"]["phone_ok"] and sites["BBB"]["issues"] == []
    assert sites["Angi"]["phone_ok"] is False and "(718) 555-0199" in sites["Angi"]["issues"][0]
    assert "Yellow Pages" in out["missing_core"] and "Apple Maps" in out["manual"] and 0 < out["score"] < 80
    assert nap_line(out).startswith("your Angi listing shows a different phone") or nap_line(out).startswith("your Yelp")


def test_citation_audits_service_with_serpapi(settings, session_factory, make_http, credits):
    with session_factory() as s:
        b = Repository(s).upsert_business(BusinessRecord(name="Royal Waste Services", provider="t", place_id="R",
                                                         phone="(718) 526-2623", address=ADDR, city="Jamaica", state="NY"))
        s.commit()
        bid = b.id
    queries = []

    def handler(request):
        queries.append(request.url.params["q"])
        return httpx.Response(200, json={"organic_results": [
            {"link": RESULTS[0]["url"], "title": RESULTS[0]["title"], "snippet": RESULTS[0]["description"]}]})

    async def go():
        async with make_http(handler) as http:
            svc = LeadService(replace(settings, sections={**settings.sections, "firecrawl": {"mode": "off"}}),
                              session_factory, http, credits)
            return await svc.citation_audits([bid])
    rows = asyncio.run(go())
    assert queries == ['"(718) 526-2623"', '"Royal Waste Services" Jamaica NY'] and rows[0]["source"] == "serpapi"
    with session_factory() as s:
        assert s.get(Business, bid).citation_score == rows[0]["score"]


def test_category_gap(session_factory):
    with session_factory() as s:
        repo = Repository(s)
        me = repo.upsert_business(BusinessRecord(name="Me", provider="t", place_id="M", categories=["Junk removal service"]))
        tops = [repo.upsert_business(BusinessRecord(name=f"T{i}", provider="t", place_id=f"T{i}",
                                                    categories=["Junk removal service", "Garbage collection service"]))
                for i in range(3)]
        repo.record_search(SearchQuery("junk removal", "11368"), "t", [(t, i + 1) for i, t in enumerate(tops)] + [(me, 5)],
                           exhausted=True, api_calls=1)
        s.commit()
        g = competitor_gap(s, me, "junk removal", None)
        assert g["category_gap"] == ["Garbage collection service"] and "Garbage collection service" in g["actions"][0]
