"""Google Business Profile: claimed or not, and how complete / active it is (a GMB-Everywhere-style audit).

Sources: the free browser (place page: claim link, photos, hours) and — as a fallback / for the details the
browser can't see from every connection — SerpAPI's place result (1 credit): ``unclaimed_listing``, categories,
attributes ("extensions"), description, posts, hours, the exact star histogram.

Unclaimed + good reviews is a lead signal: the owner isn't looking after the listing, anyone can suggest edits to
its phone number or hours. The offer is to help the OWNER claim it (and add you as a manager) — never to claim
someone else's business yourself, which breaks Google's rules.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

_REL = re.compile(r"(a|an|one|\d+)\s+(day|week|month|year)s?\s+ago", re.I)
_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def _rel_days(text: str | None) -> int | None:
    m = _REL.search(text or "")
    if not m:
        return None
    n = 1 if m.group(1).lower() in ("a", "an", "one") else int(m.group(1))
    return n * _DAYS[m.group(2).lower()]


def parse_serp_place(pr: dict[str, Any]) -> dict[str, Any]:
    attrs: list[str] = []
    for ext in pr.get("extensions") or []:
        if isinstance(ext, dict):
            for vals in ext.values():
                attrs += [str(v) for v in (vals if isinstance(vals, list) else [vals])]
    posts = pr.get("posts") or []
    post_days = [d for d in (_rel_days(p.get("date") or p.get("posted") or p.get("time"))
                             for p in posts if isinstance(p, dict)) if d is not None]
    hist = {int(x["stars"]): int(x["amount"]) for x in pr.get("rating_summary") or [] if
            isinstance(x, dict) and "stars" in x and "amount" in x}
    types = pr.get("type") or []
    return {
        "claimed": False if pr.get("unclaimed_listing") else True,
        "categories": types if isinstance(types, list) else [types],
        "description": (pr.get("description") or {}).get("snippet") if isinstance(pr.get("description"), dict)
        else pr.get("description"),
        "attributes": attrs[:40], "posts": len(posts), "last_post_days": min(post_days) if post_days else None,
        "hours": bool(pr.get("hours")), "website": pr.get("website"), "phone": pr.get("phone"),
        "rating": pr.get("rating"), "reviews": pr.get("reviews"), "histogram": hist or None,
        "open_state": pr.get("open_state"), "source": "serpapi",
    }


def profile_audit(p: dict[str, Any], *, photo_count: int | None = None, competitor_categories: int | None = None
                  ) -> dict[str, Any]:
    """0–100 completeness + plain-English fixes."""
    known = lambda k: p.get(k) is not None          # noqa: E731  (the browser can't see every field)
    checks: list[tuple[int, bool, str, str]] = []
    if known("claimed"):
        checks.append((25, p["claimed"] is not False, "the Google listing is NOT claimed — anyone can suggest changes to it",
                       "listing is claimed"))
    if known("description"):
        checks.append((10, bool(p["description"]), "no business description on the Google listing", "has a description"))
    if len(p.get("categories") or []) >= 2:       # secondary categories aren't public: only a plus when we see them
        checks.append((10, True, "", "several categories"))
    checks += [
        (8, bool(p.get("hours")), "no opening hours on Google", "opening hours listed"),
        (8, bool(p.get("website")), "no website linked from the Google listing", "website linked"),
        (8, bool(p.get("phone")), "no phone number on the Google listing", "phone listed"),
    ]
    if known("posts"):
        checks.append((12, (p.get("last_post_days") is not None and p["last_post_days"] <= 30),
                       "no Google posts in the last 30 days" if p.get("posts") else "never posts updates on Google",
                       "posts updates regularly"))
    if known("attributes"):
        checks.append((6, len(p["attributes"]) >= 3, "few public attributes (add e.g. 'Onsite services', 'Online "
                       "estimates', 'Veteran-owned' in the profile)", "attributes set"))
    if photo_count is not None:
        checks.append((8, photo_count >= 20, f"only {photo_count} photos (aim for 20+)", f"{photo_count} photos"))
    if competitor_categories and len(p.get("categories") or []) < competitor_categories:
        checks.append((5, False, f"fewer categories than the top competitors ({competitor_categories})", ""))
    total = sum(w for w, *_ in checks)
    got = sum(w for w, ok, *_ in checks if ok)
    return {"score": round(100 * got / total), "issues": [i for _, ok, i, _ in sorted(checks, key=lambda c: -c[0]) if not ok],
            "positives": [g for _, ok, _, g in checks if ok and g]}


def rescue_score(b, *, domain: dict | None, profile: dict | None) -> tuple[int, list[str]]:
    """How much this business needs rescuing — and still has a business worth saving."""
    score, why = 0, []
    st = (domain or {}).get("status")
    if st in ("expired_grace", "redemption", "pending_delete"):
        score += 45; why.append("domain expired")
    elif st in ("available", "for_sale"):
        score += 35; why.append("domain lost")
    elif st == "site_down":
        score += 30; why.append("website down")
    elif st == "expiring_soon":
        score += 15; why.append("domain expiring")
    elif st == "no_own_domain":
        score += 10; why.append("no own domain")
    claimed = (profile or {}).get("claimed") if profile else b.claimed
    if claimed is False:
        score += 30; why.append("listing unclaimed")
    if not b.website:
        score += 10; why.append("no website")
    if (b.rating or 0) >= 4.3 and (b.review_count or 0) >= 20:
        score += 15; why.append(f"{b.rating}★ · {b.review_count} reviews")
    if b.last_review_at and b.last_review_at >= datetime.utcnow() - timedelta(days=90):
        score += 10; why.append("still getting reviews")
    if (b.spam_score or 0) >= 55:
        return 0, ["looks like a fake listing"]
    return min(100, score), why
