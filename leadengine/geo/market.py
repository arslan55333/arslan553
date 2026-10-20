"""Market Weakness Index: how easy is it to win the Google map pack for a keyword in a town?

For every ZIP we've searched, the businesses Google shows (top 10) are scored on:
* how strong the top 3 are (reviews, rating) — weak top 3 = easy;
* how many of the top 10 look fake (spam hunter) — fakes get removed when reported;
* how many have no / a weak website — little SEO competition;
* how many already run Google Ads — more money in the market (worth more) but more competition.

Weakness 0–100 (higher = easier to rank a client into the top 3), plus demand (people × niche searches) and wealth,
so the best markets — weak competition, real demand, money — come first.
"""

from __future__ import annotations

import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadengine.db.models import Search, SearchResult
from leadengine.db.repo import Repository
from leadengine.geo.explorer import wealth_label, wealth_score
from leadengine.geo.zipdata import zip_directory
from leadengine.normalize import normalize_keyword


def market_stats(rows: list[tuple[Any, int]]) -> dict[str, Any] | None:
    """rows = [(business, rank)] best first."""
    rows = sorted(rows, key=lambda t: t[1])
    top10 = [b for b, _ in rows[:10]]
    if len(top10) < 3:
        return None
    real = [b for b in top10 if (b.spam_score or 0) < 55]
    top3 = real[:3] if len(real) >= 3 else top10[:3]
    rev = [b.review_count or 0 for b in top3]
    ratings = [b.rating for b in top3 if b.rating]
    fakes = sum(1 for b in top10[:5] if (b.spam_score or 0) >= 55)
    no_site = sum(1 for b in top10 if not b.website or (b.website_score is not None and b.website_score < 50)
                  or set(b.website_flags or []) & {"no_website", "facebook_only", "social_or_directory_only", "broken"})
    ads = sum(1 for b in top10 if b.ads_status in ("Active", "Likely") or b.lsa)
    return {"top3_reviews": round(statistics.mean(rev)), "top3_min_reviews": min(rev),
            "top3_rating": round(statistics.mean(ratings), 2) if ratings else None,
            "fakes_top5": fakes, "no_site_top10": no_site, "advertisers_top10": ads, "results": len(rows),
            "leaders": [{"id": b.id, "name": b.name, "reviews": b.review_count, "fake": (b.spam_score or 0) >= 55}
                        for b in top10[:3]]}


def weakness(stats: dict[str, Any]) -> int:
    r = stats["top3_reviews"]
    reviews_part = 45 if r < 15 else 35 if r < 40 else 25 if r < 80 else 12 if r < 200 else 4
    rating = stats.get("top3_rating") or 5
    rating_part = 10 if rating < 4.2 else 5 if rating < 4.6 else 0
    fake_part = min(20, 10 * stats["fakes_top5"])
    site_part = round(20 * stats["no_site_top10"] / 10)
    ads_part = -5 if stats["advertisers_top10"] >= 5 else 0
    return max(0, min(100, reviews_part + rating_part + fake_part + site_part + ads_part))


def label(w: int) -> str:
    return "easy" if w >= 60 else "medium" if w >= 35 else "hard"


def demand(zip_code: str, searches_per_1k: float) -> int | None:
    z = zip_directory().get(zip_code)
    return round((z.population or 0) / 1000 * searches_per_1k) if z and z.population else None


def markets(session: Session, keyword: str, zips: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """ZIP -> market numbers, from the latest search for this keyword in each ZIP."""
    from leadengine.insights import niche_for

    repo = Repository(session)
    stmt = select(Search).where(Search.keyword_norm == normalize_keyword(keyword), Search.zip_code.is_not(None),
                                Search.result_count > 0).order_by(Search.ran_at.desc())
    if zips:
        stmt = stmt.where(Search.zip_code.in_(list(zips)))
    niche = niche_for(keyword)
    out: dict[str, dict[str, Any]] = {}
    for srch in session.scalars(stmt):
        if srch.zip_code in out:
            continue
        stats = market_stats(repo.search_results(srch.id, limit=30))
        if stats is None:
            continue
        w = weakness(stats)
        z = zip_directory().get(srch.zip_code)
        ws = wealth_score(z.median_household_income, z.median_home_value) if z else None
        dem = demand(srch.zip_code, niche["searches_per_1k"])
        value = round((dem or 0) * niche["job_value"] * 0.18 * 0.3 * 0.4)       # #1 spot, monthly, rough
        out[srch.zip_code] = {**stats, "weakness": w, "label": label(w), "zip": srch.zip_code,
                              "town": z.city if z else None, "state": z.state if z else None,
                              "demand": dem, "top_spot_value": value, "wealth": ws, "wealth_label": wealth_label(ws),
                              "measured": srch.ran_at, "keyword": srch.keyword}
    return out


def ranked(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best markets first: weak competition × money in it."""
    return sorted(rows, key=lambda m: -(m["weakness"] * (1 + (m["top_spot_value"] or 0) / 5000)
                                       * (1 + (m["wealth"] or 50) / 200)))
