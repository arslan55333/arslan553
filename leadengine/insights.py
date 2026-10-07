"""Money insights for one business — the numbers that make an owner reply.

1. **Ad waste**: the business pays for Google Ads, so what on its side burns that money? (no conversion
   tracking, ads landing on the homepage, no tap-to-call, slow page, no form, no website …). Each leak has a
   typical loss; together they give an estimated share of the ad budget that is wasted.
2. **Money lost to competitors**: monthly searches for "service + town" × the share of clicks the top-3 map
   spots get vs. the business's own position × call rate × close rate × average job value.
3. **Gap to the top 3**: reviews, rating, review speed, photos, categories, website, service / town pages,
   owner replies — what the businesses above it have that it doesn't, with a concrete target.

Everything is computed from what the tool already stored (no network, no key). Search demand, job values and
cost-per-click are industry averages from ``data/niches.csv`` — editable, and labelled as estimates everywhere.
"""

from __future__ import annotations

import csv
import statistics
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadengine.db.models import Business, RankGrid, Search, SearchResult
from leadengine.db.repo import Repository
from leadengine.geo.zipdata import zip_directory
from leadengine.normalize import is_shared_domain

NICHES = Path(__file__).resolve().parent / "data" / "niches.csv"
# share of all clicks for a map-pack position (local-search CTR studies, rounded)
PACK_CTR = {1: 0.18, 2: 0.15, 3: 0.12}
CALL_RATE = 0.30          # clicks that turn into a call / form
CLOSE_RATE = 0.40         # calls that turn into a paid job
MAX_POP = 400_000         # a map pack serves a neighbourhood, not a whole metro


@lru_cache(maxsize=1)
def niches() -> list[dict[str, Any]]:
    with open(NICHES, encoding="utf-8") as f:
        return [{"niche": r["niche"], "match": r["match"].split("|"), "job_value": float(r["job_value"]),
                 "cpc": float(r["cpc"]), "searches_per_1k": float(r["searches_per_1k"])} for r in csv.DictReader(f)]


def niche_for(*texts: str | None) -> dict[str, Any]:
    hay = " ".join(t.lower() for t in texts if t)
    for n in niches():
        if any(m in hay for m in n["match"]):
            return n
    return {"niche": "local service", "match": [], "job_value": 500.0, "cpc": 15.0, "searches_per_1k": 1.0}


def ctr_at(position: float | None) -> float:
    if position is None:
        return 0.005
    p = round(position)
    if p in PACK_CTR:
        return PACK_CTR[p]
    return 0.02 if p <= 10 else 0.005


