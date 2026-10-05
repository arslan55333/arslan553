"""Opportunity Score 0–100: how good a prospect this business is for a website redesign pitch.

Ideal target = strong reviews + active business + spends on Google Ads + weak/old website +
reachable. Weights are configurable in ``[opportunity]`` in config.toml.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from leadengine.db.models import Business

DEFAULT_WEIGHTS = {"reputation": 25, "activity": 15, "ads": 25, "website": 25, "reachability": 10}
DEFAULT_RULES = {
    "hot": 70, "warm": 50,
    "min_rating": 3.5,           # below this -> Skip (reputation problem, not a website problem)
    "modern_site": 80,           # website score at/above this -> Skip (nothing to sell)
    "reviews_full": 300,         # review count that earns full reputation points
    "require_ads_for_hot": True, # Hot only with proof of ad spend (Active / Likely / LSA)
}
CONTACTED = {"Emailed", "Replied", "Won", "Lost"}


@dataclass
class Opportunity:
    score: int
    label: str                       # Hot | Warm | Cold | Skip
    reason: str                      # one line, human readable
    parts: dict[str, dict[str, Any]] = field(default_factory=dict)
    skip_reason: str | None = None


def _reputation(b: Business, rules: dict) -> tuple[float | None, str | None]:
    if b.rating is None and b.review_count is None:
        return None, None
    rating = b.rating or 0
    reviews = b.review_count or 0
    r = 0.0 if rating < 3.5 else min(1.0, (rating - 3.5) / 1.2)          # 4.7+ = full
    v = 0.0 if reviews < 5 else min(1.0, math.log10(reviews / 5) / math.log10(rules["reviews_full"] / 5))
    return 0.55 * r + 0.45 * v, f"{rating:.1f}★, {reviews} reviews"


def _activity(b: Business, today: date) -> tuple[float | None, str | None]:
    if not b.last_review_at and not b.recent_review_dates:
        return None, None
    last = b.last_review_at.date() if isinstance(b.last_review_at, datetime) else b.last_review_at
    days = (today - last).days if last else None
    dates = [date.fromisoformat(d) for d in (b.recent_review_dates or [])]
    in_90 = sum(1 for d in dates if (today - d).days <= 90)
    recency = 1.0 if days is not None and days <= 30 else 0.7 if days is not None and days <= 90 else \
        0.3 if days is not None and days <= 180 else 0.0
    velocity = min(1.0, in_90 / 6)
    reply = b.owner_response_rate or 0
    frac = 0.5 * recency + 0.35 * velocity + 0.15 * reply
    note = f"last review {days} days ago" if days is not None else None
    if in_90 >= 3:
        note = f"{in_90} reviews in 90 days"
    return frac, note


def _ads(b: Business) -> tuple[float | None, str | None]:
    if not b.ads_status:
        return None, None
    frac = {"Active": 1.0, "Likely": 0.7, "Past": 0.4, "None": 0.0}.get(b.ads_status, 0.0)
    if b.lsa:
        frac = 1.0
    text = {"Active": "running Google Ads", "Likely": "likely running Google Ads", "Past": "ran Google Ads before",
            "None": None}.get(b.ads_status)
    if b.lsa:
        text = "running Google Local Services Ads"
    return frac, text


def _website(b: Business) -> tuple[float | None, str | None, str | None]:
    flags = set(b.website_flags or [])
    if not b.website or "no_website" in flags:
        return 1.0, "no website", None
    if flags & {"facebook_only", "social_or_directory_only"}:
        return 1.0, "only a Facebook page", None
    if flags & {"broken", "parked", "server_default_page"}:
        return 1.0, "website is down/parked", None
    if b.website_score is None:
        return None, None, None
    return (100 - b.website_score) / 100, f"website score {b.website_score}", None


def _reachability(b: Business, emails_checked: bool = True) -> tuple[float, str | None]:
    frac = 0.0
    if b.best_email and b.email_status == "valid":
        frac, note = 1.0, "verified email"
    elif b.best_email and (b.email_confidence or 0) >= 60:
        frac, note = 0.8, None
    elif b.best_email:
        frac, note = 0.5, None
    else:
        note = "no email found" if emails_checked else "email not checked yet"
    if b.phone:
        frac = min(1.0, frac + 0.2)
    return frac, note


def score_business(b: Business, *, today: date, website_reasons: list[str] | None = None,
                   lead_status: str | None = None, weights: dict | None = None, rules: dict | None = None,
                   checked: set[str] | None = None) -> Opportunity:
    """``checked``: which deep checks ran for this lead ({"emails", "website", "ads"}); None = assume all."""
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    rules = {**DEFAULT_RULES, **(rules or {})}
    parts: dict[str, dict[str, Any]] = {}
    notes: list[str] = []

    rep, rep_note = _reputation(b, rules)
    act, act_note = _activity(b, today)
    ads, ads_note = _ads(b)
    web, web_note, _ = _website(b)
    reach, reach_note = _reachability(b, checked is None or "emails" in checked)
    for name, frac in (("reputation", rep), ("activity", act), ("ads", ads), ("website", web), ("reachability", reach)):
        if frac is not None:
            parts[name] = {"points": round(frac * weights[name], 1), "max": weights[name]}
    available = sum(p["max"] for p in parts.values())
    score = round(100 * sum(p["points"] for p in parts.values()) / available) if available else 0
    # Unknown ads status: cap confidence instead of assuming either way
    if ads is None:
        score = min(score, 85)

    for n in (rep_note, act_note, ads_note, web_note):
        if n:
            notes.append(n)
    if website_reasons and web is not None and web < 1.0:
        notes[-1] += " — " + ", ".join(r.split(" (")[0] for r in website_reasons[:2])
    if reach_note in ("no email found", "email not checked yet"):
        notes.append(reach_note)
    unchecked = [] if checked is None else [k for k in ("ads", "website") if k not in checked]
    if unchecked and ((ads is None and "ads" in unchecked) or (web is None and "website" in unchecked)):
        notes.append(" & ".join(unchecked) + " not checked yet")

    skip = None
    if b.business_status and "CLOSED" in str(b.business_status).upper():
        skip = "business closed"
    elif lead_status in CONTACTED:
        skip = f"already contacted ({lead_status})"
    elif b.rating is not None and b.rating < rules["min_rating"]:
        skip = f"low rating {b.rating:.1f}"
    elif b.website_score is not None and b.website_score >= rules["modern_site"] and not (
            set(b.website_flags or []) & {"broken", "parked"}):
        skip = f"website already modern ({b.website_score})"
    elif not b.phone and not b.best_email:
        skip = "no way to contact"

    has_ads = b.ads_status in ("Active", "Likely") or bool(b.lsa)
    if skip:
        label = "Skip"
    elif score >= rules["hot"] and (has_ads or not rules.get("require_ads_for_hot")):
        label = "Hot"
    elif score >= rules["warm"]:
        label = "Warm"
    else:
        label = "Cold"
    reason = ", ".join(notes) or "not enough data yet"
    if skip:
        reason = f"skip: {skip}" + (f" ({reason})" if notes else "")
    return Opportunity(score, label, reason[:300], parts, skip)
