import asyncio
from dataclasses import replace

import httpx
import pytest

from leadengine.db import Business, Email, Repository
from leadengine.enrich.emails import build_email_finder
from leadengine.enrich.emails.crawl import CrawlResult, Page, SiteCrawler, score_link
from leadengine.enrich.emails.extract import decode_cfemail, extract_candidates
from leadengine.enrich.emails.filters import is_free_mail, is_role, reject_reason, same_site
from leadengine.enrich.emails.guess import detect_pattern, guess_emails
from leadengine.enrich.emails.people import people_from_html, rank_people
from leadengine.enrich.emails.verify import EmailVerifier, Verification
from leadengine.models import BusinessRecord
from leadengine.service import LeadService
from tests.fake_web import BAD, EXPECTED, cf_encode, httpx_handler


def run(coro):
    return asyncio.run(coro)


def emails_of(html):
    return {(c.email, c.method) for c in extract_candidates(html)}


# ── extraction ───────────────────────────────────────────────────────
def test_cloudflare_roundtrip():
    assert decode_cfemail(cf_encode("hello@greenlawn.com")) == "hello@greenlawn.com"
    assert decode_cfemail("zz") is None and decode_cfemail("5a") is None


@pytest.mark.parametrize("html, expected", [
    ("Email: john [at] acme-roofing [dot] com", ("john@acme-roofing.com", "obfuscated")),
    ("mike(at)acme.com", ("mike@acme.com", "obfuscated")),
    ("sales at acme dot com", ("sales@acme.com", "obfuscated")),
    ('<a href="mailto:info%40acme.com?subject=Quote">x</a>', ("info@acme.com", "mailto")),
    ("&#105;&#110;&#102;&#111;&#64;acme&#46;com", ("info@acme.com", "text")),
    ("<script>var e='jane'+'@'+'acme.com'</script>", ("jane@acme.com", "script")),
    ('<script type="application/ld+json">{"email":"mailto:office@acme.com"}</script>', ("office@acme.com", "jsonld")),
    ("<div data-email='crew@acme.com'></div>", ("crew@acme.com", "attr")),
    ("<!-- admin@acme.com -->", ("admin@acme.com", "comment")),
])
def test_extraction_methods(html, expected):
    assert expected in emails_of(html)


@pytest.mark.parametrize("html", ["Find us at facebook.com/acme", "Visit us at acme.com today", "We meet at noon dot"])
def test_plain_at_in_sentences_is_not_an_email(html):
    assert emails_of(html) == set()


# ── filters ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("email, reason", [
    ("8f2a9c1d7e6b4a3f9c2d1e0f@o123456.ingest.sentry.io", "platform/placeholder domain"),
    ("logo@2x.png", "asset filename"),
    ("you@example.com", "platform/placeholder domain"),
    ("yourname@acme.com", "placeholder"),
    ("noreply@acme.com", "no-reply/system"),
    ("a1b2c3d4e5f6a7b8c9@acme.com", "hash/token"),
    ("bad..dots@acme.com", "syntax"),
    ("info@acme.com", None),
    ("treemasters.tx@gmail.com", None),
])
def test_reject_reason(email, reason):
    assert reject_reason(email) == reason


def test_role_free_same_site():
    assert is_role("info@acme.com") and is_role("dispatch2@acme.com") and not is_role("john@acme.com")
    assert is_free_mail("x@gmail.com") and not is_free_mail("x@acme.com")
    assert same_site("a@mail.acme.com", {"acme.com"}) and same_site("a@acme.com", {"www.acme.com"})
    assert not same_site("a@webguys.net", {"acme.com"})


# ── people & guesses ─────────────────────────────────────────────────
def test_people_extraction():
    html = ("<p>Call John Smith, Owner today. Founded in 1998 by Maria Lopez.</p><p>Meet our team!</p>"
            "<p>Mike O'Brien - General Manager</p>"
            '<script type="application/ld+json">{"founder":{"@type":"Person","name":"Dave K. Miller"}}</script>')
    names = [(p.full, p.title) for p in rank_people(people_from_html(html))]
    assert names[0] == ("Dave Miller", "founder")
    assert ("John Smith", "owner") in names and ("Mike O'Brien", "general manager") in names
    assert not any("team" in n.lower() for n, _ in names)


def test_guesses_and_learned_pattern():
    guesses = dict(guess_emails("acme.com", [("John", "Smith")], []))
    assert {"john@acme.com", "john.smith@acme.com", "jsmith@acme.com", "info@acme.com"} <= set(guesses)
    assert detect_pattern(["mary.jones@acme.com"], [("Mary", "Jones"), ("John", "Smith")]) == "first.last"
    learned = guess_emails("acme.com", [("Mary", "Jones"), ("John", "Smith")], ["mary.jones@acme.com"])
    assert [e for e, _ in learned] == ["john.smith@acme.com"]          # pattern applied, no generic guesses
    assert "pattern seen on site" in learned[0][1]