# ── 1. ad waste ──────────────────────────────────────────────────────
def ad_waste(b: Business, *, site: dict | None, landing: dict | None, budget: float) -> dict[str, Any] | None:
    if b.ads_status not in ("Active", "Likely") and not b.lsa:
        return None
    flags = set(b.website_flags or [])
    no_site = not b.website or is_shared_domain(b.domain or "") or bool(
        flags & {"no_website", "facebook_only", "social_or_directory_only", "broken", "parked"})
    leaks: list[tuple[float, str, str]] = []          # (share lost, problem, fix)
    lf = (landing or {}).get("facts") or {}
    if no_site:
        leaks.append((0.35, "the ads have no website of the business's own to land on",
                      "a fast one-page site built for the ad keywords"))
    else:
        if site is not None and not (site.get("conversion_tag") or site.get("google_ads_ids") or site.get("ads_in_gtm")):
            leaks.append((0.20, "no Google Ads conversion tracking on the website — Google can't tell which clicks "
                                "became calls, so it can't spend the budget on the clicks that work",
                          "conversion tracking for calls and forms"))
        if site is not None and not site.get("call_tracking"):
            leaks.append((0.03, "no call tracking — no way to know which calls came from the ads",
                          "a tracking number on the landing page"))
        if lf.get("is_homepage"):
            leaks.append((0.10, "ads send people to the homepage instead of a page for that search",
                          "a landing page per service"))
        if lf and not lf.get("tel_links"):
            leaks.append((0.12, "no tap-to-call button for people coming from the ad on a phone",
                          "a sticky call button"))
        if lf and not (lf.get("quote_form") or lf.get("forms")):
            leaks.append((0.08, "no quote form on the page the ads go to", "a short quote form above the fold"))
        if lf.get("message_match") is not None and lf["message_match"] < 0.34:
            leaks.append((0.07, "the page headline doesn't match the ad", "headline that repeats the ad's promise"))
        perf = ((landing or {}).get("pagespeed") or {}).get("performance")
        if perf is not None and perf < 50:
            leaks.append((0.15 if perf < 30 else 0.10, f"the ad's landing page scores {perf}/100 on Google's mobile "
                                                       "speed test — many paid visitors leave before it loads",
                          "a lightweight page that loads in under 2 seconds"))
        if lf and not lf.get("viewport"):
            leaks.append((0.15, "the landing page isn't built for phones", "a mobile-first page"))
        if lf and not lf.get("https"):
            leaks.append((0.05, "the landing page shows \"Not secure\"", "HTTPS"))
    remaining = 1.0
    for share, *_ in leaks:
        remaining *= 1 - share
    wasted = min(0.70, 1 - remaining)
    return {"budget": budget, "wasted_share": round(wasted, 2), "wasted_monthly": round(budget * wasted, -1),
            "leaks": [{"share": s, "problem": p, "fix": f} for s, p, f in sorted(leaks, key=lambda x: -x[0])]}


# ── 2. money lost to competitors ─────────────────────────────────────
def best_position(session: Session, b: Business, keyword: str | None, rank: dict | None) -> float | None:
    if rank and rank.get("avg_rank") is not None:
        return float(rank["avg_rank"])
    stmt = select(SearchResult.rank).join(Search, Search.id == SearchResult.search_id).where(
        SearchResult.business_id == b.id)
    if keyword:
        stmt = stmt.where(Search.keyword == keyword)
    ranks = [r for r in session.scalars(stmt) if r]
    return float(min(ranks)) if ranks else None


def money_lost(b: Business, *, keyword: str | None, position: float | None, niche: dict,
               job_value: float | None = None) -> dict[str, Any] | None:
    z = zip_directory().get(b.zip_code or "")
    if z is None or not z.population:
        return None
    # people who search for this service around the business: its ZIP plus the area Google shows it in (~5 km)
    pop = min(MAX_POP, sum(o.population or 0 for o, _ in zip_directory().nearby(z.lat, z.lng, max(3.0, z.radius_km * 1.5))))
    searches = round(pop / 1000 * niche["searches_per_1k"])
    value = job_value or niche["job_value"]

    def revenue(ctr: float) -> float:
        return searches * ctr * CALL_RATE * CLOSE_RATE * value

    now, top = revenue(ctr_at(position)), revenue(PACK_CTR[1])
    return {"keyword": keyword or niche["niche"], "monthly_searches": searches, "population": pop,
            "position": position, "job_value": value, "now_monthly": round(now, -1),
            "top_monthly": round(top, -1), "lost_monthly": round(max(0.0, top - now), -1),
            "calls_now": round(searches * ctr_at(position) * CALL_RATE, 1),
            "calls_top": round(searches * PACK_CTR[1] * CALL_RATE, 1)}


# ── 3. gap to the top 3 ──────────────────────────────────────────────
def _per_month(dates: list[str] | None) -> float | None:
    from datetime import date

    ds = sorted((date.fromisoformat(d[:10]) for d in dates or [] if d and d[:4].isdigit()), reverse=True)
    if len(ds) < 3:
        return None
    return round(len(ds) / max(1.0, (ds[0] - ds[-1]).days / 30), 1)


