import asyncio
import datetime as dt
import ssl
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest

from leadengine.config import ProviderSettings
from leadengine.db import Business, Repository
from leadengine.enrich.website import remote
from leadengine.enrich.website.analyzer import WebsiteAnalyzer
from leadengine.enrich.website.score import compute_score, grade_for
from leadengine.enrich.website.signals import analyze_html, latest_copyright_year
from leadengine.enrich.website.tech import Fingerprints, load_fingerprints
from leadengine.http import HttpClient
from leadengine.llm import LLM, extract_json
from leadengine.models import BusinessRecord
from leadengine.service import LeadService
from tests.fake_sites import MODERN_SITE, OLD_SITE, PARKED, FakeSites

TODAY = date(2026, 10, 5)


def run(coro):
    return asyncio.run(coro)


# ── HTML signals ─────────────────────────────────────────────────────
def test_copyright_year():
    assert latest_copyright_year("© 2018-2024 Acme", 2026) == 2024
    assert latest_copyright_year("Copyright 2009 Bob", 2026) == 2009
    assert latest_copyright_year("(c) 2031 future", 2026) is None
    assert latest_copyright_year("no year here", 2026) is None


def test_old_vs_modern_signals():
    old, new = analyze_html(OLD_SITE, 2026), analyze_html(MODERN_SITE, 2026)
    assert not old.viewport and new.viewport
    assert old.copyright_year == 2009 and new.copyright_year == 2026
    assert old.table_layout and old.flash and {"font", "center", "marquee"} <= set(old.deprecated_tags)
    assert not old.html5_doctype and new.html5_doctype
    assert old.tel_links == 0 and new.tel_links == 1
    assert new.quote_form and "get a free quote" in new.cta_phrases and new.reviews_section
    assert analyze_html(PARKED, 2026).parked


# ── tech detection ───────────────────────────────────────────────────
def test_builtin_fingerprints():
    fp = load_fingerprints()
    old = analyze_html(OLD_SITE, 2026)
    techs = {t.name: t for t in fp.detect(html=OLD_SITE, script_srcs=old.script_srcs, meta=old.meta)}
    assert techs["jQuery"].version == "1.7.2"
    assert techs["Microsoft FrontPage"].version == "5.0"
    assert "Adobe Flash" in techs
    new = analyze_html(MODERN_SITE, 2026)
    modern = {t.name for t in fp.detect(html=MODERN_SITE, script_srcs=new.script_srcs, meta=new.meta)}
    assert {"Google Ads Conversion", "Google Analytics 4", "CallRail"} <= modern


def test_wappalyzer_format_patterns():
    fp = Fingerprints({
        "Acme CMS": {"cats": [1], "headers": {"X-Powered-By": "AcmeCMS/([\\d.]+)\\;version:\\1"},
                     "implies": "PHP", "cookies": {"acme_session": ""}},
        "PHP": {"cats": [27]},
        "Lib": {"cats": [59], "js": {"Lib.version": "([\\d.]+)\\;version:\\1\\;confidence:50"}},
    })
    found = {t.name: t for t in fp.detect(headers={"x-powered-by": "AcmeCMS/2.1"}, js={"Lib.version": "3.4"})}
    assert found["Acme CMS"].version == "2.1" and "PHP" in found
    assert found["Lib"].version == "3.4" and found["Lib"].confidence == 50
    assert {t.name for t in fp.detect(cookies={"acme_session": "x"})} >= {"Acme CMS"}
    assert fp.js_properties == ["Lib.version"]


# ── scoring ──────────────────────────────────────────────────────────
def _score(html_text, **kw):
    html = analyze_html(html_text, 2026)
    techs = load_fingerprints().detect(html=html_text, script_srcs=html.script_srcs, meta=html.meta)
    base = dict(today=TODAY, https_ok=True, cert={"valid": True, "days_left": 200}, render=None, pagespeed=None,
                sitemap_lastmod=None, last_modified_header=None, wayback=None, vision=None, flags=[])
    base.update(kw)
    return compute_score(html, techs, **base)


def test_old_site_scores_low_with_reasons():
    old = _score(OLD_SITE, https_ok=False, cert={"valid": False, "error": "no HTTPS"},
                 render={"mobile": {"horizontal_overflow_px": 610, "small_text_ratio": 0.6}},
                 pagespeed={"performance": 22, "lcp_ms": 9400})
    new = _score(MODERN_SITE, render={"mobile": {"horizontal_overflow_px": 0, "small_text_ratio": 0}},
                 pagespeed={"performance": 91})
    assert old.score < 25 and old.grade == "Outdated"
    assert new.score > 85 and new.grade == "Modern"
    joined = " | ".join(old.reasons)
    assert old.reasons[0].startswith("not mobile friendly")
    for expected in ("copyright 2009", "slow on mobile (PageSpeed 22/100, loads in 9.4s)", "jQuery 1.7.2",
                     "Flash", "no click-to-call", "no secure HTTPS"):
        assert expected in joined, expected
    assert old.as_dict()["signals"]["tech"]["points"] == 0