# ── crawler ──────────────────────────────────────────────────────────
def test_score_link_variants():
    assert score_link("/reach-out", "Get in Touch")[1] == "contact"
    assert score_link("/contact_us", "")[1] == "contact"
    assert score_link("/privacy-policy", "Privacy")[1] == "legal"
    assert score_link("/services", "Our Services") is None


def test_plan_prefers_linked_pages_and_only_guesses_missing_types():
    home = Page("https://acme.com/", "https://acme.com/", 200,
                "<a href='/get-a-quote'>Free Quote</a><a href='/about-our-family'>About</a>"
                "<footer><a href='/privacy'>Privacy</a><a href='https://facebook.com/acme?ref=x'>FB</a></footer>")
    result = CrawlResult("acme.com", site_domains={"acme.com"})
    plan = SiteCrawler(None).plan(home, result)
    paths = [u.replace("https://acme.com", "") for u, _ in plan]
    assert paths[:3] == ["/about-our-family", "/get-a-quote", "/privacy"]
    assert "/contact" not in paths            # a contact-type page (quote) was linked
    assert result.facebook_urls == ["https://facebook.com/acme"]


def _finder(settings, make_http, verify="none", verifier=None):
    s = replace(settings, sections={**settings.sections, "emails": {"verify": verify, "max_pages": 8}})
    http = make_http(httpx_handler())
    return http, build_email_finder(s, http, insecure_transport=httpx.MockTransport(httpx_handler(insecure=True)),
                                    verifier=verifier)


def test_finder_gets_all_benchmark_sites_right(settings, make_http):
    async def go():
        http, finder = _finder(settings, make_http)
        async with http:
            out = {site: await finder.find(site) for site in EXPECTED}
            await finder.crawler.aclose()
            return out

    reports = run(go())
    for site, want in EXPECTED.items():
        best = reports[site].best
        assert (best.email if best else None) in (want or {None}), site
        assert not any(e.email in BAD and e.confidence >= 30 for e in reports[site].emails), site
    pest = {e.email: e for e in reports["oldsite-pest.com"].emails}
    assert pest["design@webguys.net"].confidence < 30 < pest["office@oldsite-pest.com"].confidence
    assert reports["ssl-broken.com"].ssl_error and reports["redirect-site.com"].redirected_to == "newbrand.com"
    assert reports["smithelectric.com"].people[0]["name"] == "John Smith"
    guesses = [e for e in reports["noemail-carpet.com"].emails if e.is_guess]
    assert guesses and all(g.confidence <= 35 for g in guesses)
    assert reports["sentry-site.com"].rejected  # junk was seen and rejected, not returned


class StubVerifier(EmailVerifier):
    def __init__(self, statuses):
        super().__init__({"verify": "mx"})
        self.statuses = statuses

    async def verify(self, email):
        return Verification(self.statuses.get(email, "unknown"), "stub", "smtp")


def test_verification_changes_confidence(settings, make_http):
    v = StubVerifier({"info@joesplumbing.com": "invalid", "john@smithelectric.com": "valid",
                      "john.smith@smithelectric.com": "valid", "hello@greenlawn.com": "catch_all"})

    async def go():
        http, finder = _finder(settings, make_http, verifier=v)
        async with http:
            r = [await finder.find(s) for s in ("joesplumbing.com", "smithelectric.com", "greenlawn.com")]
            await finder.crawler.aclose()
            return r

    joes, smith, green = run(go())
    assert joes.best is None                                    # only email failed verification
    assert smith.best.email == "john@smithelectric.com" and smith.best.confidence == 100
    guess = next(e for e in smith.emails if e.email == "john.smith@smithelectric.com")
    assert guess.is_guess and guess.confidence > 35             # verified guesses may rise
    assert green.best.confidence <= 75                          # catch-all capped


# ── verifier ─────────────────────────────────────────────────────────
def test_mx_levels_and_cache(session_factory):
    calls = []

    class V(EmailVerifier):
        async def _lookup_mx(self, domain):
            calls.append(domain)
            return {"acme.com": ["mx.acme.com"], "nomail.com": [], "dnsfail.com": None}[domain]

    async def go():
        with session_factory() as s:
            v = V({"verify": "mx"}, repo=Repository(s))
            out = [await v.verify(e) for e in ("a@acme.com", "b@acme.com", "x@nomail.com", "x@dnsfail.com",
                                                   "x@mailinator.com", "logo@2x.png")]
            s.commit()
            v2 = V({"verify": "mx"}, repo=Repository(s))      # new verifier: DB cache, no lookup
            again = await v2.verify("c@acme.com")
            return out, again

    out, again = run(go())
    assert [o.status for o in out] == ["unknown", "unknown", "invalid", "unknown", "invalid", "invalid"]
    assert out[0].mx_hosts == ["mx.acme.com"] and "MX ok" in out[0].reason
    assert calls.count("acme.com") == 1 and again.mx_hosts == ["mx.acme.com"]


