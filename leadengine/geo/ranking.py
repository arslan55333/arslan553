"""What wins the Google map pack in THIS market? A per-niche, per-area ranking formula from our own searches.

Every stored search (ZIP scans, market scans, rank-map points) is a list of businesses in Google's order. For each
listing we know its reviews, rating, website, name, category and how far it is from where the search was made.
The rank correlation (Spearman) of each signal with the position tells how much Google rewards it here:

* "reviews 0.62" → in this market, more reviews go with a much better position;
* "keyword in name 0.40" → businesses with "junk removal" in their name rank higher (also a spam tactic);
* "distance 0.55" → proximity decides a lot (you can't move — service-area pages and a closer pin can't fix it).

Then, for one business, the biggest levers: the strongest signals where it is behind the top 3.
It's a statistical pointer from a few dozen listings, not Google's real algorithm — the page says so.
"""

from __future__ import annotations

import math
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadengine.db.models import RankGrid, Search
from leadengine.db.repo import Repository
from leadengine.geo.zipdata import haversine_km
from leadengine.normalize import normalize_keyword

FEATURES = {
    "reviews": ("Number of Google reviews", True),
    "rating": ("Star rating", True),
    "kw_in_name": ("Service keyword in the business name", True),
    "category_match": ("Primary category matches the service", True),
    "website": ("Has a real website", True),
    "site_score": ("Website quality score", True),
    "distance": ("Distance from the searcher", False),
    "photos": ("Photos on the listing", True),
}
MIN_OBS = 25


def _kw_words(keyword: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", keyword.lower()) if len(w) > 3 and w not in ("service", "services")]


def features(b, keyword: str, *, lat: float | None, lng: float | None) -> dict[str, float | None]:
    words = _kw_words(keyword)
    name = (b.name or "").lower()
    cat = ((b.categories or [""])[0] or "").lower()
    has_site = bool(b.website) and not set(b.website_flags or []) & {"facebook_only", "social_or_directory_only",
                                                                    "broken", "parked"}
    dist = haversine_km(lat, lng, b.lat, b.lng) if None not in (lat, lng, b.lat, b.lng) else None
    return {"reviews": math.log1p(b.review_count or 0), "rating": b.rating,
            "kw_in_name": float(any(w in name for w in words)), "category_match": float(any(w in cat for w in words)),
            "website": float(has_site), "site_score": (b.website_score if b.website_score is not None else
                                                       (None if has_site else 0.0)),
            "distance": dist, "photos": math.log1p(b.photo_count) if b.photo_count is not None else None}


def observations(session: Session, keyword: str, zips: list[str] | None = None) -> list[dict[str, Any]]:
    """[{rank, business_id, features}] from every search / rank-map point for this keyword."""
    repo = Repository(session)
    obs: list[dict[str, Any]] = []
    stmt = select(Search).where(Search.keyword_norm == normalize_keyword(keyword), Search.result_count > 0)
    if zips:
        stmt = stmt.where(Search.zip_code.in_(list(zips)))
    for srch in session.scalars(stmt):
        for b, rank in repo.search_results(srch.id, limit=20):
            if (b.spam_score or 0) >= 55:
                continue                                   # fake listings would teach the wrong lesson
            obs.append({"rank": rank, "business_id": b.id, "f": features(b, keyword, lat=srch.lat, lng=srch.lng)})
    for grid in session.scalars(select(RankGrid).where(RankGrid.keyword == keyword)):
        cache: dict[int, Any] = {}
        for pt in grid.points or []:
            for pos, bid in enumerate(pt.get("ranks") or [], 1):
                b = cache.get(bid) or repo.get_business(bid)
                cache[bid] = b
                if b is None or (b.spam_score or 0) >= 55:
                    continue
                obs.append({"rank": pos, "business_id": bid, "f": features(b, keyword, lat=pt["lat"], lng=pt["lng"])})
    return obs


def _ranks(vals: list[float]) -> list[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 5 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (sx * sy) if sx and sy else None


def formula(obs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Signal → how strongly it goes with a better position here (0..1) and in which direction."""
    if len(obs) < MIN_OBS:
        return None
    signals = []
    for key, (label, more_is_better) in FEATURES.items():
        pairs = [(o["f"][key], o["rank"]) for o in obs if o["f"].get(key) is not None]
        if len(pairs) < MIN_OBS:
            continue
        rho = spearman([p[0] for p in pairs], [float(p[1]) for p in pairs])
        if rho is None:
            continue
        helps = -rho                      # a negative correlation with the position number = helps ranking
        signals.append({"key": key, "label": label, "strength": round(abs(helps), 2),
                        "direction": "more is better" if helps > 0 else "less is better", "n": len(pairs),
                        "expected": more_is_better == (helps > 0)})
    signals.sort(key=lambda s: -s["strength"])
    strong = [s for s in signals if s["strength"] >= 0.1]
    total = sum(s["strength"] for s in strong) or 1
    for s in signals:
        s["share"] = round(100 * s["strength"] / total) if s in strong else 0
    return {"observations": len(obs), "listings": len({o["business_id"] for o in obs}), "signals": signals}


def levers(b, keyword: str, obs: list[dict[str, Any]], form: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The strongest signals where this business is behind the businesses that rank in the top 3."""
    if not form:
        return []
    top = [o["f"] for o in obs if o["rank"] <= 3]
    if not top:
        return []
    mine = features(b, keyword, lat=None, lng=None)
    out = []
    for s in form["signals"]:
        k = s["key"]
        if s["strength"] < 0.15 or k == "distance" or not s["expected"]:
            continue
        vals = [f[k] for f in top if f.get(k) is not None]
        if not vals or mine.get(k) is None:
            continue
        avg = sum(vals) / len(vals)
        if mine[k] >= avg * 0.9:
            continue
        if k == "reviews":
            need = round(math.expm1(avg) - (b.review_count or 0))
            text = f"about {need} more Google reviews (top 3 here have ~{round(math.expm1(avg))})"
        elif k == "kw_in_name":
            text = f"{round(100 * avg)}% of the top 3 have the service in their business name (only if it's the real name)"
        elif k == "category_match":
            text = "set the primary Google category to the exact service"
        elif k == "website":
            text = "a real website of its own"
        elif k == "site_score":
            text = f"a better website (top 3 average score {round(avg)})"
        elif k == "rating":
            text = f"lift the rating to ~{avg:.1f}★"
        elif k == "photos":
            text = f"more photos (top 3 have ~{round(math.expm1(avg))})"
        else:
            continue
        out.append({"signal": s["label"], "strength": s["strength"], "share": s["share"], "action": text})
    return out[:4]
