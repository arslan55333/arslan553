import asyncio
import csv
import email
import io
import json
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from leadengine import crm
from leadengine.db import Business, OutboundEmail, Repository, Suppression, utcnow
from leadengine.llm import LLM, LLMError
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.outreach import compose, export as ox, facts as ofacts, mail
from leadengine.service import LeadService


def run(coro):
    return asyncio.run(coro)


def add(s, name, **kw):
    b = Repository(s).upsert_business(BusinessRecord(name=name, provider="t", place_id="P-" + name, **kw))
    s.commit()
    return b


def seed(sf, preview_url="https://bobs-septic-dallas-1.previews.example.com"):
    with sf() as s:
        repo = Repository(s)
        bob = add(s, "Bob's Septic", website="https://bobseptic.com", phone="2145550001", rating=4.9,
                  review_count=310, zip_code="75201", city="Dallas", state="TX", categories=["Septic system service"])
        bob.owner_name, bob.best_email, bob.email_status = "Bob Miller", "bob@bobseptic.com", "valid"
        bob.website_score, bob.website_grade, bob.ads_status = 22, "F", "Active"
        rival = add(s, "Rival Septic", website="https://rival.com", phone="2145550002", rating=4.6, review_count=120,
                    zip_code="75201", city="Dallas", state="TX")
        rival.website_score, rival.ads_status, rival.lsa = 88, "Active", True
        weak = add(s, "Weak Co", website="https://weak.com", phone="2145550003", review_count=5, zip_code="75201")
        weak.website_score = 30
        repo.record_search(SearchQuery("septic", "75201"), "t", [(bob, 1), (rival, 2), (weak, 3)], exhausted=True,
                           api_calls=1)
        repo.set_enrichment(bob.id, "website", {"score": 22, "reasons": [
            "not mobile friendly (no mobile viewport tag, phones show a shrunken desktop page)",
            "slow on mobile (PageSpeed 23/100, loads in 9.1s)",
            "copyright 2011 (~15 years without updates)",
            "no click-to-call button, no quote/booking form"], "pagespeed": {"performance": 23}})
        repo.set_enrichment(bob.id, "ads", {"status": "Active", "evidence": ["Google search ad live now (position 1)"]})
        repo.set_enrichment(bob.id, "preview", {"url": preview_url, "screenshot": None, "path": "x"})
        s.commit()
        return bob.id, rival.id, weak.id


def cfg(**kw):
    base = {"sender_name": "Arslan", "agency": "Acme Web", "sender_email": "arslan@getacme-web.com",
            "physical_address": "123 Main St, Austin TX 78701",
            "unsubscribe_line": "Not interested? Reply \"unsubscribe\" and I won't email you again.",
            "followup_days": [3, 7, 14], "max_words": 150, "offer": "", "name_competitors": False}
    base.update(kw)
    return base


# ── facts ────────────────────────────────────────────────────────────
def test_facts_come_from_real_findings(session_factory):
    bob_id, *_ = seed(session_factory)
    with session_factory() as s:
        f = ofacts.gather(s, s.get(Business, bob_id), cfg(), {})
    assert f.first_name == "Bob" and f.keyword == "septic" and f.pagespeed == 23
    assert f.issue_kinds[:3] == ["mobile", "speed", "outdated"]
    assert "shrunken desktop page" in f.issues[0] and "23/100" in f.issues[1] and "since around 2011" in f.issues[2]
    assert f.issues[3] == "the site has no click-to-call button, no quote/booking form"
    c = f.competitor
    assert c and c.name == "" and c.site_score == 88 and c.better_on == ["website", "local services ads"]
    assert f.ads_evidence and f.preview_url.startswith("https://")
    with session_factory() as s:
        named = ofacts.gather(s, s.get(Business, bob_id), cfg(name_competitors=True), {})
    assert named.competitor.name == "Rival Septic"
    assert ofacts.first_name("Acme Plumbing LLC") is None and ofacts.first_name("Mr Smith") is None
    assert ofacts.first_name("maria lopez") == "Maria"


