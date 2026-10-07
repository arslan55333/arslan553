"""Build a branded, printable audit report for one business (facts only, noindex).

Layout on disk::

    data/reports/<slug>/index.html      (+ robots.txt, _headers, img/*.jpg)
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from leadengine.config import Settings
from leadengine.db.models import Business, RankGrid, SearchResult
from leadengine.db.repo import Repository
from leadengine.geo.rankgrid import svg_heatmap
from leadengine.outreach.facts import plain_issue
from leadengine.preview.builder import HEADERS, ROBOTS, slugify

_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
                   autoescape=select_autoescape(["html"]))


def _grade(score: int | None) -> str:
    if score is None:
        return "na"
    return "good" if score >= 75 else "ok" if score >= 50 else "bad"


def competitors(session: Session, b: Business, rank_summary: list[dict] | None, limit: int = 5) -> list[dict]:
    """Top competitors: from the rank map if there is one, else businesses ranking above in the same searches."""
    repo = Repository(session)
    rows: list[dict] = []
    if rank_summary:
        for r in rank_summary:
            if r["business_id"] == b.id:
                continue
            o = repo.get_business(r["business_id"])
            if o is not None:
                rows.append({"name": o.name, "rating": o.rating, "reviews": o.review_count, "solv": r["solv"],
                             "site": o.website_score, "ads": o.ads_status})
            if len(rows) >= limit:
                break
        return rows
    seen = set()
    for sid, rank in session.execute(select(SearchResult.search_id, SearchResult.rank).where(SearchResult.business_id == b.id)):
        for o, orank in repo.search_results(sid, limit=limit + 2):
            if o.id != b.id and o.id not in seen and (rank is None or orank < rank):
                seen.add(o.id)
                rows.append({"name": o.name, "rating": o.rating, "reviews": o.review_count, "solv": None,
                             "site": o.website_score, "ads": o.ads_status})
    return rows[:limit]


def _insights(session: Session, b: Business, settings: Settings) -> dict[str, Any]:
    from leadengine.insights import insights
    try:
        return insights(session, b, settings)
    except Exception:
        return {}


def gather(session: Session, b: Business, settings: Settings) -> dict[str, Any]:
    repo = Repository(session)
    enr = {k: (e.payload if (e := repo.latest_enrichment(b.id, k, fresh_only=False)) else None)
           for k in ("website", "ads", "landing", "seo", "rank", "preview", "reviews", "citations", "site_audit")}
    web, landing, seo, rank = enr["website"] or {}, enr["landing"] or {}, enr["seo"] or {}, enr["rank"]
    reviews = enr["reviews"] or {}
    citations = enr["citations"] or {}
    site_audit = enr["site_audit"] or {}
    website_issues = [plain_issue(r)[1] for r in (web.get("reasons") or [])][:6]
    top: list[str] = []
    if landing.get("issues") and b.ads_status in ("Active", "Likely"):
        top.append("Paid clicks: " + landing["issues"][0])
    top += [i[0].upper() + i[1:] for i in website_issues[:2]]
    if rank and rank.get("points"):
        top.append(f"Shows in Google's top 3 map results at only {rank['top3']} of {rank['points']} spots in the area")
    bad_nap = [x for x in citations.get("listings") or [] if x.get("issues") and x.get("directory")]
    if bad_nap:
        top.append(f"{bad_nap[0]['site']} {bad_nap[0]['issues'][0]} — Google trusts businesses whose details match everywhere")
    if reviews.get("unanswered_negative"):
        top.append(f"{len(reviews['unanswered_negative'])} negative Google review(s) with no reply from you")
    top += (seo.get("issues") or [])[:2]
    grid = None
    heat = None
    if rank and rank.get("grid_id"):
        grid = session.get(RankGrid, rank["grid_id"])
        if grid is not None:
            heat = svg_heatmap(grid.points or [], b.id, width=420,
                               tiles=bool(settings.section("rank").get("map_tiles", True)))
    cards = [
        {"label": "Website", "value": b.website_score, "suffix": "/100", "grade": _grade(b.website_score),
         "note": web.get("grade") or ("no website" if not b.website else "")},
        {"label": "Ad landing page", "value": landing.get("score"), "suffix": "/100", "grade": _grade(landing.get("score")),
         "note": "where your ads send people" if landing else "no ads found"},
        {"label": "Local SEO", "value": seo.get("score"), "suffix": "/100", "grade": _grade(seo.get("score")),
         "note": "website + Google profile"},
        {"label": "Map visibility", "value": rank.get("solv") if rank else None, "suffix": "%",
         "grade": ("na" if not rank or rank.get("solv") is None else
                   "good" if rank["solv"] >= 50 else "ok" if rank["solv"] >= 20 else "bad"),
         "note": "share of top-3 spots" if rank else "not measured"},
    ]
    if citations:
        cards.append({"label": "Listings (NAP)", "value": citations.get("score"), "suffix": "/100",
                      "grade": _grade(citations.get("score")), "note": "directories + matching details"})
    if reviews:
        cards.append({"label": "Reviews health", "value": reviews.get("score"), "suffix": "/100",
                      "grade": _grade(reviews.get("score")), "note": "replies, negatives, review speed"})
    return {"b": b, "web": web, "landing": landing, "seo": seo, "rank": rank, "grid": grid, "heat": heat,
            "ads": enr["ads"] or {}, "preview": enr["preview"] or {}, "website_issues": website_issues,
            "reviews": reviews, "money": _insights(session, b, settings), "citations": citations, "site_audit": site_audit,
            "top": [t[0].upper() + t[1:] for t in dict.fromkeys(top) if t][:5], "cards": cards,
            "competitors": competitors(session, b, grid.summary if grid else None),
            "today": date.today().strftime("%B %d, %Y")}


class ReportBuilder:
    def __init__(self, settings: Settings, out_dir: Path | None = None) -> None:
        self.settings = settings
        self.out_dir = out_dir or settings.root / "data" / "reports"
        cfg = settings.section("preview")
        self.brand = {"name": cfg.get("brand_name") or "Your Agency", "url": cfg.get("brand_url", ""),
                      "email": cfg.get("brand_email", "")}

    def build(self, session: Session, b: Business) -> dict[str, Any]:
        data = gather(session, b, self.settings)
        slug = f"{slugify(b.name)}-{slugify(b.city or '', 20)}".strip("-") + f"-{b.id}"
        site = self.out_dir / slug
        img = site / "img"
        img.mkdir(parents=True, exist_ok=True)

        def copy(path: str | None, name: str) -> str | None:
            if path and Path(path).is_file():
                dest = img / name
                shutil.copyfile(path, dest)
                return f"img/{name}"
            return None
        shots = {
            "site": copy(data["web"].get("screenshot"), "website.jpg"),
            "site_mobile": copy(data["web"].get("mobile_screenshot"), "website-mobile.jpg"),
            "landing": copy(data["landing"].get("screenshot"), "landing.jpg"),
            "landing_mobile": copy(data["landing"].get("mobile_screenshot"), "landing-mobile.jpg"),
            "preview": copy(data["preview"].get("screenshot"), "concept.jpg"),
        }
        html = _env.get_template("report.html").render(**data, shots=shots, brand=self.brand)
        (site / "index.html").write_text(html, encoding="utf-8", newline="\n")
        (site / "robots.txt").write_text(ROBOTS, encoding="utf-8", newline="\n")
        (site / "_headers").write_text(HEADERS, encoding="utf-8", newline="\n")
        return {"slug": slug, "path": str(site / "index.html"), "dir": str(site), "url": None, "top": data["top"]}
