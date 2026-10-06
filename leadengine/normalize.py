"""Normalisation helpers used for dedupe keys and cache lookups."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_NON_DIGIT = re.compile(r"\D+")
_US_ADDRESS_TAIL = re.compile(
    r",\s*([^,]+?),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?(?:,\s*(?:USA|US|United States))?\s*$"
)

# A business "website" on one of these is not its own domain, so it must not
# be used to dedupe (every Facebook-only business would share facebook.com).
SHARED_DOMAINS = frozenset({
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "youtube.com", "tiktok.com", "yelp.com", "google.com", "business.site", "g.page",
    "nextdoor.com", "angi.com", "homeadvisor.com", "thumbtack.com", "bbb.org",
    "yellowpages.com", "linktr.ee", "wixsite.com", "godaddysites.com", "square.site",
})


def normalize_phone(phone: object) -> str | None:
    """Digits only, US country code removed. ``None`` if too short to be a phone."""
    if not phone:
        return None
    digits = _NON_DIGIT.sub("", str(phone))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) >= 7 else None


def normalize_domain(url: object) -> str | None:
    """``https://www.Example.com/contact`` -> ``example.com``."""
    if not url:
        return None
    text = str(url).strip()
    if "://" not in text:
        text = "http://" + text
    try:
        host = (urlparse(text).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else None


def is_shared_domain(domain: str | None) -> bool:
    if not domain:
        return False
    return any(domain == d or domain.endswith("." + d) for d in SHARED_DOMAINS)


def dedupe_key(phone: object, website: object) -> str | None:
    """Fallback identity when there is no Google place_id: normalised phone + own domain."""
    p = normalize_phone(phone)
    d = normalize_domain(website)
    if is_shared_domain(d):
        d = None
    if not p and not d:
        return None
    return f"{p or ''}|{d or ''}"


def normalize_keyword(keyword: str) -> str:
    return " ".join(keyword.lower().split())


def normalize_zip(zip_code: str) -> str:
    """Return a 5-digit US ZIP or raise ``ValueError``."""
    digits = _NON_DIGIT.sub("", zip_code or "")[:5]
    if len(digits) != 5:
        raise ValueError(f"Not a valid US ZIP code: {zip_code!r}")
    return digits


def parse_us_address(address: str | None) -> tuple[str | None, str | None, str | None]:
    """Pull (city, state, zip) out of ``"12 Main St, Springfield, IL 62701, USA"``."""
    if not address:
        return None, None, None
    m = _US_ADDRESS_TAIL.search(address.strip())
    if not m:
        return None, None, None
    return m.group(1).strip(), m.group(2), m.group(3)


_AD_HOSTS = ("googleadservices.com", "doubleclick.net", "googlesyndication.com")


def unwrap_ad_url(url: str | None) -> tuple[str | None, bool]:
    """Google ad click links (google.com/aclk?..., googleadservices.com/...) -> (real site or None, was_an_ad).

    Sponsored Maps listings link their "Website" button through Google's ad click URL; the real
    landing page is in ``adurl=``. When it isn't there, the caller resolves the redirect later.
    """
    if not url:
        return url, False
    from urllib.parse import parse_qs, urlparse

    p = urlparse(url)
    host = (p.hostname or "").lower()
    is_ad = host.endswith(_AD_HOSTS) or (host.startswith(("www.google.", "google.")) and p.path.startswith(("/aclk", "/url")))
    if not is_ad:
        return url, False
    qs = parse_qs(p.query)
    for key in ("adurl", "url", "q"):
        for v in qs.get(key, []):
            if v.startswith("http"):
                inner, _ = unwrap_ad_url(v)
                return inner, True
    return None, True
