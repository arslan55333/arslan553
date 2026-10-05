import asyncio
from dataclasses import replace
from datetime import date

import httpx
import pytest

from leadengine.config import ProviderSettings
from leadengine.db import Business, Repository
from leadengine.enrich.ads.serp import (
    SerpAd, ad_landing_domain, ads_from_serpapi, canonical_location, match_ads, name_similarity, serp_browser, uule)
from leadengine.enrich.ads.site_tags import SiteAdSignals, scan_gtm, scan_html
from leadengine.enrich.ads.status import decide, parse_transparency
from leadengine.models import BusinessRecord
from leadengine.providers.playwright_maps import PlaywrightMapsProvider
from leadengine.service import LeadService
from tests.fake_maps import FakeMaps, business

TODAY = date(2026, 10, 5)


def run(coro):
    return asyncio.run(coro)


# ── website tags ─────────────────────────────────────────────────────
def test_scan_html_finds_ad_tech():
    html = """<script async src="https://www.googletagmanager.com/gtag/js?id=AW-987654321"></script>
    <script>gtag('config','AW-987654321'); gtag('event','conversion',{'send_to':'AW-987654321/abc'});</script>
    <script>(function(w,d,s,l,i){})(window,document,'script','dataLayer','GTM-WXYZ12');</script>
    <img src="https://googleads.g.doubleclick.net/pagead/viewthroughconversion/987654321/?guid=ON">
    <script src="//cdn.callrail.com/companies/1/2/12/swap.js"></script>
    <script>fbq('init', '1234567890123'); var gclid = getParam('gclid');</script>
    <script src="//bat.bing.com/bat.js"></script>"""
    s = scan_html(html)
    assert s.google_ads_ids == ["AW-987654321"] and s.conversion_tag and s.remarketing_tag
    assert s.gtm_containers == ["GTM-WXYZ12"] and s.call_tracking == ["CallRail"]
    assert s.meta_pixel and s.meta_pixel_ids == ["1234567890123"] and s.gclid_handling and s.microsoft_ads
    assert not scan_html("<html>plain site</html>").google_ads_evidence


def test_gtm_container_reveals_hidden_ads_tag(make_http):
    def handler(request):
        assert request.url.params["id"] == "GTM-ABC123"
        return httpx.Response(200, text='var data={"resource":{"tags":[{"function":"__awct","vtp_conversionId":"555666777"}]}};'
                                        "gtag('config','AW-555666777');")

    async def go():
        async with make_http(handler) as http:
            return await scan_gtm(http, scan_html("<script>'GTM-ABC123'</script>"))

    s = run(go())
    assert s.ads_in_gtm and s.google_ads_ids == ["AW-555666777"] and s.google_ads_evidence


# ── SERP helpers ─────────────────────────────────────────────────────
def test_uule_and_location():
    loc = canonical_location("Dallas", "TX")
    assert loc == "Dallas,Texas,United States"
    assert len(loc) == 26 and uule(loc) == "w+CAIQICIa" + "RGFsbGFzLFRleGFzLFVuaXRlZCBTdGF0ZXM="


def test_ad_landing_domain():
    assert ad_landing_domain("https://www.googleadservices.com/pagead/aclk?sa=L&adurl=https://www.acme.com/x") == "acme.com"
    assert ad_landing_domain("https://www.google.com/aclk?foo=1") is None
    assert ad_landing_domain("https://acme.com/page") == "acme.com"


def test_serpapi_ads_parsing():
    ads = ads_from_serpapi({
        "ads": [{"title": "Acme Roofing - Free Estimate", "displayed_link": "www.acmeroofing.com", "position": 1}],
        "local_ads": {"ads": [{"title": "Bob's Roofing", "phone": "(214) 555-0101", "badge": "Google Guaranteed"}]},
    })
    assert [(a.kind, a.domain, a.badge) for a in ads] == [("search", "acmeroofing.com", None),
                                                          ("lsa", None, "Google Guaranteed")]


def test_match_ads():
    ads = [SerpAd("search", "Free Quotes Today", "acme.com"), SerpAd("lsa", "Bob's Roofing LLC", None, "214-555-0101"),
           SerpAd("lsa", "Smith Roofing & Gutters", None)]
    assert [a.title for a in match_ads(ads, name="Acme", domain="acme.com", phone=None)] == ["Free Quotes Today"]
    assert [a.title for a in match_ads(ads, name="Bobs", domain=None, phone="(214) 555-0101")] == ["Bob's Roofing LLC"]
    assert [a.title for a in match_ads(ads, name="Smith Roofing", domain=None, phone=None)] == ["Smith Roofing & Gutters"]
    assert match_ads(ads, name="Roofing", domain="other.com", phone=None) == []   # one generic word is not a match
    assert name_similarity("Smith Roofing", "Jones Roofing") < 0.8


