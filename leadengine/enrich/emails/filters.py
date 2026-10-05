"""Decide whether a candidate is a real, contactable email (ported from v3, fixed and extended)."""

from __future__ import annotations

import re

from leadengine.normalize import normalize_domain

ASSET_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".css", ".js", ".woff", ".woff2",
             ".ttf", ".eot", ".mp4", ".webm", ".pdf", ".zip", ".avif", ".bmp", ".map")

# Domains whose addresses are never the business's contact email. Matched with subdomains
# (v3 only matched exactly, so o123.ingest.sentry.io slipped through).
JUNK_DOMAINS = frozenset({
    "example.com", "example.org", "example.net", "domain.com", "yourdomain.com", "yoursite.com", "website.com",
    "email.com", "test.com", "sentry.io", "sentry-next.wixpress.com", "wixpress.com", "wix.com", "schema.org",
    "w3.org", "googleapis.com", "google.com", "gstatic.com", "sentry.wixpress.com", "mailchimp.com",
    "sendgrid.net", "amazonses.com", "sparkpostmail.com", "mandrillapp.com", "mailgun.org", "exacttarget.com",
    "klaviyo.com", "constantcontact.com", "hubspot.com", "hs-sites.com", "marketo.com", "pardot.com",
    "salesforce.com", "zendesk.com", "intercom.io", "intercom-mail.com", "wordpress.com", "wordpress.org",
    "shopify.com", "squarespace.com", "godaddy.com", "secureserver.net", "cloudflare.com", "jquery.com",
    "github.com", "apple.com", "facebook.com", "fb.com", "twitter.com", "instagram.com", "yelp.com",
    "bootstrapcdn.com", "fontawesome.com", "gravatar.com", "addtoany.com", "elementor.com", "duda.co",
})
JUNK_LOCAL_PREFIXES = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster", "bounce",
    "unsubscribe", "abuse", "spam", "auto-reply", "autoreply", "daemon", "robot", "notifications",
)
PLACEHOLDER_LOCALS = frozenset({
    "you", "your", "yourname", "your.name", "name", "first.last", "firstname", "firstname.lastname",
    "john.doe", "johndoe", "jane.doe", "janedoe", "user", "username", "email", "test", "example", "someone",
    "me", "xxx", "abc",
})
FREE_MAIL = frozenset({
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "msn.com", "icloud.com", "me.com",
    "aol.com", "comcast.net", "att.net", "sbcglobal.net", "verizon.net", "bellsouth.net", "cox.net",
    "charter.net", "protonmail.com", "proton.me", "ymail.com", "rocketmail.com", "mail.com", "gmx.com",
    "zoho.com", "earthlink.net", "frontier.com", "windstream.net", "centurylink.net", "optonline.net",
})
ROLE_LOCALS = (
    "info", "contact", "hello", "office", "admin", "sales", "support", "service", "services", "help",
    "booking", "bookings", "reservations", "quote", "quotes", "estimates", "estimate", "dispatch", "team",
    "mail", "inquiries", "inquiry", "enquiries", "billing", "accounts", "customerservice", "orders", "jobs",
    "careers", "hr", "marketing", "media", "press", "webmaster", "reception", "frontdesk",
)
_HEX_RE = re.compile(r"^[0-9a-f]{12,}$")
_LOCAL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._%+-]{0,62}[a-z0-9])?$")


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def local_of(email: str) -> str:
    return email.split("@", 1)[0].lower()


def is_hash_like(local: str) -> bool:
    """Tracking ids, tokens and hashes (Sentry DSNs, Wix ids ...)."""
    s = local.lower()
    if _HEX_RE.match(s):
        return True
    if len(s) > 12:
        vowels = sum(c in "aeiou" for c in s)
        if vowels == 0 or (len(s) > 16 and vowels / len(s) < 0.08):
            return True
    if len(s) > 24 and sum(c.isdigit() for c in s) > len(s) * 0.35:
        return True
    return False


def _domain_matches(domain: str, base: str) -> bool:
    return domain == base or domain.endswith("." + base)


def is_junk_domain(domain: str) -> bool:
    return any(_domain_matches(domain, j) for j in JUNK_DOMAINS)


def is_role(email: str) -> bool:
    local = re.sub(r"[^a-z]", "", local_of(email))
    return any(local == r or local.startswith(r) for r in ROLE_LOCALS)


def is_free_mail(email: str) -> bool:
    return domain_of(email) in FREE_MAIL


def same_site(email: str, site_domains: set[str]) -> bool:
    """Email domain equals (or is a subdomain of / parent of) one of the site's domains."""
    d = domain_of(email)
    return any(_domain_matches(d, s) or _domain_matches(s, d) for s in site_domains if s)


def reject_reason(email: str) -> str | None:
    """``None`` if the email is plausible, otherwise why it was rejected."""
    if email.count("@") != 1 or len(email) > 254:
        return "syntax"
    local, domain = email.split("@")
    if not _LOCAL_RE.match(local) or ".." in local:
        return "syntax"
    if not normalize_domain(domain) or ".." in domain or domain.startswith("-"):
        return "syntax"
    tld = domain.rsplit(".", 1)[-1]
    if not tld.isalpha() or not 2 <= len(tld) <= 24:
        return "syntax"
    if email.endswith(ASSET_EXT) or any(ext + "@" in email for ext in ASSET_EXT) or re.search(r"@\d+x\.", email):
        return "asset filename"
    if is_junk_domain(domain):
        return "platform/placeholder domain"
    if local in PLACEHOLDER_LOCALS:
        return "placeholder"
    if any(local == p or local.startswith(p) for p in JUNK_LOCAL_PREFIXES):
        return "no-reply/system"
    if is_hash_like(local):
        return "hash/token"
    return None