# ── templates ────────────────────────────────────────────────────────
def test_template_drafts_are_personal_and_safe(session_factory):
    bob_id, *_ = seed(session_factory)
    with session_factory() as s:
        f = ofacts.gather(s, s.get(Business, bob_id), cfg(), {})
    d = compose.template_drafts(f, cfg())
    assert [v["angle"] for v in d["variants"]] == ["short", "detailed", "competitor"]
    short, detailed, comp = (v["body"] for v in d["variants"])
    assert short.startswith("Hi Bob,") and compose.LINK in short and "310 Google reviews" in short
    assert "- it scores 23/100 on Google's mobile speed test" in detailed and "running Google Ads" in detailed
    assert "I noticed another septic system service company nearby has" in comp and "88/100" in comp
    assert "more Google reviews (310 vs 120)" in comp
    assert [fu["day"] for fu in d["followups"]] == [3, 7, 14]
    assert "viewport" in d["followups"][1]["body"]                # tip matches the main problem (mobile)
    for text in [v["body"] for v in d["variants"]] + [fu["body"] for fu in d["followups"]]:
        assert not compose.RISKY.search(text)
    for v in d["variants"]:
        assert not v["subject"].lower().startswith(("re:", "fwd"))


def test_no_website_and_no_preview_wording(session_factory):
    with session_factory() as s:
        b = add(s, "Solo Pumping", phone="2145550009", city="Plano", categories=["Septic system service"])
        f = ofacts.gather(s, b, cfg(), {})
    d = compose.template_drafts(f, cfg())
    short = d["variants"][0]["body"]
    assert f.no_website and "no website to check" in short and "Hi Solo Pumping team," in short
    assert compose.LINK not in short and "mock up a free concept" in short


def test_final_body_adds_signature_and_can_spam_footer():
    body = compose.final_body(f"Hi Bob,\n\nLook: {compose.LINK}", cfg(), preview_url="https://p.example.com")
    assert "Look: https://p.example.com" in body and body.index("Arslan") < body.index("123 Main St")
    assert body.rstrip().endswith("I won't email you again.")
    missing = compose.final_body("Hi", cfg(physical_address=""))
    assert compose.ADDRESS_MISSING in missing


# ── AI drafts ────────────────────────────────────────────────────────
class FakeLLM(LLM):
    name = "fake"

    def __init__(self, reply=None, error=None):
        super().__init__("fake", {})
        self.reply, self.error, self.prompts = reply, error, []

    async def complete(self, prompt, *, system=None, images=None, max_tokens=2000):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return json.dumps(self.reply)


AI_REPLY = {
    "variants": [
        {"angle": "short", "subject": "Re: quick question",
         "body": "Hi Bob,\n\nI was looking at septic companies in Dallas and found Bob's Septic. Your 310 reviews are great, "
                 "but your site shows a shrunken desktop page on phones. We guarantee you'll rank #1 on Google. "
                 "70% of customers search on mobile. I mocked up a concept: [PREVIEW_LINK]\n\nSee also https://spam.example.com "
                 "for more.\n\nWorth a look?\n\nBest,\nArslan"},
        {"angle": "detailed", "subject": "A few fixes for Bob's Septic",
         "body": "Hi Bob,\n\nI checked bobseptic.com and found three things costing you calls:\n- shrunken desktop page on phones\n"
                 "- 23/100 on Google's mobile speed test\n- not updated since around 2011\n\nYou're running Google Ads too, "
                 "so slow pages waste paid clicks. I built a concept so you can compare side by side.\n\nOpen to a "
                 "10-minute call this week?"},
        {"angle": "competitor", "subject": "x"},     # empty body -> template fallback
    ],
    "followups": [{"body": "Hi Bob,\n\nBumping this in case it got buried. The concept is still up at [PREVIEW_LINK] "
                           "and I'm happy to walk you through it."}, {"body": "too short"}],
}


def test_ai_drafts_are_filtered_and_fall_back(session_factory):
    bob_id, *_ = seed(session_factory)
    with session_factory() as s:
        f = ofacts.gather(s, s.get(Business, bob_id), cfg(), {})
    llm = FakeLLM(AI_REPLY)
    d = run(compose.make_drafts(f, cfg(), llm))
    assert d["source"] == "ai" and "Hi Bob," in llm.prompts[0] and "[PREVIEW_LINK]" in llm.prompts[0]
    assert "bob@bobseptic.com" not in llm.prompts[0]                 # recipient address never goes to the model
    short = d["variants"][0]
    assert short["subject"] == "quick question"                       # fake "Re:" stripped
    assert "guarantee" not in short["body"] and "70%" not in short["body"] and "spam.example.com" not in short["body"]
    assert "shrunken desktop page" in short["body"] and compose.LINK in short["body"]
    assert not short["body"].rstrip().endswith(("Best,", "Arslan"))
    detailed = d["variants"][1]
    assert detailed["source"] == "ai" and compose.LINK in detailed["body"]   # link appended when the model forgot it
    assert d["variants"][2]["source"] == "template"
    assert [fu["source"] for fu in d["followups"]] == ["ai", "template", "template"]

    broken = run(compose.make_drafts(f, cfg(), FakeLLM(error=LLMError("no key"))))
    assert broken["source"] == "template" and len(broken["variants"]) == 3