def competitor_gap(session: Session, b: Business, keyword: str | None, rank: dict | None) -> dict[str, Any] | None:
    repo = Repository(session)
    grid = session.get(RankGrid, rank["grid_id"]) if rank and rank.get("grid_id") else None
    if grid is not None and grid.summary:            # rank map: the 3 businesses with the most top-3 spots
        ids = [x["business_id"] for x in grid.summary if x.get("business_id") != b.id][:3]
        top = [o for o in (session.get(Business, i) for i in ids) if o]
    else:
        searches = select(SearchResult.search_id).join(Search, Search.id == SearchResult.search_id).where(
            SearchResult.business_id == b.id)
        if keyword:
            searches = searches.where(Search.keyword == keyword)
        best: dict[int, int] = {}
        for sid in session.scalars(searches):
            for o, r in repo.search_results(sid, limit=6):
                if o.id != b.id:
                    best[o.id] = min(best.get(o.id, 99), r)
        top = [session.get(Business, i) for i, _ in sorted(best.items(), key=lambda kv: kv[1])[:3]]
    top = [o for o in top if o]
    if not top:
        return None

    def seo_pages(x: Business, key: str) -> int | None:
        e = repo.latest_enrichment(x.id, "seo", fresh_only=False)
        site = ((e.payload or {}).get("facts") or {}).get("site") if e else None
        return site.get(key) if site else None

    def rev_speed(x: Business) -> float | None:
        e = repo.latest_enrichment(x.id, "reviews", fresh_only=False)
        if e and (e.payload or {}).get("per_month") is not None:
            return e.payload["per_month"]
        return _per_month(x.recent_review_dates)

    metrics = [  # (label, getter, higher is better, unit, action template)
        ("Google reviews", lambda x: x.review_count, "", "get about {n} more reviews"),
        ("Star rating", lambda x: x.rating or None, "★", "lift the rating to {v}★ by asking happy customers"),
        ("New reviews / month", rev_speed, "", "ask every customer — about {n} more reviews a month"),
        ("Photos on Google", lambda x: x.photo_count, "", "add about {n} photos"),
        ("Google categories", lambda x: len(x.categories or []) or None, "", "add {n} more categories"),
        ("Website score", lambda x: x.website_score if x.website else 0, "/100", "a modern, fast website"),
        ("Service pages", lambda x: seo_pages(x, "service_pages"), "", "add about {n} service pages"),
        ("Town pages", lambda x: seo_pages(x, "area_pages"), "", "add about {n} town pages"),
        ("Owner replies to reviews", lambda x: round(100 * x.owner_response_rate) if x.owner_response_rate is not None
         else None, "%", "reply to every review"),
    ]
    rows, actions = [], []
    for label, get, unit, action in metrics:
        mine = get(b)
        theirs = [v for v in (get(o) for o in top) if v is not None]
        if not theirs:
            continue
        avg = statistics.mean(theirs)
        behind = mine is None or mine < avg * 0.9
        rows.append({"label": label, "you": mine, "top3": round(avg, 1 if label == "Star rating" else 0),
                     "unit": unit, "behind": behind})
        if behind:
            n = max(1, round(avg - (mine or 0)))
            actions.append(action.format(n=n, v=round(avg, 1)))
    mine = {c.lower() for c in b.categories or []}
    counts: dict[str, int] = {}
    for o in top:
        for c in {c for c in o.categories or []}:
            counts[c] = counts.get(c, 0) + 1
    need = 2 if len(top) >= 2 else 1
    category_gap = [c for c, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n >= need and c.lower() not in mine][:5]
    for c in category_gap[:2]:
        actions.insert(0, f"add the Google category \"{c}\" (the top businesses use it)")
    return {"category_gap": category_gap,
            "competitors": [{"id": o.id, "name": o.name, "rating": o.rating, "reviews": o.review_count,
                             "website": o.website} for o in top],
            "rows": rows, "actions": actions[:6], "behind": sum(r["behind"] for r in rows), "measured": len(rows)}


