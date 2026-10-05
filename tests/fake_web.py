"""20 small-business websites in memory, for the email benchmark and tests (no internet).

Each site copies a pattern common on real local-business sites. ``EXPECTED`` holds the
acceptable best answers; ``BAD`` holds addresses that must never be the answer.
Usable from httpx (``httpx_handler``) and from the legacy ``requests``-based v3 code
(``FakeRequests``).
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx


def cf_encode(email: str, key: int = 0x5A) -> str:
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in email)


def page(body: str, nav: str = "", footer: str = "", head: str = "") -> str:
    return (f"<!doctype html><html><head><title>Site</title>{head}</head><body>"
            f"<nav><a href='/'>Home</a>{nav}</nav><main>{body}</main><footer>{footer}</footer></body></html>")


NAV = "<a href='/services'>Services</a><a href='/about-us'>About Us</a><a href='/contact-us'>Contact Us</a>"

SITES: dict[str, dict[str, str]] = {
    # 1 email in the homepage footer (easy case, v3 handles it)
    "joesplumbing.com": {"/": page("<h1>Joe's Plumbing</h1>", NAV, "Call (214) 555-0101 · info@joesplumbing.com")},
    # 2 email only as mailto on the linked contact page
    "acme-roofing.com": {
        "/": page("<h1>Acme Roofing</h1>", NAV),
        "/contact-us": page("<a href='mailto:estimates@acme-roofing.com'>Email us</a>", NAV),
        "/about-us": page("Family owned since 1988.", NAV),
    },
    # 3 Cloudflare-protected email on the contact page
    "greenlawn.com": {
        "/": page("<h1>Green Lawn</h1>", NAV),
        "/contact-us": page(f"<a href='/cdn-cgi/l/email-protection#{cf_encode('hello@greenlawn.com')}'>"
                            f"<span class='__cf_email__' data-cfemail='{cf_encode('hello@greenlawn.com')}'>"
                            "[email&#160;protected]</span></a>", NAV),
    },
    # 4 email only in schema.org JSON-LD
    "bestdumpsters.com": {"/": page("<h1>Best Dumpsters</h1>", NAV, head=(
        '<script type="application/ld+json">{"@context":"https://schema.org","@type":"LocalBusiness",'
        '"name":"Best Dumpsters","email":"mailto:rentals@bestdumpsters.com","telephone":"+1-214-555-0144"}</script>'))},
    # 5 human obfuscation on the about page
    "cityhvac.com": {
        "/": page("<h1>City HVAC</h1>", NAV),
        "/about-us": page("Questions? write to service [at] cityhvac [dot] com", NAV),
    },
    # 6 web designer email in the homepage footer, real one on the contact page
    "oldsite-pest.com": {
        "/": page("<h1>Old Site Pest Control</h1>", NAV, "Website by WebGuys · design@webguys.net"),
        "/contact-us": page("Email: office@oldsite-pest.com", NAV),
    },
    # 7 the business really uses gmail
    "tree-masters.com": {"/": page("<h1>Tree Masters</h1>Email treemasters.tx@gmail.com for a quote", NAV)},
    # 8 email only on the Facebook page
    "poolpros.com": {"/": page("<h1>Pool Pros</h1>", NAV,
                              "<a href='https://www.facebook.com/poolprosdallas'>Facebook</a>")},
    # 9 owner name on about page, personal email on contact page
    "smithelectric.com": {
        "/": page("<h1>Smith Electric</h1>", NAV),
        "/about-us": page("<p>John Smith, Owner, has 30 years of experience.</p>", NAV),
        "/contact-us": page("Reach John directly: john@smithelectric.com", NAV),
    },
    # 10 junk: Sentry DSN + retina image name; real email on contact page
    "sentry-site.com": {
        "/": page("<img src='/logo@2x.png' alt='logo'><h1>Sentry Site Movers</h1>", NAV,
                  head="<script>Sentry.init({dsn:'https://8f2a9c1d7e6b4a3f9c2d1e0f@o123456.ingest.sentry.io/42'})</script>"),
        "/contact-us": page("hello@sentry-site.com", NAV),
    },
    # 11 email assembled in JavaScript on the contact page
    "js-email.com": {
        "/": page("<h1>JS Email Cleaning</h1>", NAV),
        "/contact-us": page("<script>var m='info'+'@'+'js-email.com';document.write(m)</script>", NAV),
    },
    # 12 only the privacy policy (footer link) has an email
    "privacy-only.com": {
        "/": page("<h1>Privacy Only Landscaping</h1>", "", "<a href='/privacy-policy'>Privacy Policy</a>"),
        "/privacy-policy": page("Contact our privacy officer at privacy@privacy-only.com", ""),
    },
    # 13 old domain redirects to a new brand domain
    "redirect-site.com": {"*redirect*": "newbrand.com"},
    "newbrand.com": {
        "/": page("<h1>New Brand Garage Doors</h1>", NAV),
        "/contact-us": page("<a href='mailto:info@newbrand.com'>info@newbrand.com</a>", NAV),
    },
    # 14 no email anywhere, only a form
    "noemail-carpet.com": {
        "/": page("<h1>No Email Carpet</h1>", NAV),
        "/contact-us": page("<form><input name='email' placeholder='you@example.com'></form>", NAV),
    },
    # 15 broken SSL certificate (only loads with verification off)
    "ssl-broken.com": {"/": page("<h1>SSL Broken Fencing</h1>", NAV, "sales@ssl-broken.com")},
    # 16 staff emails on a team page linked as "Meet the Team"
    "team-page.com": {
        "/": page("<h1>Team Page Painters</h1>", "<a href='/our-crew'>Meet the Team</a>"),
        "/our-crew": page("Mike Jones, General Manager - mike.jones@team-page.com", ""),
    },
    # 17 fully entity-encoded email
    "entity-encoded.com": {"/": page("<h1>Entity Pressure Washing</h1>", NAV,
                                     "&#105;&#110;&#102;&#111;&#64;entity-encoded&#46;com")},
    # 18 platform junk + real email on contact page
    "wix-site.com": {
        "/": page("<h1>Wix Site Detailing</h1>", NAV, head="<script>var x='support@wix.com';"
                  "var y='a1b2c3d4e5f6a7b8@sentry-next.wixpress.com';</script>"),
        "/contact-us": page("<a href='mailto:bookings@wix-site.com'>Book now</a>", NAV),
    },
    # 19 email in a data attribute
    "data-attr.com": {"/": page("<h1>Data Attr Roofing</h1><button data-email='crew@data-attr.com'>Email</button>",
                                NAV)},
    # 20 contact page linked as "Get in Touch" at an unusual URL
    "reach-hvac.com": {
        "/": page("<h1>Reach HVAC</h1>", "<a href='/reach-out'>Get in Touch</a>"),
        "/reach-out": page("Email: dispatch@reach-hvac.com", ""),
    },
}

FACEBOOK = {
    "/poolprosdallas": ('<html><body><script>{"intro":"Pool cleaning","email":"poolprosdallas\\u0040gmail.com"}'
                        "</script>Log in to Facebook</body></html>"),
}

EXPECTED: dict[str, set[str]] = {
    "joesplumbing.com": {"info@joesplumbing.com"},
    "acme-roofing.com": {"estimates@acme-roofing.com"},
    "greenlawn.com": {"hello@greenlawn.com"},
    "bestdumpsters.com": {"rentals@bestdumpsters.com"},
    "cityhvac.com": {"service@cityhvac.com"},
    "oldsite-pest.com": {"office@oldsite-pest.com"},
    "tree-masters.com": {"treemasters.tx@gmail.com"},
    "poolpros.com": {"poolprosdallas@gmail.com"},
    "smithelectric.com": {"john@smithelectric.com"},
    "sentry-site.com": {"hello@sentry-site.com"},
    "js-email.com": {"info@js-email.com"},
    "privacy-only.com": {"privacy@privacy-only.com"},
    "redirect-site.com": {"info@newbrand.com"},
    "noemail-carpet.com": set(),
    "ssl-broken.com": {"sales@ssl-broken.com"},
    "team-page.com": {"mike.jones@team-page.com"},
    "entity-encoded.com": {"info@entity-encoded.com"},
    "wix-site.com": {"bookings@wix-site.com"},
    "data-attr.com": {"crew@data-attr.com"},
    "reach-hvac.com": {"dispatch@reach-hvac.com"},
}
BAD = {"design@webguys.net", "you@example.com", "support@wix.com", "logo@2x.png"}

SSL_BROKEN = {"ssl-broken.com"}


def route(url: str, insecure: bool = False) -> tuple[int, str, str]:
    """``(status, html, final_url)``; raises ``SSLError`` for broken-certificate sites."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    bare = host[4:] if host.startswith("www.") else host
    path = parts.path.rstrip("/") or "/"
    if bare in SSL_BROKEN and parts.scheme == "https" and not insecure:
        raise SSLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    if bare in ("facebook.com", "m.facebook.com"):
        html = FACEBOOK.get(path)
        return (200, html, url) if html else (404, "nope", url)
    site = SITES.get(bare)
    if site is None:
        raise ConnectionError(f"unknown host {host}")
    if "*redirect*" in site:
        target = f"https://{site['*redirect*']}{path if path != '/' else '/'}"
        return route(target, insecure)
    if path in site:
        return 200, site[path], url
    return 404, page("Not found"), url


class SSLError(Exception):
    pass


def httpx_handler(insecure: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        try:
            status, html, final = route(str(request.url), insecure)
        except SSLError as exc:
            raise httpx.ConnectError(str(exc), request=request)
        except ConnectionError as exc:
            raise httpx.ConnectError(str(exc), request=request)
        if final != str(request.url):
            return httpx.Response(301, headers={"Location": final})
        return httpx.Response(status, text=html, headers={"content-type": "text/html; charset=utf-8"})
    return handler


class FakeRequests:
    """Just enough of the ``requests`` module for the v3 extractor."""

    class exceptions:  # noqa: N801
        SSLError = SSLError

    class _Resp:
        def __init__(self, status: int, text: str, url: str) -> None:
            self.status_code, self.text, self.url = status, text, url
            self.headers = {"content-type": "text/html"}

    def get(self, url, headers=None, timeout=None, verify=True, allow_redirects=True, **kw):
        status, html, final = route(url, insecure=not verify)
        return self._Resp(status, html, final)
