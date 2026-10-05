"""Website Score 0–100 (higher = more modern / healthier). Every signal is stored with its
points and a plain-English reason, so the score is explainable in an outreach email."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from leadengine.enrich.website.signals import OUTDATED_TECH, OUTDATED_VERSIONS, HtmlSignals, version_tuple
from leadengine.enrich.website.tech import CAT_BOOKING, CAT_LIVE_CHAT, CAT_REVIEWS, Tech

WEIGHTS = {"security": 10, "mobile": 15, "freshness": 15, "tech": 15, "speed": 15, "conversion": 15,
           "basics": 5, "design": 10}


@dataclass
class Signal:
    name: str
    points: float          # 0..max
    max: float
    detail: str


@dataclass
class WebsiteScore:
    score: int | None
    grade: str
    signals: dict[str, Signal] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)      # weaknesses, worst first (for pitches)
    positives: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"score": self.score, "grade": self.grade, "flags": self.flags, "reasons": self.reasons,
                "positives": self.positives,
                "signals": {k: {"points": round(v.points, 1), "max": v.max, "detail": v.detail}
                            for k, v in self.signals.items()}}


def grade_for(score: int | None) -> str:
    if score is None:
        return "n/a"
    if score >= 80:
        return "Modern"
    if score >= 60:
        return "OK"
    if score >= 40:
        return "Dated"
    return "Outdated"


def compute_score(
    html: HtmlSignals | None,
    techs: list[Tech],
    *,
    today: date,
    https_ok: bool | None,
    cert: dict[str, Any] | None,
    render: dict[str, Any] | None,
    pagespeed: dict[str, Any] | None,
    sitemap_lastmod: str | None,
    last_modified_header: str | None,
    wayback: dict[str, Any] | None,
    vision: dict[str, Any] | None,
    flags: list[str],
) -> WebsiteScore:
    result = WebsiteScore(None, "n/a", flags=list(flags))
    if html is None:
        return result
    reasons: list[tuple[float, str]] = []     # (severity, text)
    S = result.signals

    def add(name: str, frac: float, detail: str) -> None:
        frac = max(0.0, min(1.0, frac))
        S[name] = Signal(name, frac * WEIGHTS[name], WEIGHTS[name], detail)

    # security
    if https_ok is not None:
        days = (cert or {}).get("days_left")
        if not https_ok:
            add("security", 0, (cert or {}).get("error") or "no working HTTPS")
            reasons.append((9, "no secure HTTPS (browsers show 'Not secure')"))
        elif days is not None and days < 14:
            add("security", 0.5, f"certificate expires in {days} days")
            reasons.append((5, f"SSL certificate expires in {days} days"))
        else:
            add("security", 1, "valid HTTPS")
            result.positives.append("valid HTTPS")

    # mobile
    mob = (render or {}).get("mobile")
    if mob is not None or html is not None:
        frac, notes = 1.0, []
        if not html.viewport:
            frac -= 0.6
            notes.append("no mobile viewport tag")
        if mob:
            if mob.get("viewport_width", 0) > 500:
                frac -= 0.4
                notes.append("phones show a shrunken desktop page")
            if mob.get("horizontal_overflow_px", 0) > 20:
                frac -= 0.4
                notes.append(f"page is {mob['horizontal_overflow_px']}px wider than a phone screen")
            if mob.get("small_text_ratio", 0) > 0.3:
                frac -= 0.2
                notes.append("text too small on phones")
        add("mobile", frac, "; ".join(notes) or "mobile friendly")
        if frac < 0.6:
            reasons.append((10, "not mobile friendly (" + ", ".join(notes) + ")"))

    # freshness
    years: list[tuple[int, str]] = []
    if html.copyright_year:
        years.append((html.copyright_year, f"copyright {html.copyright_year}"))
    if sitemap_lastmod:
        years.append((int(sitemap_lastmod[:4]), f"sitemap updated {sitemap_lastmod}"))
    if last_modified_header:
        try:
            from email.utils import parsedate_to_datetime
            years.append((parsedate_to_datetime(last_modified_header).year, "page Last-Modified header"))
        except (TypeError, ValueError):
            pass
    if years:
        newest, why = max(years)
        age = today.year - newest
        add("freshness", 1 - max(0, age - 1) / 8, why)
        if age >= 3:
            reasons.append((8 + min(age, 10) / 10, f"{why} (~{age} years without updates)"))
        else:
            result.positives.append(why)
    elif wayback and wayback.get("first_year"):
        add("freshness", 0.5, f"online since {wayback['first_year']}, no update date found")

    # tech
    penalties: list[str] = []
    names = {t.name: t for t in techs}
    for tech_name, min_version, text in OUTDATED_VERSIONS:
        t = names.get(tech_name)
        v = version_tuple(t.version if t else None)
        if v and v < min_version:
            penalties.append(text.format(v=t.version))
    penalties += [why for name, why in OUTDATED_TECH.items() if name in names]
    if html.flash and "uses Flash (dead since 2020)" not in penalties:
        penalties.append("uses Flash (dead since 2020)")
    if html.table_layout:
        penalties.append("table-based layout (1990s/2000s technique)")
    if html.frames:
        penalties.append("uses frames")
    if html.deprecated_tags:
        penalties.append("old HTML tags: " + ", ".join(html.deprecated_tags))
    if not html.html5_doctype:
        penalties.append("pre-HTML5 page")
    add("tech", 1 - 0.3 * len(penalties), "; ".join(penalties) or "no outdated technology found")
    for p in penalties[:3]:
        reasons.append((7, p))

    # speed
    perf = (pagespeed or {}).get("performance")
    if perf is not None:
        add("speed", perf / 100, f"Google PageSpeed mobile {perf}/100")
        if perf < 50:
            lcp = (pagespeed or {}).get("lcp_ms")
            extra = f", loads in {lcp / 1000:.1f}s" if lcp else ""
            reasons.append((7 + (50 - perf) / 50, f"slow on mobile (PageSpeed {perf}/100{extra})"))
        elif perf >= 80:
            result.positives.append(f"fast (PageSpeed {perf})")

    # conversion basics
    cats = {c for t in techs for c in t.categories}
    conv = 0.0
    missing = []
    if html.tel_links:
        conv += 0.3
    else:
        missing.append("no click-to-call button")
    if html.quote_form or CAT_BOOKING in cats:
        conv += 0.3
    else:
        missing.append("no quote/booking form")
    if html.cta_phrases:
        conv += 0.2
    else:
        missing.append("no clear call-to-action")
    if html.reviews_section or CAT_REVIEWS in cats:
        conv += 0.1
    else:
        missing.append("no reviews shown")
    if CAT_BOOKING in cats or CAT_LIVE_CHAT in cats:
        conv += 0.1
    add("conversion", conv, "; ".join(missing) or "click-to-call, form, CTA and reviews present")
    if missing:
        reasons.append((6 + len(missing) / 2, ", ".join(missing)))

    # basics
    basics = sum([bool(html.title), html.meta_description, html.html5_doctype,
                  html.images == 0 or html.images_without_alt / max(1, html.images) < 0.5, html.word_count >= 150])
    add("basics", basics / 5, f"{basics}/5 basic SEO checks")

    # design (AI vision)
    if vision and vision.get("outdated_1_10") is not None:
        outdated = float(vision["outdated_1_10"])
        add("design", 1 - (outdated - 1) / 9, f"AI design review: {outdated:.0f}/10 outdated")
        if outdated >= 6:
            reasons.append((6 + outdated / 10, "design looks outdated: " + (vision.get("why") or "")[:120]))

    available = sum(s.max for s in S.values())
    got = sum(s.points for s in S.values())
    result.score = round(100 * got / available) if available else None
    result.grade = grade_for(result.score)
    result.reasons = [text for _, text in sorted(reasons, key=lambda r: -r[0])]
    return result