def test_missing_signals_are_left_out_not_counted_as_zero():
    s = _score(MODERN_SITE)                       # no pagespeed, no render, no vision
    assert "speed" not in s.signals and "design" not in s.signals
    assert s.score > 85


def test_vision_and_expiring_certificate():
    s = _score(MODERN_SITE, cert={"valid": True, "days_left": 5},
               vision={"outdated_1_10": 9, "why": "stock template from 2010, cramped layout"})
    assert s.signals["security"].points == 5 and s.signals["design"].points < 2
    assert any("expires in 5 days" in r for r in s.reasons) and any("design looks outdated" in r for r in s.reasons)
    assert grade_for(None) == "n/a" and grade_for(65) == "OK" and grade_for(45) == "Dated"


# ── remote checks (mocked network) ───────────────────────────────────
def test_pagespeed_wayback_sitemap(make_http):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "pagespeedonline" in url:
            assert request.url.params["strategy"] == "mobile"
            return httpx.Response(200, json={"lighthouseResult": {
                "categories": {"performance": {"score": 0.37}},
                "audits": {"largest-contentful-paint": {"numericValue": 6100.0}, "cumulative-layout-shift": {"numericValue": 0.31}}}})
        if "web.archive.org" in url:
            last = request.url.params["limit"] == "-1"
            return httpx.Response(200, json=[["timestamp"], ["20230102000000" if last else "20040506000000"]])
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nSitemap: https://acme.com/sm.xml\n")
        if url.endswith("/sm.xml"):
            return httpx.Response(200, text='<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                            "<url><loc>a</loc><lastmod>2019-03-01</lastmod></url>"
                                            "<url><loc>b</loc><lastmod>2021-07-15T10:00:00+00:00</lastmod></url></urlset>")
        return httpx.Response(404)

    async def go():
        async with make_http(handler) as http:
            return (await remote.pagespeed(http, "https://acme.com"), await remote.wayback_span(http, "acme.com"),
                    await remote.sitemap_lastmod(http, "https://acme.com"))

    psi, wb, lastmod = run(go())
    assert psi["performance"] == 37 and psi["lcp_ms"] == 6100.0 and psi["cls"] == 0.31
    assert wb == {"first_capture": "2004-05-06", "last_capture": "2023-01-02", "first_year": 2004}
    assert lastmod == "2021-07-15"


def test_pagespeed_rate_limit_message(make_http):
    async def go():
        async with make_http(lambda r: httpx.Response(429)) as http:
            return await remote.pagespeed(http, "https://acme.com")

    assert "PAGESPEED_API_KEY" in run(go())["error"]


def _self_signed(tmp_path: Path, days_valid: int):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test CA")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - dt.timedelta(days=400))
            .not_valid_after(now + dt.timedelta(days=days_valid)).sign(key, hashes.SHA256()))
    crt, pem = tmp_path / "c.pem", tmp_path / "k.pem"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    return crt, pem


def test_certificate_check_reads_expired_cert(tmp_path):
    crt, pem = _self_signed(tmp_path, days_valid=-10)

    async def go():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt, pem)

        async def handle(reader, writer):
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)
        port = server.sockets[0].getsockname()[1]
        async with server:
            return await remote.check_certificate("127.0.0.1", port, timeout=5), \
                await remote.check_certificate("127.0.0.1", 1, timeout=2)

    bad, closed = run(go())
    assert bad["valid"] is False and bad["days_left"] < 0 and bad["issuer"] == "Test CA" and bad["error"]
    assert closed["valid"] is False and "no HTTPS" in closed["error"]


# ── full analyzer with a real browser ────────────────────────────────
class FakeVision(LLM):
    name = "fake"

    def __init__(self):
        super().__init__("fake", {})
        self.images = []

    async def complete(self, prompt, *, system=None, images=None, max_tokens=2000):
        self.images += images or []
        return 'Sure! ```json\n{"outdated_1_10": 8, "era": "2005", "why": "tiny fonts and a table layout"}\n```'


def _external_mock():
    def handler(request: httpx.Request) -> httpx.Response:
        if "pagespeedonline" in str(request.url):
            return httpx.Response(200, json={"lighthouseResult": {"categories": {"performance": {"score": 0.3}}}})
        return httpx.Response(404)
    return handler