# ── verdict ──────────────────────────────────────────────────────────
def test_decide_status_levels():
    site_aw = SiteAdSignals(google_ads_ids=["AW-1"], conversion_tag=True)
    active = decide(serp_hits=[SerpAd("search", "Ad", "a.com", position=2)], serp_checked=True, site=site_aw,
                    maps_sponsored=False, transparency=None, today=TODAY)
    assert active.status == "Active" and active.confidence == 95 and not active.lsa
    assert any("position 2" in e for e in active.evidence) and active.google_ads_ids == ["AW-1"]
    lsa = decide(serp_hits=[SerpAd("lsa", "Bob", badge="Google Guaranteed")], serp_checked=True, site=None,
                 maps_sponsored=False, transparency=None, today=TODAY)
    assert lsa.status == "Active" and lsa.lsa and "Google Guaranteed" in lsa.evidence[0]
    likely = decide(serp_hits=[], serp_checked=True, site=site_aw, maps_sponsored=False, transparency=None, today=TODAY)
    assert likely.status == "Likely" and likely.confidence == 70
    weak = decide(serp_hits=[], serp_checked=True, site=SiteAdSignals(call_tracking=["CallRail"]),
                  maps_sponsored=False, transparency=None, today=TODAY)
    assert weak.status == "Likely" and weak.confidence == 40
    maps = decide(serp_hits=[], serp_checked=False, site=None, maps_sponsored=True, transparency=None, today=TODAY)
    assert maps.status == "Active"
    past = decide(serp_hits=[], serp_checked=True, site=None, maps_sponsored=False,
                  transparency={"creatives": 4, "last_shown": "2025-01-10"}, today=TODAY)
    assert past.status == "Past"
    none = decide(serp_hits=[], serp_checked=True, site=SiteAdSignals(meta_pixel=True), maps_sponsored=False,
                  transparency=None, today=TODAY)
    assert none.status == "None" and none.meta_ads


def test_parse_transparency():
    data = {"ad_creatives": [
        {"advertiser": "Acme LLC", "target_domain": "acme.com", "first_shown": 1700000000, "last_shown": 1790000000},
        {"advertiser": "Acme LLC", "target_domain": "acme.com", "first_shown": "2023-01-01", "last_shown": "2026-09-30"},
        {"advertiser": "Other", "target_domain": "other.com", "last_shown": "2026-10-04"}]}
    out = parse_transparency(data, "acme.com", TODAY)
    assert out["creatives"] == 2 and out["last_shown"] == "2026-09-30" and out["first_shown"] == "2023-01-01"
    assert parse_transparency({}, "x.com", TODAY)["creatives"] == 0


# ── browser SERP + full service (real Chromium, local fake Google) ───
pw = pytest.importorskip("playwright.async_api")


@pytest.fixture
def fake_google():
    with FakeMaps() as fm:
        yield fm


@pytest.fixture
def ads_settings(settings, fake_google):
    extra = {"base_url": fake_google.base_url, "contexts": 2, "max_attempts": 1, "timeout_ms": 10000}
    return replace(settings, providers={**settings.providers, "playwright": ProviderSettings(extra=extra)},
                   sections={**settings.sections, "ads": {"serp_provider": "playwright", "gtm": True}})


def test_serp_browser_parses_search_ads_and_lsa(ads_settings, make_http, fake_google):
    async def go():
        async with make_http(lambda r: httpx.Response(404)) as http:
            p = PlaywrightMapsProvider(ads_settings, http)
            try:
                return await serp_browser(p, "dumpster rental", "Dallas", "TX", p.base_url)
            finally:
                await p.aclose()

    snap = run(go())
    assert snap.error is None
    search = [a for a in snap.ads if a.kind == "search"]
    lsa = [a for a in snap.ads if a.kind == "lsa"]
    assert [a.domain for a in search] == [business(2)["website"][8:-1], "bigchain.com"]
    assert search[0].phone == "(214) 555-1002"
    assert [(a.title, a.badge) for a in lsa] == [(business(3)["name"], "Google Guaranteed"),
                                                 ("Other Guaranteed Pro", "Google Screened")]
    assert "uule=w%2BCAIQICI" in fake_google.serp_queries[0]


def test_detect_ads_service(ads_settings, session_factory, make_http, fake_google):
    b2, b3, b4, b5 = business(2), business(3), business(6), business(5)  # #6 has a website with GTM

    def site_handler(request):
        host = request.url.host
        if host.endswith(b4["website"][8:-1]):
            return httpx.Response(200, text="<html><script>(window,'GTM-HID123')</script>Welcome</html>",
                                  headers={"content-type": "text/html"})
        if "googletagmanager" in host:
            return httpx.Response(200, text="gtag('config','AW-111222333')")
        return httpx.Response(404)

    with session_factory() as db:
        repo = Repository(db)
        ids = []
        for b in (b2, b3, b4, b5):
            ids.append(repo.upsert_business(BusinessRecord(
                name=b["name"], provider="t", place_id=b["place_id"], website=b["website"], phone=b["phone"],
                address=f"{b['street']}, Dallas, TX 75201", categories=["Dumpster rental service"])).id)
        db.commit()

    async def go():
        async with make_http(site_handler) as http:
            service = LeadService(ads_settings, session_factory, http)
            try:
                first = await service.detect_ads(ids, keyword="dumpster rental")
                second = await service.detect_ads(ids, keyword="dumpster rental")
                return first, second
            finally:
                await service.aclose()

    first, second = run(go())
    by = {r["name"]: r for r in first}
    assert by[b2["name"]]["status"] == "Active" and not by[b2["name"]]["lsa"]       # search ad (domain match)
    assert by[b3["name"]]["status"] == "Active" and by[b3["name"]]["lsa"]           # LSA (name match)
    assert by[b4["name"]]["status"] == "Likely" and by[b4["name"]]["google_ads_ids"] == ["AW-111222333"]
    assert by[b5["name"]]["status"] == "None"
    assert len(fake_google.serp_queries) == 1                                     # one SERP for all four, then cached
    assert [r["status"] for r in second] == [r["status"] for r in first]
    with session_factory() as db:
        assert db.get(Business, ids[1]).lsa is True and db.get(Business, ids[0]).ads_status == "Active"
