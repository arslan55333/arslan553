"""Google reviews audit: what customers complain about, and whether the owner answers.

Input (from the free browser, or SerpAPI's ``google_maps_reviews`` engine as a fallback):
* ``lowest``  — the lowest-rated reviews (sorted "Lowest rating")
* ``newest``  — the newest reviews (sorted "Newest")
* ``histogram`` — star counts {5: 120, 4: 10, …} when Google shows them
* ``topics`` — Google's own "mentioned in N reviews" chips

Output: negative count and share, unanswered negative reviews (with a suggested reply for each), the owner's
reply rate, review speed (per month, days since the last review), complaint themes, a 0–100 reputation score,
and plain-English issues for the audit report and the outreach email.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from leadengine.log import get_logger

log = get_logger("reviews")

THEMES: dict[str, tuple[str, ...]] = {
    "late / no-show": ("late", "no show", "no-show", "never showed", "didn't show", "did not show", "waited",
                       "hours late", "never came", "didn't come", "stood up", "missed the appointment", "no pickup",
                       "no pick up", "never picked up", "didn't pick up", "not picked up", "missed pickup", "never arrived",
                       "did not arrive", "didn't arrive"),
    "price / overcharged": ("overcharg", "price", "expensive", "hidden fee", "extra fee", "charged me", "rip off",
                            "ripoff", "quote was", "more than quoted", "double charged", "refund"),
    "rude / unprofessional": ("rude", "unprofessional", "attitude", "disrespect", "yelled", "hung up", "nasty",
                              "arrogant", "condescending"),
    "no response / hard to reach": ("never called", "no call back", "didn't call", "never responded",
                                    "no response", "didn't answer", "unreachable", "ignored", "voicemail",
                                    "never got back", "did not respond"),
    "damage / mess": ("damage", "damaged", "scratched", "broke", "broken", "mess", "left trash", "dent", "destroyed"),
    "poor quality / unfinished": ("poor job", "bad job", "sloppy", "incomplete", "not finished", "half done",
                                  "terrible work", "redo", "re-do", "worst", "low quality", "didn't finish"),
    "scam / dishonest": ("scam", "fraud", "dishonest", "lied", "liar", "stole", "bait and switch", "fake"),
}

REPLIES: dict[str, str] = {
    "late / no-show": "Hi {name}, we're sorry we didn't arrive when we promised — your time matters. We've changed "
                      "how we schedule so this doesn't happen again. Please call us at {phone} and we'll make it right.",
    "price / overcharged": "Hi {name}, thanks for the feedback. We want our pricing to be clear before any work "
                           "starts. Please call us at {phone} so we can go over the invoice with you.",
    "rude / unprofessional": "Hi {name}, we're sorry about how you were treated — that isn't how we work. We've "
                             "spoken with the team. Please call us at {phone}; we'd like to fix this.",
    "no response / hard to reach": "Hi {name}, we're sorry we missed you. We've added a better way to reach us — "
                                   "call or text {phone} and you'll get a reply the same day.",
    "damage / mess": "Hi {name}, we're sorry about the damage. Please call us at {phone} so we can make it right.",
    "poor quality / unfinished": "Hi {name}, we're sorry the job didn't meet your expectations. Please call us at "
                                 "{phone} and we'll come back to finish it properly.",
    "scam / dishonest": "Hi {name}, we take this seriously and would like to understand what happened. Please "
                        "call us at {phone} so we can look into it personally.",
    "": "Hi {name}, thank you for the honest feedback, and we're sorry about your experience. Please call us at "
        "{phone} so we can make it right.",
}

_REL = re.compile(r"(a|an|one|\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", re.I)
_UNIT_DAYS = {"second": 0, "minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}


def review_date(r: dict[str, Any], now: datetime) -> datetime | None:
    iso = r.get("iso_date")
    if iso:
        try:
            return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    m = _REL.search(r.get("date") or "")
    if not m:
        return None
    n = 1 if m.group(1).lower() in ("a", "an", "one") else int(m.group(1))
    return now - timedelta(days=n * _UNIT_DAYS[m.group(2).lower()])


def themes_of(text: str | None) -> list[str]:
    low = (text or "").lower()
    return [t for t, words in THEMES.items() if any(w in low for w in words)]


def _key(r: dict[str, Any]) -> str:
    return (r.get("author") or "") + "|" + (r.get("text") or "")[:60]


def suggested_reply(review: dict[str, Any], phone: str | None) -> str:
    first = (review.get("author") or "there").split()[0]
    theme = (themes_of(review.get("text")) or [""])[0]
    return REPLIES[theme].format(name=first, phone=phone or "our office")


def analyze(*, newest: list[dict], lowest: list[dict], histogram: dict[int, int] | None, rating: float | None,
            total: int | None, topics: list[dict] | None = None, phone: str | None = None,
            now: datetime | None = None, source: str = "browser") -> dict[str, Any]:
    now = now or datetime.utcnow()
    hist = {int(k): int(v) for k, v in (histogram or {}).items() if str(k).isdigit()}
    seen: dict[str, dict] = {}
    for r in (lowest or []) + (newest or []):
        if r.get("rating") is not None:
            seen.setdefault(_key(r), r)
    sample = list(seen.values())
    negatives = [r for r in sample if (r.get("rating") or 5) <= 2]
    if hist:
        neg_count, neg_exact = hist.get(1, 0) + hist.get(2, 0), True
        total = total or sum(hist.values())
    else:
        neg_count, neg_exact = len(negatives), len(negatives) < len(lowest or [])
    neg_share = round(100 * neg_count / total, 1) if total else None
    unanswered = [r for r in negatives if not r.get("owner_response")]
    neg_reply_rate = round(100 * (len(negatives) - len(unanswered)) / len(negatives)) if negatives else None
    new = newest or []
    reply_rate = round(100 * sum(1 for r in new if r.get("owner_response")) / len(new)) if new else None
    dates = sorted((d for d in (review_date(r, now) for r in new) if d), reverse=True)
    last_days = (now - dates[0]).days if dates else None
    in90 = sum(1 for d in dates if (now - d).days <= 90)
    per_month = None
    if len(dates) >= 3:
        span = max(30, (dates[0] - dates[-1]).days)
        per_month = round(len(dates) / (span / 30), 1)
    theme_counts: dict[str, list[dict]] = {}
    for r in negatives + [r for r in sample if r.get("rating") == 3]:
        for t in themes_of(r.get("text")):
            theme_counts.setdefault(t, []).append(r)
    themes = sorted(({"theme": t, "count": len(rs), "example": (rs[0].get("text") or "")[:180]}
                     for t, rs in theme_counts.items()), key=lambda x: -x["count"])
    google_topics = [{"keyword": t.get("keyword"), "mentions": t.get("mentions")} for t in topics or []
                     if t.get("keyword")][:12]

    issues: list[tuple[float, str]] = []
    positives: list[str] = []
    score = 100.0
    if rating is not None and rating < 4.5:
        score -= (4.5 - rating) * 25
        issues.append((6, f"{rating:.1f}★ average — under 4.5 loses clicks to competitors with higher stars"))
    if neg_count:
        more = "" if neg_exact else "+"
        issues.append((7, f"{neg_count}{more} negative (1–2★) reviews"
                       + (f" — {neg_share}% of all reviews" if neg_share is not None and neg_exact else "")))
        score -= min(20, neg_count * (1 if neg_exact else 2))
    if unanswered:
        issues.append((9, f"{len(unanswered)} negative review(s) with no reply from the owner — every future "
                          "customer reads these"))
        score -= min(25, 6 * len(unanswered))
    elif negatives:
        positives.append("owner replies to negative reviews")
    if reply_rate is not None:
        if reply_rate < 30:
            issues.append((5, f"owner replied to only {reply_rate}% of the newest reviews (Google rewards replies)"))
            score -= 10
        else:
            positives.append(f"owner replies to {reply_rate}% of new reviews")
    if last_days is not None and last_days > 60:
        issues.append((6, f"no new review in {last_days} days — Google favours businesses with fresh reviews"))
        score -= 10
    elif in90 >= 3:
        positives.append(f"{in90} new reviews in the last 90 days")
    if themes:
        t = themes[0]
        issues.append((5, f"most common complaint: {t['theme']} ({t['count']} review{'s' if t['count'] > 1 else ''})"))
    if total is not None and total < 25:
        issues.append((4, f"only {total} reviews — top competitors usually have 100+"))
        score -= 8
    return {
        "source": source, "rating": rating, "total": total, "histogram": hist or None,
        "negative": neg_count, "negative_exact": neg_exact, "negative_share": neg_share,
        "unanswered_negative": [{"rating": r.get("rating"), "date": r.get("date") or r.get("iso_date"),
                                 "author": r.get("author"), "text": (r.get("text") or "")[:400],
                                 "themes": themes_of(r.get("text")), "reply": suggested_reply(r, phone)}
                                for r in unanswered[:8]],
        "negative_reply_rate": neg_reply_rate, "reply_rate": reply_rate, "last_review_days": last_days,
        "reviews_90d": in90, "per_month": per_month, "themes": themes, "google_topics": google_topics,
        "score": max(0, min(100, round(score))),
        "issues": [t for _, t in sorted(issues, key=lambda x: -x[0])], "positives": positives,
        "sampled": len(sample),
        "good_reviews": good_reviews(newest or []),
    }


def good_reviews(reviews: list[dict], limit: int = 4) -> list[dict[str, Any]]:
    """Short 4-5★ quotes for the preview page, author shortened to "First L."."""
    out = []
    for r in reviews:
        text = (r.get("text") or "").strip()
        if (r.get("rating") or 0) >= 4 and 40 <= len(text) <= 500:
            parts = (r.get("author") or "").split()
            name = f"{parts[0]} {parts[-1][0]}." if len(parts) >= 2 else (parts[0] if parts else "Google reviewer")
            out.append({"text": text, "rating": r.get("rating"), "author": name})
    return out[:limit]


# ── SerpAPI fallback (1 credit per call) ─────────────────────────────
def from_serpapi(item: dict[str, Any]) -> dict[str, Any]:
    resp = item.get("response") or {}
    return {"rating": item.get("rating"), "date": item.get("date"), "iso_date": item.get("iso_date"),
            "text": item.get("snippet") or (item.get("extracted_snippet") or {}).get("original"),
            "author": (item.get("user") or {}).get("name"), "owner_response": bool(resp.get("snippet") or resp)}


async def serpapi_reviews(http, api_key: str, *, data_id: str | None, place_id: str | None,
                          sort_by: str) -> tuple[list[dict], dict[str, Any]]:
    params = {"engine": "google_maps_reviews", "hl": "en", "sort_by": sort_by, "api_key": api_key}
    if data_id:
        params["data_id"] = data_id
    elif place_id:
        params["place_id"] = place_id
    else:
        return [], {}
    r = await http.request("GET", "https://serpapi.com/search.json", params=params, retries=1, timeout=60)
    data = r.json() if r.status_code == 200 else {}
    if data.get("error"):
        raise RuntimeError(data["error"])
    return [from_serpapi(x) for x in data.get("reviews") or []], data


async def ai_summary(llm, name: str, audit: dict[str, Any]) -> str | None:
    """Two plain sentences about what unhappy customers say (only when an AI key is set)."""
    negs = [r["text"] for r in audit.get("unanswered_negative") or [] if r.get("text")]
    if llm is None or not negs:
        return None
    prompt = (f"These are negative Google reviews of {name}, a local service business. In two short, factual "
              "sentences, say what customers complain about most and what the owner should fix. No fluff.\n\n"
              + "\n---\n".join(negs[:8]))
    try:
        return (await llm.complete(prompt, max_tokens=200)).strip()
    except Exception as exc:
        log.warning("AI review summary failed", extra={"data": {"error": str(exc)[:120]}})
        return None