# ── service ──────────────────────────────────────────────────────────
def test_service_drafts_skip_contacted_and_track_usage(settings, session_factory, credits):
    bob_id, rival_id, weak_id = seed(session_factory)
    with session_factory() as s:
        crm.set_status(s, weak_id, "Emailed")
        s.commit()
    svc = LeadService(settings, session_factory, None, credits)
    rows = run(svc.draft_outreach([bob_id, weak_id], use_ai=False))
    assert rows[0]["source"] == "template" and rows[0]["to"] == "bob@bobseptic.com"
    assert "already contacted" in rows[1]["skipped"]
    assert any("physical_address" in w for w in rows[0]["warnings"])   # default config has no address yet
    with session_factory() as s:
        payload = Repository(s).latest_enrichment(bob_id, "outreach", fresh_only=False).payload
    assert len(payload["variants"]) == 3 and payload["facts"]["site_score"] == 22


# ── approve + send ───────────────────────────────────────────────────
def sending_settings(settings, **send):
    sections = dict(settings.sections)
    sections["outreach"] = {**cfg(), "sending": {"enabled": True, "smtp_host": "smtp.test", "max_per_day": 30,
                                                  "min_delay_seconds": 5, **send}}
    return replace(settings, sections=sections)


def draft(settings, sf, *ids):
    run(LeadService(settings, sf, None).draft_outreach(list(ids), use_ai=False))


def test_approve_guards(settings, session_factory):
    bob_id, rival_id, weak_id = seed(session_factory)
    draft(settings, session_factory, bob_id, rival_id)
    with session_factory() as s:
        with pytest.raises(mail.OutreachError, match="no email"):
            mail.approve(s, rival_id, "short")
        with pytest.raises(mail.OutreachError, match="No drafts"):
            mail.approve(s, weak_id, "short")
        rows = mail.approve(s, bob_id, "detailed")
        assert [r.status for r in rows] == ["approved", "scheduled", "scheduled", "scheduled"]
        assert rows[1].subject == "Re: " + rows[0].subject and rows[3].delay_days == 14
        again = mail.approve(s, bob_id, "short", followups=False)
        s.commit()
        assert [r.status for r in rows] == ["cancelled"] * 4 and len(again) == 1
        mail.suppress(s, "rival.com", "asked")
        with pytest.raises(mail.OutreachError, match="do-not-contact"):
            mail.approve(s, rival_id, "short", to="owner@rival.com")
        crm.set_status(s, bob_id, "Emailed")
        with pytest.raises(crm.AlreadyContacted):
            mail.approve(s, bob_id, "short")


def test_sender_refuses_without_opt_in(settings, session_factory):
    bob_id, *_ = seed(session_factory)
    draft(settings, session_factory, bob_id)
    with session_factory() as s:
        mail.approve(s, bob_id, "short")
        s.commit()
    sent = []
    report = run(mail.Sender(settings, session_factory, send_fn=sent.append).run())
    assert sent == [] and "sending is off" in report.stopped and "physical_address" in report.stopped
    bad = sending_settings(settings, allowed_from_domains=["other.com"])
    assert "allowed_from_domains" in run(mail.Sender(bad, session_factory, send_fn=sent.append).run()).stopped


