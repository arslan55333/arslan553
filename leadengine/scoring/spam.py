"""Fake / spam Google listing hunter + duplicate listings.

Lead-gen spam networks fill local map packs with made-up businesses ("Cascade Cool Dumpster Rental" →
``premiersummit.site``). They push real businesses down — and Google removes them when they're reported through
its Business Redressal Complaint Form. That is a service you can sell, and a strong pitch:
"2 of the 3 listings above you look fake; reporting them could move you into the top 3".

Signals (all from data we already have, no network):
* website on a throw-away domain (.site, .online, .xyz …) or a builder subdomain, or a domain whose name has nothing
  to do with the business name;
* website domain registered in the last months, unregistered, or the site is down (Domain Rescue data);
* the name is keyword-stuffed ("Dumpster Rental Queens Pros") — also against Google's rules on its own;
* almost no reviews, or a burst of reviews in a few days;
* the same phone number or website shared by several listings;
* a single, generic category.

The result is a 0–100 suspicion score with reasons. It is a pointer for a human check, not a verdict.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Any

from leadengine.insights import niche_for
from leadengine.normalize import is_shared_domain

CHEAP_TLDS = {"site", "online", "xyz", "top", "store", "space", "website", "icu", "click", "fun", "live", "life",
              "shop", "pro", "info", "biz", "cloud", "us", "vip", "work", "today", "world", "cfd", "sbs", "lol", "buzz"}
BUILDER = ("ueniweb.com", "business.site", "wixsite.com", "godaddysites.com", "square.site", "weebly.com", "site123.me")
GENERIC_WORDS = {"rental", "rentals", "removal", "service", "services", "company", "co", "llc", "inc", "pros", "pro",
                 "experts", "solutions", "group", "the", "and", "of", "nyc", "best", "top", "local", "near", "me",
                 "dumpster", "junk", "plumbing", "plumber", "roofing", "hvac", "cleaning", "moving", "movers", "towing"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def name_matches_domain(name: str, domain: str | None) -> bool | None:
    if not domain:
        return None
    label = domain.lower().split(".")[0].replace("-", "")
    words = [w for w in _words(name) if len(w) > 2]
    distinctive = [w for w in words if w not in GENERIC_WORDS] or words
    return any(w in label for w in distinctive) or (len(words) >= 2 and "".join(w[0] for w in words) in label)


def stuffed_name(name: str, keyword_words: set[str], places: set[str]) -> bool:
    """Name made of the service keyword + a place, e.g. 'Dumpster Rental Queens Pros', 'Plumber Dallas TX'."""
    ws = set(_words(name))
    has_service = bool(ws & keyword_words)
    has_place = any(p in " ".join(_words(name)) for p in places)
    return has_service and has_place


def review_burst(dates: list[str] | None, total: int | None) -> bool:
    ds = sorted(date.fromisoformat(d[:10]) for d in dates or [] if d and d[:4].isdigit())
    if len(ds) < 5 or (total or 0) > 40:
        return False
    return (ds[-1] - ds[0]).days <= 14


def template_key(b) -> tuple[str, str] | None:
    """Look-alike network key: same domain extension + same last two name words, almost no reviews."""
    if (b.review_count or 0) > 3 or not b.domain or is_shared_domain(b.domain):
        return None                      # no own website: the name pattern alone is too common to mean anything
    words = _words(b.name)
    tld = b.domain.rsplit(".", 1)[-1]
    return (tld, " ".join(words[-2:])) if len(words) >= 3 else None


def network_sizes(businesses: list) -> Counter:
    return Counter(k for k in (template_key(b) for b in businesses) if k)


def spam_signals(b, *, phone_count: int = 1, domain_count: int = 1, domain_info: dict | None = None,
                 places: set[str] | None = None, today: date | None = None, network: int = 1) -> dict[str, Any]:
    today = today or date.today()
    reasons: list[tuple[int, str]] = []
    dom = (b.domain or "").lower().removeprefix("www.")
    niche = niche_for(*(b.categories or []), b.name)
    kw_words = {w for m in niche["match"] for w in _words(m)} | {"rental", "removal", "plumber", "roofing", "service"}
    tld = dom.rsplit(".", 1)[-1] if dom else ""
    if dom and not is_shared_domain(dom):
        if tld in CHEAP_TLDS:
            reasons.append((20, f"website on a throw-away .{tld} domain"))
        if any(dom.endswith(h) for h in BUILDER):
            reasons.append((8, "website is a free builder subdomain"))
        if name_matches_domain(b.name, dom) is False:
            reasons.append((22, f"website {dom} has nothing to do with the business name"))
    d = domain_info or {}
    if d.get("status") in ("available", "site_down", "for_sale", "pending_delete"):
        reasons.append((18, f"its website is dead ({d.get('label', d['status']).lower()})"))
    reg = d.get("registered")
    if reg and (today - date.fromisoformat(reg[:10])).days <= 180:
        reasons.append((12, f"website domain registered only {(today - date.fromisoformat(reg[:10])).days} days ago"))
    if stuffed_name(b.name, kw_words, places or set()):
        reasons.append((15, "business name is stuffed with the service + a place (against Google's rules)"))
    rc = b.review_count or 0
    if rc == 0:
        reasons.append((10, "no Google reviews at all"))
    elif rc <= 5 and (b.rating or 0) >= 4.9:
        reasons.append((6, f"only {rc} perfect 5★ review(s)"))
    if review_burst(b.recent_review_dates, b.review_count):
        reasons.append((15, "a burst of reviews within two weeks"))
    if phone_count > 1:
        reasons.append((25, f"the same phone number is on {phone_count} different listings"))
    if domain_count > 1 and dom and not is_shared_domain(dom):
        reasons.append((15, f"the same website is on {domain_count} different listings"))
    if network >= 3:
        reasons.append((20, f"one of {network} look-alike listings (same name pattern, same kind of domain, no reviews)"))
    if len(b.categories or []) <= 1 and not b.address:
        reasons.append((5, "no address shown and a single category"))
    if not b.phone:
        reasons.append((8, "no phone number"))
    score = min(100, sum(w for w, _ in reasons))
    label = "likely_fake" if score >= 55 else "suspicious" if score >= 30 else "ok"
    return {"score": score, "label": label, "reasons": [r for _, r in sorted(reasons, key=lambda x: -x[0])]}


def duplicate_groups(businesses: list) -> dict[int, list[int]]:
    """business id -> other ids that look like the same business (same phone, or same name + same website)."""
    by_phone: dict[str, list[int]] = {}
    by_name_dom: dict[tuple[str, str], list[int]] = {}
    for b in businesses:
        if b.phone_norm:
            by_phone.setdefault(b.phone_norm, []).append(b.id)
        key = (" ".join(_words(b.name)), (b.domain or "").removeprefix("www."))
        if key[1]:
            by_name_dom.setdefault(key, []).append(b.id)
    out: dict[int, set[int]] = {}
    for group in list(by_phone.values()) + list(by_name_dom.values()):
        if len(group) > 1:
            for i in group:
                out.setdefault(i, set()).update(x for x in group if x != i)
    return {k: sorted(v) for k, v in out.items()}


def counts(businesses: list) -> tuple[Counter, Counter]:
    phones = Counter(b.phone_norm for b in businesses if b.phone_norm)
    domains = Counter((b.domain or "").removeprefix("www.") for b in businesses if b.domain)
    return phones, domains
