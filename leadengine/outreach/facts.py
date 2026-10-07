"""Everything a cold email may say about a lead, taken only from what we actually measured.

Nothing here is invented: issues come from the Website Score reasons, ads from the ads check,
the competitor from the same Google Maps searches, the link from the preview builder."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadengine.db.models import Business, SearchResult
from leadengine.db.repo import Repository
from leadengine.normalize import is_shared_domain, normalize_domain

COMPANY_WORDS = re.compile(r"\b(llc|inc|co|corp|company|ltd|services?|group|team|plumbing|septic|roofing)\b", re.I)


@dataclass
class Competitor:
    name: str
    site_score: int | None
    ads_status: str | None
    lsa: bool
    rating: float | None
    review_count: int | None
    better_on: list[str] = field(default_factory=list)   # e.g. ["website", "google ads"]


@dataclass
class OutreachFacts:
    business_id: int
    name: str
    first_name: str | None
    category: str
    city: str
    state: str
    keyword: str | None
    email: str | None
    email_status: str | None
    website: str | None
    domain: str | None
    rating: float | None
    review_count: int | None
    site_score: int | None
    site_grade: str | None
    no_website: bool
    issues: list[str]                      # plain-English, outreach-ready, worst first
    issue_kinds: list[str]                 # mobile | speed | security | outdated | design | conversion | tech | broken
    pagespeed: int | None
    ads_status: str | None
    lsa: bool
    ads_evidence: list[str]
    competitor: Competitor | None
    preview_url: str | None                # public link (only when the preview was published)
    preview_image: str | None              # local screenshot of the preview (can be attached)
    sender_name: str = ""
    agency: str = ""
    offer: str = ""
    landing_issues: list[str] = field(default_factory=list)   # problems on the page their ads point to
    landing_url: str | None = None
    landing_score: int | None = None
    audit_url: str | None = None        # published audit report (only when it has a public link)
    map_top3: int | None = None         # rank map: top-3 spots out of map_points
    map_points: int | None = None
    review_line: str | None = None      # e.g. "6 negative Google reviews have no reply yet"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def first_name(owner: str | None) -> str | None:
    """'John A. Smith' -> 'John'; company-looking names -> None."""
    if not owner or COMPANY_WORDS.search(owner):
        return None
    part = owner.strip().split()[0].strip(".,")
    if part.lower() in {"mr", "mrs", "ms", "dr"} and len(owner.split()) > 1:
        return None                                    # 'Mr Smith' -> greet by company instead
    return part.capitalize() if part.isalpha() and 1 < len(part) < 20 else None


def plain_issue(reason: str) -> tuple[str, str]:
    """Website Score reason -> (kind, sentence fragment a business owner understands)."""
    r = reason.strip()
    low = r.lower()
    if low.startswith("not mobile friendly"):
        notes = re.search(r"\((.+)\)", r)
        detail = notes.group(1) if notes else ""
        if "shrunken desktop" in detail:
            return "mobile", "on a phone it shows a shrunken desktop page, so people have to pinch and zoom"
        if "wider than a phone" in detail:
            return "mobile", "on a phone the page is wider than the screen and scrolls sideways"
        return "mobile", "it isn't set up for phones" + (f" ({detail})" if detail else "")
    if low.startswith("slow on mobile"):
        m = re.search(r"pagespeed (\d+)/100(?:, loads in ([\d.]+)s)?", low)
        if m:
            secs = f" and takes about {m.group(2)} seconds to load" if m.group(2) else ""
            return "speed", f"it scores {m.group(1)}/100 on Google's mobile speed test{secs}"
        return "speed", "it loads slowly on phones"
    if "no secure https" in low:
        return "security", "browsers show it as \"Not secure\" because it has no HTTPS"
    if "certificate expires" in low or "ssl certificate" in low:
        return "security", "its security certificate " + r.split("certificate", 1)[1].strip()
    m = re.search(r"(?:copyright|sitemap updated|last-modified).*?(\d{4}).*?~(\d+) years", low)
    if m:
        return "outdated", f"it looks like it hasn't been updated since around {m.group(1)}"
    if low.startswith("design looks outdated"):
        return "design", "the design looks dated next to newer sites"
    if low == "website does not load":
        return "broken", "the website didn't load when I tried it"
    if low.startswith("no real website"):
        host = re.search(r"\((.+)\)", r)
        return "no_site", f"your Google listing points to {host.group(1) if host else 'a page'} instead of your own website"
    if any(k in low for k in ("click-to-call", "quote/booking form", "call-to-action", "reviews shown")):
        return "conversion", "the site has " + low
    return "tech", r[0].lower() + r[1:]


def find_competitor(session: Session, biz: Business, name_it: bool) -> Competitor | None:
    """Strongest business from the same searches that beats this one on website and/or ads."""
    search_ids = select(SearchResult.search_id).where(SearchResult.business_id == biz.id)
    rows = session.scalars(
        select(Business).join(SearchResult, SearchResult.business_id == Business.id)
        .where(SearchResult.search_id.in_(search_ids), Business.id != biz.id).distinct())
    mine = biz.website_score if biz.website else 0
    best: tuple[tuple, Competitor] | None = None
    for other in rows:
        better = []
        if other.website and other.website_score is not None and other.website_score >= (mine or 0) + 20:
            better.append("website")
        other_ads = other.ads_status in ("Active", "Likely") and biz.ads_status not in ("Active",)
        if other_ads:
            better.append("google ads")
        if other.lsa and not biz.lsa:
            better.append("local services ads")
        if not better:
            continue
        key = (len(better), other.website_score or 0, other.review_count or 0)
        comp = Competitor(name=other.name if name_it else "", site_score=other.website_score,
                          ads_status=other.ads_status, lsa=bool(other.lsa), rating=other.rating,
                          review_count=other.review_count, better_on=better)
        if best is None or key > best[0]:
            best = (key, comp)
    return best[1] if best else None


def gather(session: Session, biz: Business, cfg: dict[str, Any], brand: dict[str, Any]) -> OutreachFacts:
    repo = Repository(session)
    web = repo.latest_enrichment(biz.id, "website", fresh_only=False)
    ads = repo.latest_enrichment(biz.id, "ads", fresh_only=False)
    prev = repo.latest_enrichment(biz.id, "preview", fresh_only=False)
    land = repo.latest_enrichment(biz.id, "landing", fresh_only=False)
    land_p = (land.payload or {}) if land else {}
    aud = repo.latest_enrichment(biz.id, "audit", fresh_only=False)
    rank = repo.latest_enrichment(biz.id, "rank", fresh_only=False)
    rank_p = (rank.payload or {}) if rank else {}
    rev = repo.latest_enrichment(biz.id, "reviews", fresh_only=False)
    rev_p = (rev.payload or {}) if rev else {}
    web_p = (web.payload or {}) if web else {}
    ads_p = (ads.payload or {}) if ads else {}
    prev_p = (prev.payload or {}) if prev else {}

    issues: list[str] = []
    kinds: list[str] = []
    no_website = not biz.website or is_shared_domain(biz.domain or normalize_domain(biz.website))
    if no_website and biz.website and not any(k == "no_site" for k in kinds):
        issues.append(f"your Google listing points to {normalize_domain(biz.website)} instead of your own website")
        kinds.append("no_site")
    for reason in web_p.get("reasons") or []:
        if reason == "no website":
            no_website = True
            continue
        kind, text = plain_issue(reason)
        if kind == "no_site":
            no_website = True
        if kind not in kinds or kind == "tech":
            issues.append(text)
            kinds.append(kind)
    keywords = repo.keywords_for(biz.id)
    return OutreachFacts(
        business_id=biz.id, name=biz.name, first_name=first_name(biz.owner_name),
        category=(biz.categories or [keywords[0] if keywords else "local service"])[0],
        city=biz.city or "", state=biz.state or "", keyword=keywords[0] if keywords else None,
        email=biz.best_email, email_status=biz.email_status,
        rating=biz.rating, review_count=biz.review_count, site_score=biz.website_score,
        site_grade=biz.website_grade, no_website=no_website, issues=issues[:4], issue_kinds=kinds[:4],
        pagespeed=(web_p.get("pagespeed") or {}).get("performance"),
        ads_status=biz.ads_status, lsa=bool(biz.lsa), ads_evidence=list(ads_p.get("evidence") or [])[:3],
        website=None if no_website else biz.website, domain=None if no_website else biz.domain,
        competitor=find_competitor(session, biz, bool(cfg.get("name_competitors"))),
        preview_url=prev_p.get("url"), preview_image=prev_p.get("screenshot"),
        sender_name=cfg.get("sender_name", ""), agency=cfg.get("agency") or brand.get("brand_name", ""),
        offer=cfg.get("offer", ""),
        landing_issues=list(land_p.get("issues") or [])[:3],
        landing_url=land_p.get("final_url") or land_p.get("url"),
        landing_score=land_p.get("score"),
        audit_url=((aud.payload or {}).get("url") if aud else None),
        map_top3=rank_p.get("top3"), map_points=rank_p.get("points"),
        review_line=review_line(rev_p),
    )


def review_line(audit: dict) -> str | None:
    """One outreach-ready sentence from the reviews audit (unanswered negatives first)."""
    un = audit.get("unanswered_negative") or []
    if un:
        n = len(un)
        quote = next((u["text"] for u in un if len(u.get("text") or "") >= 20), None)
        q = f" (one says \"{quote[:70].rstrip()}…\")" if quote else ""
        return (f"{n} negative Google review{'s have' if n > 1 else ' has'} no reply from you yet{q} — "
                "future customers read those before they call")
    if audit.get("last_review_days") and audit["last_review_days"] > 60:
        return f"your last Google review was {audit['last_review_days']} days ago, and Google favours fresh reviews"
    if audit.get("reply_rate") is not None and audit["reply_rate"] < 30:
        return f"you've replied to only {audit['reply_rate']}% of your recent Google reviews"
    return None

