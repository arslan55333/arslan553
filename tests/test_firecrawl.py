import asyncio
import json
from dataclasses import replace

import httpx

from leadengine.db import Business, Repository
from leadengine.enrich.ads.landing import audit_landing
from leadengine.enrich.emails.crawl import SiteCrawler
from leadengine.enrich.firecrawl import Firecrawl, build_firecrawl, needs_js, page_stats
from leadengine.enrich.website.analyzer import WebsiteAnalyzer
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.service import LeadService

SHELL = """<html><head><title>Acme</title><script src="/a.js"></script><script src="/b.js"></script>
<script>window.x=1</script></head><body><div id="root"></div><noscript>You need to enable JavaScript to run this app.
</noscript></body></html>"""
RENDERED = """<html><head><title>Acme Plumbing Dallas</title><meta name="viewport" content="width=device-width"></head>
<body><h1>Plumber in Dallas</h1><a href="tel:2145550100">Call</a><a href="mailto:owner@acme-plumb.com">Email</a>
<a href="/contact">Contact</a><p>""" + "words " * 120 + "</p></body></html>"""


def fc_handler(calls, *, status=200, pages=None):
    """Fake api.firecrawl.dev: /scrape returns RENDERED, /map returns ``pages``."""
    def handle(request):
        body = json.loads(request.content or b"{}")
        calls.append((request.url.path, body.get("url"), body.get("formats")))
        if status != 200:
            return httpx.Response(status, json={"success": False, "error": "Payment required"})
        if request.url.path == "/v1/map":
            return httpx.Response(200, json={"success": True, "links": pages or []})
        if body.get("formats") == ["json"]:
            return httpx.Response(200, json={"success": True, "data": {
                "json": {"services": ["drain cleaning", "water heaters"], "owner_name": "Sam Acme", "founded_year": 1998,
                         "license_number": ""}, "metadata": {"creditsUsed": 5, "statusCode": 200}}})
        return httpx.Response(200, json={"success": True, "data": {
            "rawHtml": RENDERED, "markdown": "# Plumber",
            "metadata": {"url": body["url"], "statusCode": 200, "title": "Acme", "creditsUsed": 1}}})
    return handle


def site_handler(fc_calls, *, site_status=403, site_html=None, fc_status=200, pages=None):
    fc = fc_handler(fc_calls, status=fc_status, pages=pages)

    def handle(request):
        if request.url.host == "api.firecrawl.dev":
            return fc(request)
        if site_html is not None:
            return httpx.Response(200, text=site_html, headers={"content-type": "text/html"})
        return httpx.Response(site_status, text="Attention required", headers={"content-type": "text/html"})
    return handle


def test_needs_js_and_page_stats():
    assert needs_js(SHELL) and needs_js("") and not needs_js(RENDERED)
    urls = ["https://a.com", "https://a.com/drain-cleaning", "https://a.com/water-heater-repair", "https://a.com/plano",
            "https://a.com/plano-plumber", "https://a.com/blog/5-tips", "https://a.com/wp-content/x.jpg",
            "https://a.com/contact", "https://a.com/contact/", "https://a.com/about-plumber",
            "https://a.com/service-areas/frisco", "https://a.com/contact-plumbing-service"]
    st = page_stats(urls, service_words=["plumber"], towns=["Plano"])
    assert st["pages"] == 10 and st["blog_posts"] == 1 and st["area_pages"] == 3     # plano x2 + service-areas/frisco
    assert st["service_pages"] == 2      # drain-cleaning, water-heater-repair (not about-/contact- pages)


def test_build_firecrawl_modes(settings, make_http):
    http = make_http(lambda r: httpx.Response(200))
    assert build_firecrawl(settings, http) is None                                   # no key
    keyed = replace(settings, firecrawl_api_key="fc-test")
    assert build_firecrawl(keyed, http).mode == "smart"
    off = replace(keyed, sections={**keyed.sections, "firecrawl": {"mode": "off"}})
    assert build_firecrawl(off, http) is None
    fb = Firecrawl("k", http, mode="fallback")
    assert fb.enabled and not fb.can_map and not fb.can_extract
    asyncio.run(http.aclose())


def test_crawler_falls_back_to_firecrawl_when_site_blocks_us(make_http, credits):
    calls = []

    async def go():
        async with make_http(site_handler(calls, site_status=403)) as http:
            fc = Firecrawl("fc-test", http, mode="fallback", credits=credits)
            crawler = SiteCrawler(http, max_pages=4, firecrawl=fc, firecrawl_subpages=1)
            res = await crawler.crawl("https://acme-plumb.com")
            return res, fc.used
    res, used = asyncio.run(go())
    assert res.reachable and res.via_firecrawl and "Plumber in Dallas" in res.pages[0].html
    assert [p.kind for p in res.pages] == ["home", "contact"]      # only 1 sub-page through Firecrawl
    assert used == 2 and all(path == "/v1/scrape" for path, *_ in calls)
    assert sum(u.calls_month for u in credits.summary() if u.provider == "firecrawl") == 2


def test_crawler_renders_javascript_shell(make_http):
    calls = []

    async def go():
        async with make_http(site_handler(calls, site_html=SHELL)) as http:
            crawler = SiteCrawler(http, max_pages=1, firecrawl=Firecrawl("fc-test", http, mode="fallback"))
            return await crawler.crawl("https://acme-plumb.com")
    res = asyncio.run(go())
    assert "Plumber in Dallas" in res.pages[0].html and len(calls) == 1