class FakeSMTP:
    """Minimal SMTP server: accepts known mailboxes (or everything when catch_all)."""

    def __init__(self, mailboxes, catch_all=False):
        self.mailboxes, self.catch_all, self.rcpts = set(mailboxes), catch_all, []

    async def handle(self, reader, writer):
        writer.write(b"220 fake ESMTP\r\n")
        await writer.drain()
        while line := await reader.readline():
            cmd = line.decode().strip()
            if cmd.upper().startswith("EHLO"):
                writer.write(b"250-fake\r\n250 OK\r\n")
            elif cmd.upper().startswith("RCPT TO:"):
                addr = cmd[8:].strip("<> ")
                self.rcpts.append(addr)
                writer.write(b"250 OK\r\n" if self.catch_all or addr in self.mailboxes else b"550 no such user\r\n")
            elif cmd.upper() == "QUIT":
                writer.write(b"221 bye\r\n")
                await writer.drain()
                break
            else:
                writer.write(b"250 OK\r\n")
            await writer.drain()
        writer.close()


@pytest.mark.parametrize("catch_all, email, status", [
    (False, "real@acme.com", "valid"), (False, "nobody@acme.com", "invalid"), (True, "nobody@acme.com", "catch_all"),
])
def test_smtp_probe_and_catch_all(catch_all, email, status):
    async def go():
        fake = FakeSMTP({"real@acme.com"}, catch_all)
        server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        class V(EmailVerifier):
            async def _lookup_mx(self, domain):
                return ["127.0.0.1"]

        async with server:
            v = V({"verify": "smtp", "smtp_port": port, "smtp_helo": "me.test", "smtp_timeout_seconds": 3})
            return await v.verify(email), fake

    result, fake = run(go())
    assert result.status == status and result.level == "smtp"
    assert len(fake.rcpts) == 2              # the address + a random catch-all probe, one connection


def test_smtp_blocked_port_is_unknown():
    class V(EmailVerifier):
        async def _lookup_mx(self, domain):
            return ["127.0.0.1"]

    v = V({"verify": "smtp", "smtp_port": 1, "smtp_timeout_seconds": 1})
    r = run(v.verify("a@acme.com"))
    assert r.status == "unknown" and "port 25" in r.reason


def test_reacher(make_http):
    def handler(request):
        assert request.headers["x-reacher-secret"] == "s3cret"
        return httpx.Response(200, json={"is_reachable": "safe", "smtp": {"is_catch_all": False}})

    async def go():
        async with make_http(handler) as http:
            v = EmailVerifier({"verify": "reacher", "reacher_url": "http://reacher.local:8080"}, http,
                              reacher_secret="s3cret")
            return await v.verify("john@acme.com")

    r = run(go())
    assert r.status == "valid" and r.level == "reacher"


# ── service: stores results, caches per business ─────────────────────
def test_service_find_emails_stores_and_caches(settings, session_factory, make_http):
    s = replace(settings, sections={**settings.sections, "emails": {"verify": "none", "concurrency": 3}})
    with session_factory() as db:
        repo = Repository(db)
        ids = [repo.upsert_business(BusinessRecord(name=n, provider="t", place_id=f"P{i}", website=w)).id
               for i, (n, w) in enumerate([("Smith Electric", "https://smithelectric.com"),
                                           ("Old Pest", "oldsite-pest.com"), ("No Site", None),
                                           ("Ghost", "https://doesnotexist.example")])]
        db.commit()

    async def go():
        async with make_http(httpx_handler()) as http:
            service = LeadService(s, session_factory, http)
            first = await service.find_emails(ids)
            second = await service.find_emails(ids)
            return first, second

    first, second = run(go())
    assert first.checked == 3 and first.no_website == 1 and first.with_email == 2 and first.unreachable == 1
    assert second.checked == 0 and second.cached == 3
    with session_factory() as db:
        smith = db.get(Business, ids[0])
        assert smith.best_email == "john@smithelectric.com" and smith.owner_name == "John Smith"
        assert db.query(Email).filter_by(business_id=ids[0], is_guess=True).count() >= 1
        assert Repository(db).latest_enrichment(ids[0], "site_fetch").payload["reachable"] is True


def test_unreachable_site_is_retried_after_a_day(settings, session_factory, make_http):
    s = replace(settings, sections={**settings.sections, "emails": {"verify": "none"}})
    with session_factory() as db:
        bid = Repository(db).upsert_business(BusinessRecord(name="Down", provider="t", place_id="PD",
                                                            website="https://down.example")).id
        db.commit()

    async def go():
        async with make_http(httpx_handler()) as http:
            await LeadService(s, session_factory, http).find_emails([bid])

    run(go())
    with session_factory() as db:
        row = Repository(db).latest_enrichment(bid, "emails")
        assert (row.expires_at - row.fetched_at).days == 1