def test_sender_sends_throttles_and_runs_followups(settings, session_factory):
    bob_id, rival_id, weak_id = seed(session_factory)
    st = sending_settings(settings)
    with session_factory() as s:
        s.get(Business, weak_id).best_email = "hi@weak.com"
        s.commit()
    draft(st, session_factory, bob_id, weak_id)
    with session_factory() as s:
        mail.approve(s, bob_id, "short")
        mail.approve(s, weak_id, "detailed", followups=False)
        s.commit()
    sent, sleeps = [], []

    async def fake_sleep(sec):
        sleeps.append(sec)

    sender = mail.Sender(st, session_factory, send_fn=sent.append, sleep=fake_sleep)
    report = run(sender.run())
    assert len(sent) == 2 and len(report.sent) == 2 and len(sleeps) == 1 and 5 <= sleeps[0] <= 7.5
    first = sent[0]
    assert first["To"] == "bob@bobseptic.com" and first["List-Unsubscribe"] == "<mailto:arslan@getacme-web.com?subject=unsubscribe>"
    text = first.get_content()
    assert "https://bobs-septic-dallas-1.previews.example.com" in text and "123 Main St" in text and "unsubscribe" in text
    with session_factory() as s:
        assert crm.current_status(s, bob_id) == "Emailed"
        fus = list(s.scalars(select(OutboundEmail).where(OutboundEmail.business_id == bob_id, OutboundEmail.step > 0)))
        assert all(f.scheduled_for and f.status == "scheduled" for f in fus)
        fus[0].scheduled_for = utcnow() - timedelta(minutes=1)      # time passes: follow-up 1 is due
        s.commit()
    assert run(sender.run()).sent == ["bob@bobseptic.com step 1"]
    fu = sent[-1]
    assert fu["In-Reply-To"] == first["Message-ID"] and fu["Subject"].startswith("Re: ")
    with session_factory() as s:                                     # lead replies -> no more follow-ups
        crm.set_status(s, bob_id, "Replied")
        for f in s.scalars(select(OutboundEmail).where(OutboundEmail.step == 2)):
            f.scheduled_for = utcnow() - timedelta(minutes=1)
        s.commit()
    report = run(sender.run())
    assert report.sent == [] and "follow-ups stop" in report.skipped[0]


def test_sender_daily_cap_and_smtp_failure(settings, session_factory):
    bob_id, rival_id, weak_id = seed(session_factory)
    st = sending_settings(settings, max_per_day=1, min_delay_seconds=0)
    with session_factory() as s:
        s.get(Business, weak_id).best_email = "hi@weak.com"
        s.commit()
    draft(st, session_factory, bob_id, weak_id)
    with session_factory() as s:
        mail.approve(s, bob_id, "short", followups=False)
        mail.approve(s, weak_id, "short", followups=False)
        s.commit()

    def boom(msg):
        raise OSError("connection refused")

    report = run(mail.Sender(st, session_factory, send_fn=boom).run())
    assert len(report.failed) == 2
    with session_factory() as s:
        for r in s.scalars(select(OutboundEmail)):
            r.status = "approved"
        s.commit()
    report = run(mail.Sender(st, session_factory, send_fn=lambda m: None).run())
    assert len(report.sent) == 1 and "daily limit" in report.stopped


# ── inbox ────────────────────────────────────────────────────────────
class FakeIMAP:
    def __init__(self, inbox):
        self.inbox, self.appended = inbox, []

    def select(self, *a, **k):
        return "OK", [b"1"]

    def search(self, charset, criteria):
        sender = criteria.split('FROM "')[1].split('"')[0]
        ids = [str(i).encode() for i, m in enumerate(self.inbox) if sender in m["From"].lower()]
        return "OK", [b" ".join(ids)]

    def fetch(self, num, what):
        return "OK", [(b"1", self.inbox[int(num)].as_bytes())]

    def append(self, folder, flags, date, data):
        self.appended.append((folder, flags, data))
        return "OK", [b""]


def _mail(frm, subject, body):
    m = email.message.EmailMessage()
    m["From"], m["Subject"] = frm, subject
    m.set_content(body)
    return m


def test_check_replies_unsubscribe_and_bounce(settings, session_factory):
    bob_id, rival_id, weak_id = seed(session_factory)
    with session_factory() as s:
        for bid, addr in ((bob_id, "bob@bobseptic.com"), (rival_id, "owner@rival.com"), (weak_id, "hi@weak.com")):
            s.add(OutboundEmail(business_id=bid, to_email=addr, step=0, subject="x", body="y", status="sent",
                                sent_at=utcnow()))
            s.add(OutboundEmail(business_id=bid, to_email=addr, step=1, subject="Re: x", body="y", status="scheduled"))
            crm.set_status(s, bid, "Emailed", force=True)
        s.get(Business, weak_id).best_email = "hi@weak.com"
        s.commit()
    conn = FakeIMAP([_mail("Bob <bob@bobseptic.com>", "Re: x", "Sounds good, call me Tuesday."),
                     _mail("owner@rival.com", "Re: x", "Please unsubscribe me."),
                     _mail("MAILER-DAEMON@mx.test", "Undelivered", "Delivery to hi@weak.com failed: no such user")])
    with session_factory() as s:
        out = mail.check_replies(s, conn)
        s.commit()
    assert out == {"replied": ["bob@bobseptic.com"], "unsubscribed": ["owner@rival.com"], "bounced": ["hi@weak.com"]}
    with session_factory() as s:
        assert crm.current_status(s, bob_id) == "Replied" and crm.current_status(s, rival_id) == "Lost"
        assert s.get(Business, weak_id).email_status == "invalid"
        assert {x.value for x in s.scalars(select(Suppression))} == {"owner@rival.com", "hi@weak.com"}
        assert not list(s.scalars(select(OutboundEmail).where(OutboundEmail.status == "scheduled")))