def insights(session: Session, b: Business, settings) -> dict[str, Any]:
    repo = Repository(session)
    cfg = settings.section("money")
    keyword = next(iter(repo.keywords_for(b.id)), None)
    niche = niche_for(keyword, *(b.categories or []))
    pick = {k: (e.payload if (e := repo.latest_enrichment(b.id, k, fresh_only=False)) else None)
            for k in ("ads_site", "landing", "rank", "website")}
    if pick["landing"] is None and pick["website"] and pick["website"].get("html"):
        h, web = pick["website"]["html"], pick["website"]       # no ad landing audit: the website is where ads go
        pick["landing"] = {"facts": {"tel_links": h.get("tel_links"), "forms": h.get("forms"),
                                     "quote_form": h.get("quote_form"), "viewport": h.get("viewport"),
                                     "https": str(web.get("final_url") or "").startswith("https://")},
                           "pagespeed": web.get("pagespeed")}
    budget = float(cfg.get("ad_budget") or 1500)
    position = best_position(session, b, keyword, pick["rank"])
    return {
        "niche": niche["niche"], "keyword": keyword,
        "ad_waste": ad_waste(b, site=pick["ads_site"], landing=pick["landing"], budget=budget),
        "money": money_lost(b, keyword=keyword, position=position, niche=niche,
                            job_value=float(cfg["job_value"]) if cfg.get("job_value") else None),
        "gap": competitor_gap(session, b, keyword, pick["rank"]),
        "assumptions": {"budget": budget, "cpc": niche["cpc"], "call_rate": CALL_RATE, "close_rate": CLOSE_RATE},
    }


def money_line(ins: dict[str, Any]) -> str | None:
    """One outreach-ready sentence with a number the owner cares about."""
    w, m = ins.get("ad_waste"), ins.get("money")
    if w and w["wasted_share"] >= 0.15 and w["leaks"]:
        return (f"from what I can see, roughly {round(w['wasted_share'] * 100)}% of a typical Google Ads budget would be "
                f"wasted on your setup — mainly because {w['leaks'][0]['problem'].split(' — ')[0]}")
    if m and m["lost_monthly"] >= 1000 and m["calls_top"] >= 2 * max(m["calls_now"], 0.5):
        spot = (f"you came up at #{round(m['position'])} on Google Maps" if m["position"]
                else "you didn't show up in the top 20 on Google Maps")
        now = "less than one call" if m["calls_now"] < 1 else f"about {round(m['calls_now'])} calls"
        return (f"when I searched \"{m['keyword']}\" near you, {spot}. Around {m['monthly_searches']} people a month "
                f"run that search; that spot brings in {now} a month, while #1 gets about {round(m['calls_top'])}")
    return None


def rank_history(session: Session, business_id: int, keyword: str | None = None) -> list[dict[str, Any]]:
    """Every rank-map run for this business (oldest first) with the change since the previous run."""
    from leadengine.db.models import Enrichment

    stmt = select(Enrichment).where(Enrichment.business_id == business_id, Enrichment.kind == "rank") \
        .order_by(Enrichment.fetched_at)
    rows, prev = [], {}
    for e in session.scalars(stmt):
        p = e.payload or {}
        kw = p.get("keyword")
        if keyword and kw != keyword:
            continue
        before = prev.get(kw)
        rows.append({"date": e.fetched_at.date().isoformat(), "keyword": kw, "solv": p.get("solv"),
                     "avg_rank": p.get("avg_rank"), "top3": p.get("top3"), "points": p.get("points"),
                     "solv_change": None if before is None or p.get("solv") is None else p["solv"] - before["solv"],
                     "rank_change": None if before is None or p.get("avg_rank") is None
                     else round(before["avg_rank"] - p["avg_rank"], 1)})
        prev[kw] = p
    return rows
