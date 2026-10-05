import asyncio
import io
import re
import zipfile
from dataclasses import replace

import httpx
import pytest

from leadengine.db import Business, Repository
from leadengine.llm import LLM
from leadengine.models import BusinessRecord
from leadengine.preview import deploy as deployers
from leadengine.preview.builder import PreviewBuilder, render_page, slugify
from leadengine.preview.content import _clean, gather_facts, make_copy, normalize_hours, template_copy
from leadengine.providers.playwright_maps import top_reviews


def run(coro):
    return asyncio.run(coro)


def make_biz(**kw):
    base = dict(id=7, name="Bob's Septic & Drain", phone="(214) 555-0199", city="Dallas", state="TX", zip_code="75201",
                rating=4.8, review_count=212, categories=["Septic system service", "Plumber"],
                hours={"Monday": "7 AM–6 PM", "sunday": "Closed"}, owner_name="Bob Miller")
    base.update(kw)
    return Business(**base)


ACTIVITY = {"top_reviews": [{"text": "Came the same day and fixed it. Great crew!", "rating": 5, "author": "Maria G."}]}


def test_facts_and_hours_formats():
    f = gather_facts(make_biz(), ACTIVITY)
    assert f.phone_href == "tel:+12145550199" and f.category == "Septic system service"
    assert f.hours == [("Monday", "7 AM–6 PM"), ("Sunday", "Closed")]
    assert f.service_area[0] == "Dallas" and len(f.service_area) > 1 and f.reviews[0]["author"] == "Maria G."
    assert normalize_hours(["Monday: 8 AM – 5 PM", "Tuesday: 8 AM – 5 PM"]) == [("Monday", "8 AM – 5 PM"), ("Tuesday", "8 AM – 5 PM")]
    assert normalize_hours({"mon": ["8-5"]}) == [("Monday", "8-5")] and normalize_hours(None) == []


def test_top_reviews_picks_good_text_and_shortens_names():
    picked = top_reviews([
        {"rating": 2, "text": "Late and rude, would not use again at all.", "author": "Ann Smith"},
        {"rating": 5, "text": "ok", "author": "Bo"},
        {"rating": 5, "text": "Fantastic service, explained everything and cleaned up after.", "author": "James Robert Taylor"},
        {"rating": 4, "text": "Good value and showed up on time for the appointment.", "author": "Priya"}])
    assert [r["author"] for r in picked] == ["James T.", "Priya"]


def test_claim_filter():
    text = "We pump tanks fast. Family owned for 25 years. Fully licensed and insured. Call today!"
    assert _clean(text) == "We pump tanks fast. Call today!"
    assert _clean("#1 rated in Texas. From $99 per visit. Friendly crew.") == "Friendly crew."


class FakeLLM(LLM):
    def __init__(self, reply):
        super().__init__("fake", {})
        self.reply = reply
        self.prompts = []

    async def complete(self, prompt, *, system=None, images=None, max_tokens=2000):
        self.prompts.append(prompt)
        return self.reply


def test_ai_copy_is_filtered_and_falls_back():
    reply = ('{"headline": "Dallas septic done right", "subheadline": "#1 rated in Texas. Quick help for your tank.",'
             ' "about": "Serving Dallas since 1999. We answer the phone.", "services": [{"name": "Pumping",'
             ' "blurb": "Routine pumping. Guaranteed lowest price."}], "why_us": ["Licensed pros", "Clear quotes"],'
             ' "faq": [{"q": "Do you pump?", "a": "Yes."}], "cta": "Book now"}')
    f = gather_facts(make_biz(), ACTIVITY)
    llm = FakeLLM(reply)
    c = run(make_copy(f, llm))
    assert c.source == "ai" and c.headline == "Dallas septic done right"
    assert c.subheadline == "Quick help for your tank." and c.about == "We answer the phone."
    assert c.services == [{"name": "Pumping", "blurb": "Routine pumping."}] and c.why_us == ["Clear quotes"]
    assert "Do not invent" in llm.prompts[0] and "Maria" not in llm.prompts[0]      # reviews not sent to the model
    broken = run(make_copy(f, FakeLLM("sorry, I can't")))
    assert broken.source == "template"