# ── exports / push ───────────────────────────────────────────────────
def test_exports_csv_eml_imap_and_webhook(settings, session_factory, make_http, tmp_path):
    bob_id, rival_id, weak_id = seed(session_factory)
    with session_factory() as s:                       # weak: no link, preview screenshot only
        shot = tmp_path / "desktop.jpg"
        shot.write_bytes(b"\xff\xd8\xff" + b"0" * 50)
        Repository(s).set_enrichment(weak_id, "preview", {"url": None, "screenshot": str(shot)})
        s.get(Business, weak_id).best_email = "hi@weak.com"
        s.commit()
    draft(settings, session_factory, bob_id, weak_id, rival_id)
    with session_factory() as s:
        crm.set_status(s, rival_id, "Emailed")
        s.commit()
        items, skipped = ox.load_draft_items(s)
    assert [b.name for b, _ in items] == ["Bob's Septic", "Weak Co"] and skipped == ["Rival Septic"]
    rows = ox.merge_rows(items, cfg(), "best")
    assert rows[0]["angle"] == "competitor" and rows[0]["first_name"] == "Bob"
    data = list(csv.DictReader(io.StringIO(ox.to_merge_csv(rows).lstrip("﻿"))))
    assert data[0]["email"] == "bob@bobseptic.com" and "123 Main St" in data[0]["body"]
    assert data[0]["followup_3_day"] == "14" and data[0]["followup_1_subject"].startswith("Re: ")
    assert "[PREVIEW_LINK]" not in data[0]["body"] + data[0]["followup_1_body"]

    files = ox.write_eml(items, cfg(), "short", tmp_path / "eml")
    msg = email.message_from_bytes(files[1].read_bytes())
    assert msg["X-Unsent"] == "1" and msg["To"] == "hi@weak.com"
    assert [p.get_filename() for p in msg.walk() if p.get_filename()] == ["concept-preview.jpg"]  # body says attached

    conn = FakeIMAP([])
    assert mail.push_imap_drafts(conn, [m for _, m in ox.draft_messages(items, cfg(), "short")], "Drafts") == 2
    assert conn.appended[0][1] == r"(\Draft)"

    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200)

    async def go():
        async with make_http(handler) as http:
            return await ox.push_webhook(http, "https://hooks.example.com/x", items, cfg(), "detailed")
    assert run(go()) == 2 and seen[0]["type"] == "leadengine.draft" and seen[0]["angle"] == "detailed"


# ── dashboard ────────────────────────────────────────────────────────
def test_dashboard_draft_approve_outbox(settings, session_factory):
    from fastapi.testclient import TestClient

    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        sf = app.state.sf
        bob_id, *_ = seed(sf)
        r = c.post(f"/leads/{bob_id}/draft", data={"ai": "true"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
        draft(settings, sf, bob_id)
        page = c.get(f"/leads/{bob_id}").text
        assert "Approve this one" in page and "Hi Bob," in page and "Follow-up sequence" in page
        r = c.post(f"/leads/{bob_id}/approve", data={"angle": "short", "followups": "true"}, follow_redirects=True)
        assert "Outbox" in r.text and "sending is off" in r.text and "bob@bobseptic.com" in r.text
        refused = c.post("/outbox/send", data={"confirm": "true"}, follow_redirects=True)
        assert "sending is off" in refused.text
        with sf() as s:
            row_id = s.scalar(select(OutboundEmail.id).where(OutboundEmail.step == 0))
        c.post(f"/outbox/{row_id}/cancel")
        c.post("/suppress", data={"value": "spam.com", "reason": "manual"})
        with sf() as s:
            assert s.get(OutboundEmail, row_id).status == "cancelled"
            assert s.scalar(select(Suppression.value)) == "spam.com"
        assert "bob@bobseptic.com" in c.get("/outreach/drafts.csv").text