@pytest.fixture
def chromium_ok():
    pw = pytest.importorskip("playwright.async_api")

    async def probe():
        async with pw.async_playwright() as p:
            b = await p.chromium.launch()
            await b.close()
    try:
        run(probe())
    except Exception:
        pytest.skip("Chromium not available")


def test_analyzer_end_to_end(settings, tmp_path, chromium_ok):
    from leadengine.enrich.website.render import WebsiteRenderer

    real_request = HttpClient.request

    async def go():
        cfg = {"pagespeed": True, "wayback": False, "screenshot": True, "vision": True, "timeout_seconds": 5}
        s = replace(settings, sections={**settings.sections, "website": cfg})
        mock = httpx.AsyncClient(transport=httpx.MockTransport(_external_mock()))

        async def routed(self, method, url, **kw):   # local sites go to the real server, Google APIs to the mock
            if "127.0.0.1" in str(url):
                return await real_request(self, method, url, **kw)
            kw.pop("retries", None)
            return await mock.request(method, url, **kw)

        HttpClient.request = routed
        try:
            with FakeSites() as sites:
                async with HttpClient(s.http) as http:
                    vision = FakeVision()
                    analyzer = WebsiteAnalyzer(s, http, renderer=WebsiteRenderer(), llm=vision,
                                               screenshot_dir=tmp_path, today=TODAY)
                    try:
                        old = await analyzer.analyze(sites.url("old"), key="1", name="Bob's Septic")
                        modern = await analyzer.analyze(sites.url("modern"), key="2")
                        parked = await analyzer.analyze(sites.url("parked"), key="3")
                        broken = await analyzer.analyze(sites.url("nothing-here"), key="4")
                    finally:
                        await analyzer.aclose()
                    return old, modern, parked, broken, vision
        finally:
            HttpClient.request = real_request
            await mock.aclose()

    old, modern, parked, broken, vision = run(go())
    assert old["score"] < 25 < 60 < modern["score"]
    # no viewport tag: phones render a ~980px desktop layout shrunk down; the modern site fits 390px
    assert old["mobile"]["viewport_width"] > 500 and modern["mobile"]["viewport_width"] == 390
    assert modern["mobile"]["horizontal_overflow_px"] == 0
    assert "phones show a shrunken desktop page" in old["signals"]["mobile"]["detail"]
    assert Path(old["screenshot"]).exists() and Path(old["mobile_screenshot"]).exists()
    assert {"name": "jQuery", "version": "1.7.2", "categories": [59]} in old["tech"]
    assert old["vision"]["outdated_1_10"] == 8 and vision.images[0][:3] == b"\xff\xd8\xff"   # JPEG screenshot sent
    assert old["pagespeed"]["performance"] == 30
    assert "parked" in parked["flags"] and parked["score"] <= 10
    assert "broken" in broken["flags"] and broken["score"] is None
    assert "ssl_invalid" not in old["flags"]          # http-only site is not an SSL error


def test_no_website_and_facebook_only(settings, make_http):
    async def go():
        async with make_http(lambda r: httpx.Response(404)) as http:
            a = WebsiteAnalyzer(settings, http, today=TODAY)
            return await a.analyze(None, key="x"), await a.analyze("https://facebook.com/bobsseptic", key="y")

    none, fb = run(go())
    assert none["flags"] == ["no_website"] and fb["flags"] == ["facebook_only"] and fb["score"] is None


def test_extract_json():
    assert extract_json('text {"a": 1} more') == {"a": 1}
    assert extract_json("```json\n[1, 2]\n```") == [1, 2]
    with pytest.raises(Exception):
        extract_json("no json")


def test_service_scores_and_caches(settings, session_factory, make_http):
    s = replace(settings, sections={**settings.sections, "website": {"pagespeed": False, "wayback": False,
                                                                       "screenshot": False}})
    with session_factory() as db:
        repo = Repository(db)
        ids = [repo.upsert_business(BusinessRecord(name="Bob", provider="t", place_id="W1", website=None)).id,
               repo.upsert_business(BusinessRecord(name="FB", provider="t", place_id="W2",
                                                   website="https://www.facebook.com/x")).id]
        db.commit()

    async def go():
        async with make_http(lambda r: httpx.Response(404)) as http:
            svc = LeadService(s, session_factory, http)
            return await svc.score_websites(ids), await svc.score_websites(ids)

    first, second = run(go())
    assert {r["name"]: r["flags"] for r in first} == {"Bob": ["no_website"], "FB": ["facebook_only"]}
    assert all(r["cached"] for r in second)
    with session_factory() as db:
        assert db.get(Business, ids[1]).website_flags == ["facebook_only"]