def test_page_is_clearly_a_preview_and_safe():
    f = gather_facts(make_biz(name='Bob <script>alert(1)</script> Septic'), ACTIVITY)
    html = render_page(f, template_copy(f), "warm", {"name": "Arslan Web Studio", "url": "", "email": "a@b.com"})
    assert '<meta name="robots" content="noindex, nofollow, noarchive">' in html
    assert "Website redesign preview</b> prepared for Bob &lt;script&gt;" in html and "<script>alert" not in html
    assert "not the official" in html and "Demo only" in html and 'action=' not in html
    assert 'href="tel:+12145550199"' in html
    assert not re.search(r'(src|href)="https?://(?!arslan)', html)      # no external assets / Google photos
    assert slugify("Bob's Septic & Drain, LLC") == "bob-s-septic-and-drain-llc"


def test_builder_writes_site_and_marks_crm(settings, session_factory, tmp_path):
    from leadengine import crm
    from leadengine.service import LeadService

    s = replace(settings, sections={**settings.sections, "preview": {"brand_name": "Arslan Web Studio", "use_ai": False}})
    with session_factory() as db:
        b = Repository(db).upsert_business(BusinessRecord(name="Acme Roofing", provider="t", place_id="R1",
                                                          phone="2145550100", address="1 Main St, Dallas, TX 75201",
                                                          categories=["Roofing contractor"]))
        Repository(db).set_enrichment(b.id, "maps_activity", ACTIVITY)
        db.commit()
        bid = b.id
    rows = run(LeadService(s, session_factory, None).build_previews([bid], style="bold", screenshots=False))
    site = (s.root / "data" / "previews" / rows[0]["slug"] / "site")
    assert (site / "index.html").read_text().count("Arslan Web Studio") >= 2
    assert (site / "robots.txt").read_text() == "User-agent: *\nDisallow: /\n" and "noindex" in (site / "_headers").read_text()
    assert rows[0]["style"] == "bold" and rows[0]["copy_source"] == "template" and rows[0]["url"] is None
    with session_factory() as db:
        assert crm.current_status(db, bid) == "Preview Built"
        assert Repository(db).latest_enrichment(bid, "preview").payload["slug"] == rows[0]["slug"]


def test_netlify_deploy(tmp_path, make_http):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>hi</h1>")
    (site / "_headers").write_text("/*\n  X-Robots-Tag: noindex\n")
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer tok"
        if request.method == "GET":
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/sites"):
            assert request.read() and b"acme-1.previews.studio.com" in request.content
            return httpx.Response(201, json={"id": "S1", "ssl_url": "https://preview-acme-1.netlify.app"})
        names = zipfile.ZipFile(io.BytesIO(request.content)).namelist()
        assert sorted(names) == ["_headers", "index.html"] and request.headers["content-type"] == "application/zip"
        return httpx.Response(200, json={"id": "D1"})

    async def go():
        async with make_http(handler) as http:
            return await deployers.deploy_netlify(http, "tok", site, "preview-acme-1", "acme-1.previews.studio.com")

    assert run(go()) == "https://acme-1.previews.studio.com"
    assert [c[0] for c in calls] == ["GET", "POST", "POST"] and calls[2][1] == "/api/v1/sites/S1/deploys"
    with pytest.raises(deployers.DeployError):
        run(deployers.deploy_netlify(None, "", site, "x"))


def test_cloudflare_deploy_creates_project_then_retries(tmp_path):
    seen = []

    async def runner(args, env):
        seen.append(args)
        assert env["CLOUDFLARE_API_TOKEN"] == "t"
        if len(seen) == 1:
            return 1, "Project not found"
        return 0, "Deployment complete"

    url = run(deployers.deploy_cloudflare(tmp_path, "preview-acme", api_token="t", account_id="a", runner=runner))
    assert url == "https://preview-acme.pages.dev"
    assert "create" in seen[1] and seen[2][-5:-3] == ["--project-name", "preview-acme"]