def test_no_firecrawl_call_for_normal_site(make_http):
    calls = []

    async def go():
        async with make_http(site_handler(calls, site_html=RENDERED)) as http:
            crawler = SiteCrawler(http, max_pages=1, firecrawl=Firecrawl("fc-test", http, mode="full"))
            return await crawler.crawl("https://acme-plumb.com")
    res = asyncio.run(go())
    assert res.reachable and not res.via_firecrawl and calls == []


def test_out_of_credits_disables_for_the_run(make_http):
    calls = []

    async def go():
        async with make_http(site_handler(calls, site_status=403, fc_status=402)) as http:
            fc = Firecrawl("fc-test", http, mode="smart")
            crawler = SiteCrawler(http, max_pages=1, firecrawl=fc)
            a = await crawler.crawl("https://one.com")
            b = await crawler.crawl("https://two.com")
            return a, b, fc
    a, b, fc = asyncio.run(go())
    assert not a.reachable and not b.reachable and not fc.enabled and len(calls) == 1


def test_credit_cap_per_run(make_http):
    calls = []

    async def go():
        async with make_http(site_handler(calls, site_status=403)) as http:
            fc = Firecrawl("fc-test", http, mode="fallback", max_per_run=2)
            return [await fc.scrape(f"https://s{i}.com") for i in range(4)]
    got = asyncio.run(go())
    assert [g is not None for g in got] == [True, True, False, False] and len(calls) == 2


def test_blocked_site_is_not_called_broken(settings, make_http):
    """A site that answers 403 to robots is up: it must not become a 'website is down' hot lead."""
    async def go():
        async with make_http(lambda r: httpx.Response(403, text="cf")) as http:
            a = WebsiteAnalyzer(replace(settings, sections={**settings.sections, "website": {"pagespeed": False,
                                "wayback": False, "screenshot": False}}), http)
            try:
                return await a.analyze("https://guarded.com", key="1")
            finally:
                await a.aclose()
    out = asyncio.run(go())
    assert out["flags"] == ["blocks_bots"] and out["score"] is None and "Firecrawl" in out["reasons"][0]


def test_landing_audit_through_firecrawl(make_http):
    calls = []

    async def go():
        async with make_http(site_handler(calls, site_html=SHELL)) as http:
            return await audit_landing(http, "https://acme-plumb.com/lp", ad_title="Dallas Plumber", use_pagespeed=False,
                                       firecrawl=Firecrawl("fc-test", http, mode="fallback"))
    out = asyncio.run(go())
    assert out["via_firecrawl"] and out["facts"]["tel_links"] and out["facts"]["h1"] == "Plumber in Dallas"


def test_seo_audit_counts_pages_and_compares_competitors(settings, session_factory, make_http):
    with session_factory() as s:
        repo = Repository(s)
        me = repo.upsert_business(BusinessRecord(name="Acme", provider="t", place_id="A", website="https://acme-plumb.com/",
                                                 city="Dallas", state="TX", zip_code="75201", review_count=31))
        rival = repo.upsert_business(BusinessRecord(name="Rival", provider="t", place_id="R", review_count=400,
                                                    website="https://rival.com/"))
        repo.record_search(SearchQuery("plumber", "75201"), "t", [(rival, 1), (me, 2)], exhausted=True, api_calls=1)
        s.commit()
        mid = me.id
    calls = []
    many = [f"https://rival.com/plumbing-service-{i}" for i in range(40)]

    def handle(request):
        if request.url.host == "api.firecrawl.dev":
            body = json.loads(request.content)
            pages = many if "rival" in body["url"] else ["https://acme-plumb.com/", "https://acme-plumb.com/contact"]
            return fc_handler(calls, pages=pages)(request)
        if request.url.path == "/":
            return httpx.Response(200, text=RENDERED, headers={"content-type": "text/html"})
        return httpx.Response(404)

    async def go():
        async with make_http(handle) as http:
            svc = LeadService(replace(settings, firecrawl_api_key="fc-test"), session_factory, http)
            return (await svc.seo_audits([mid]))[0]
    out = asyncio.run(go())
    assert out["facts"]["site"]["pages"] == 2 and out["facts"]["competitor_sites"][0]["pages"] == 40
    assert any("competitors above you average 40" in i for i in out["issues"])
    assert [c[0] for c in calls] == ["/v1/map", "/v1/map"]


def test_site_info_full_mode(settings, session_factory, make_http):
    with session_factory() as s:
        b = Repository(s).upsert_business(BusinessRecord(name="Acme", provider="t", place_id="A",
                                                         website="https://acme-plumb.com/"))
        s.commit()
        bid = b.id
    calls = []

    async def go():
        async with make_http(fc_handler(calls)) as http:
            svc = LeadService(replace(settings, firecrawl_api_key="fc-test"), session_factory, http)
            n0 = await svc.site_info([bid])                       # mode smart: not run
            n1 = await svc.site_info([bid], explicit=True)        # button on the lead page
            return n0, n1
    n0, n1 = asyncio.run(go())
    assert (n0, n1) == (0, 1) and calls[0][2] == ["json"]
    with session_factory() as s:
        info = Repository(s).latest_enrichment(bid, "site_info").payload
        assert info["owner_name"] == "Sam Acme" and "license_number" not in info
        assert s.get(Business, bid).owner_name == "Sam Acme"
