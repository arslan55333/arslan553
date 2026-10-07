"""Cold email drafts: 3 angles (short / detailed / competitor) + a follow-up sequence.

AI writes from the measured facts only; a post-filter removes invented claims, foreign links and
sign-offs. The CAN-SPAM footer (your address + unsubscribe line) is added by code, never by the AI."""

from __future__ import annotations

import re
from typing import Any

from leadengine.db.models import utcnow
from leadengine.llm import LLM, LLMError
from leadengine.log import get_logger
from leadengine.outreach.facts import OutreachFacts

log = get_logger("outreach")

ANGLES = ("short", "detailed", "competitor")
LINK = "[PREVIEW_LINK]"
ADDRESS_MISSING = "[ADD YOUR MAILING ADDRESS: config.toml [outreach] physical_address]"
# claims we never let into an email unless they come from you (offer line)
RISKY = re.compile(r"#\s?1\b|\$\s?\d|\d+\s?%|\b(number one|award|licen[sc]ed|insured|bonded|certified|"
                   r"guarantee[ds]?|warrant(y|ies)|cheapest|lowest price|best in|double your|triple your|"
                   r"first page of google|top of google|page one of google|risk[- ]free|act now|limited time)\b", re.I)
SIGNOFF = re.compile(r"^(best|thanks|thank you|cheers|regards|kind regards|best regards|sincerely|talk soon|"
                     r"warmly|all the best)[,!.]?\s*$", re.I)
URL = re.compile(r"https?://\S+|www\.\S+", re.I)
TIPS = {
    "mobile": "adding a proper mobile layout (viewport tag + larger text) usually stops phone visitors bouncing",
    "speed": "compressing the homepage images is often enough to cut the load time in half",
    "security": "turning on free HTTPS (Let's Encrypt) removes the \"Not secure\" warning in Chrome",
    "outdated": "fresh photos of recent jobs and a current year in the footer make a site feel active again",
    "design": "a single clear headline with your phone number up top does more than any redesign trend",
    "conversion": "a tap-to-call button at the top of every page is the quickest win for more calls",
    "broken": "it's worth checking your hosting, as visitors from Google are hitting a dead page",
    "no_site": "even a simple one-page site with your services and reviews helps Google trust your listing",
    "tech": "updating the site's software closes security holes and usually speeds it up",
}


# ── helpers ───────────────────────────────────────────────────────────
def trade(category: str) -> str:
    """'Construction company' -> 'construction', 'Waste management service' -> 'waste management'."""
    c = re.sub(r"\s+(company|companies|service|services|contractor|contractors|business)$", "", category.strip(), flags=re.I)
    return (c or category).lower()


def greeting(f: OutreachFacts) -> str:
    return f"Hi {f.first_name}," if f.first_name else f"Hi {f.name} team,"


def where(f: OutreachFacts) -> str:
    return f.city or "your area"


def _rating_line(f: OutreachFacts) -> str | None:
    if f.rating and f.review_count and f.rating >= 4.3 and f.review_count >= 20:
        return f"{f.review_count} Google reviews at {f.rating:.1f} stars say your customers are happy"
    return None


def _preview_sentence(f: OutreachFacts, attach: bool) -> str:
    if f.preview_url:
        return f"I put together a free concept of a modern site for {f.name} so you can see the difference: {LINK}"
    if f.preview_image and attach:
        return f"I put together a free concept of a modern homepage for {f.name} - screenshot attached."
    return f"I'd be happy to mock up a free concept of a modern homepage for {f.name} so you can see the difference."


def _comp_phrase(f: OutreachFacts) -> str | None:
    c = f.competitor
    if not c:
        return None
    who = c.name or f"another {f.category.lower()} company nearby"
    bits = []
    if "website" in c.better_on and c.site_score is not None:
        mine = (f"yours {f.site_score}/100" if f.site_score is not None and not f.no_website
                else "while you don't have your own site yet")
        bits.append(f"a much stronger website (it scores {c.site_score}/100 in my check, {mine})")
    if "google ads" in c.better_on:
        bits.append("Google Ads running")
    if "local services ads" in c.better_on:
        bits.append("a Local Services ad showing above the normal results")
    return f"I noticed {who} has " + " and ".join(bits) if bits else None


def _issue_lines(f: OutreachFacts, n: int) -> list[str]:
    if f.no_website:
        social = [i for i, k in zip(f.issues, f.issue_kinds) if k == "no_site"]
        return social[:1] or ["people who find you on Google Maps have no website to check before calling"]
    return f.issues[:n] or ["the site could do a lot more to turn visitors into calls"]


def _ads_line(f: OutreachFacts) -> str | None:
    if f.landing_issues and f.ads_status in ("Active", "Likely"):
        return (f"You're paying for Google Ads, but the page those ads send people to has a problem: "
                f"{f.landing_issues[0]}. Every paid click that lands there and leaves is money spent twice.")
    if f.ads_status in ("Active", "Likely") and f.no_website:
        return ("It also looks like you're paying for Google Ads - without a site of your own, that paid traffic "
                "lands somewhere you can't control or measure.")
    if f.ads_status == "Active":
        return ("You're also running Google Ads, so every visitor who leaves a slow or hard-to-use page "
                "is paid traffic going to waste.")
    if f.ads_status == "Likely":
        return "It looks like you've set up Google Ads tracking, so a better landing page would make that spend go further."
    if f.ads_status == "Past":
        return "I noticed you've run Google Ads before - a stronger site makes any future ad spend go a lot further."
    return None


# ── template drafts ──────────────────────────────────────────────────
def template_drafts(f: OutreachFacts, cfg: dict[str, Any], attach: bool = False) -> dict[str, Any]:
    issue = _issue_lines(f, 1)[0]
    rating = _rating_line(f)
    preview = _preview_sentence(f, attach)
    offer = (cfg.get("offer") or "").strip()
    subject_topic = {"mobile": "on phones", "speed": "speed", "security": "\"Not secure\" warning",
                     "outdated": "website", "design": "website"}.get((f.issue_kinds or ["website"])[0], "website")
    if f.no_website:
        subject_topic = "website"

    intro = f"I was looking at {trade(f.category)} companies in {where(f)} and came across {f.name}."
    short_mid = (f"{rating} - but {issue}." if rating else f"One thing stood out: {issue}.")
    short = "\n\n".join([greeting(f), f"{intro} {short_mid}", preview, "Worth a quick look?"])

    found = _issue_lines(f, 3)
    bullets = "\n".join(f"- {i}" for i in found)
    things = "a few things that are" if len(found) > 1 else "one thing that's"
    checked = f"I ran a quick check on {f.domain}" if f.domain and not f.no_website else "I took a quick look online"
    detailed_parts = [greeting(f), f"{intro} {checked} and noticed {things} likely costing you calls:",
                      bullets]
    if ads := _ads_line(f):
        detailed_parts.append(ads)
    if f.money_line:
        detailed_parts.append(f"{f.money_line[0].upper()}{f.money_line[1:]}.")
    if f.review_line:
        detailed_parts.append(f"One more thing I noticed: {f.review_line}.")
    detailed_parts.append(preview + (f" {offer}" if offer else ""))
    detailed_parts.append("Would you be open to a 10-minute call this week?")
    detailed = "\n\n".join(detailed_parts)

    comp = _comp_phrase(f)
    if comp:
        search = f"\"{f.keyword}\" in {where(f)}" if f.keyword else f"{f.category.lower()} in {where(f)}"
        c = f.competitor
        lines = [f"When I searched {search}, {comp}."]
        if f.review_count and c and c.review_count is not None and f.review_count > c.review_count:
            lines.append(f"You actually have more Google reviews ({f.review_count} vs {c.review_count}).")
        lines.append((f"Right now, {issue}." if f.no_website else f"Looking at {f.domain or 'your site'}, {issue}."))
        competitor = "\n\n".join([greeting(f), " ".join(lines), preview,
                                   "Want me to send over what it would take to get ahead?"])
        comp_subject = f"{f.name} vs. the competition"
    else:
        lead = rating or f"People in {where(f)} can find {f.name} on Google Maps"
        gap = (f"{lead}, but without a website of your own, {issue}." if f.no_website else
               f"{lead}, but the website isn't doing that justice - {issue}.")
        competitor = "\n\n".join([
            greeting(f), gap,
            "When someone compares you with the next company on Google, the site is often what decides who gets the call.",
            preview, "Open to a quick look?"])
        comp_subject = "Your reviews vs. your website"

    variants = [
        {"angle": "short", "subject": f"{f.name} {subject_topic}".strip(), "body": short},
        {"angle": "detailed", "subject": f"A few quick fixes for {f.name}", "body": detailed},
        {"angle": "competitor", "subject": comp_subject, "body": competitor},
    ]
    return {"variants": variants, "followups": template_followups(f, cfg, attach)}


def template_followups(f: OutreachFacts, cfg: dict[str, Any], attach: bool = False) -> list[dict[str, Any]]:
    days = list(cfg.get("followup_days") or [3, 7, 14])
    kind = (["no_site"] if f.no_website else f.issue_kinds or ["conversion"])[0]
    link = f" The concept is here: {LINK}" if f.preview_url else ""
    bodies = [
        f"{greeting(f)}\n\nJust bumping this in case it got buried.{link} Happy to walk you through it in 10 minutes.",
        f"{greeting(f)}\n\nOne quick tip, even if we never work together: {TIPS.get(kind, TIPS['conversion'])}.\n\n"
        "If you'd like a hand with it, just reply.",
        f"{greeting(f)}\n\nI'll stop reaching out after this one. If a refreshed website ever makes the list, "
        f"just reply and I'll pick it up.{link} All the best with {f.name}.",
    ]
    out = []
    for i, day in enumerate(days[:3]):
        out.append({"day": int(day), "subject": "", "body": bodies[min(i, len(bodies) - 1)]})
    return out


# ── AI drafts ────────────────────────────────────────────────────────
PROMPT = """Write cold emails from a small web-design agency to a local business owner.

FACTS (measured by our tools - the ONLY facts you may use):
{facts}

Write:
1. Three first-email variants:
   - "short": 50-80 words. One problem, the concept preview, one soft question.
   - "detailed": 90-{max_words} words. Up to 3 problems as a short "- " list, the ads angle if the facts mention ads, the preview.
   - "competitor": compare with the competitor in the facts (if competitor is null, compare their strong reviews with their weak website instead).
2. {n_followups} short follow-ups (30-60 words each): a polite bump, one genuinely useful free tip tied to their main problem, and a friendly last note.

Rules:
- Use ONLY the facts. Never invent numbers, percentages, prices, results, guarantees, rankings, years in business, or awards.
- Start each body with "{greeting}" on its own line. Plain text, short paragraphs, no markdown, no emojis.
- {link_rule}
- No other links. No signature, sign-off, name or address at the end (we add those).
- Subjects: 2-6 words, specific to the business, no "Re:" or "Fwd:", no clickbait, no ALL CAPS, no "free".
- Sound like one helpful human, not marketing. No hype words (amazing, revolutionary, skyrocket).
Return JSON: {{"variants": [{{"angle": "short", "subject": "...", "body": "..."}}, {{"angle": "detailed", ...}}, {{"angle": "competitor", ...}}],
"followups": [{{"body": "..."}}, ...]}}"""


def _facts_for_prompt(f: OutreachFacts, attach: bool) -> dict[str, Any]:
    keep = {k: v for k, v in f.as_dict().items()
            if k not in ("business_id", "email", "email_status", "preview_image", "issue_kinds", "sender_name")
            and v not in (None, "", [], False)}
    keep["has_preview"] = bool(f.preview_url) or (bool(f.preview_image) and attach)
    keep.pop("preview_url", None)
    return keep


def clean_body(text: str, f: OutreachFacts) -> str:
    """Drop risky sentences, foreign links and model-added sign-offs; normalise the preview link token."""
    text = text.replace("\r\n", "\n").replace("[preview_link]", LINK).replace("{PREVIEW_LINK}", LINK)
    out_lines = []
    for line in text.split("\n"):
        sentences = re.split(r"(?<=[.!?])\s+", line)
        kept = []
        for s in sentences:
            if RISKY.search(s):
                continue
            if URL.search(s):
                if f.preview_url and f.preview_url in s:
                    s = s.replace(f.preview_url, LINK)
                elif not (f.audit_url and f.audit_url in s):
                    continue
            kept.append(s)
        out_lines.append(" ".join(kept).rstrip())
    while out_lines and (not out_lines[-1].strip() or SIGNOFF.match(out_lines[-1].strip())
                         or out_lines[-1].strip() in (f.sender_name, f.agency)):
        out_lines.pop()
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
    if f.preview_url and LINK not in body:
        body += f"\n\nHere's the concept: {LINK}"
    if not f.preview_url:
        body = body.replace(LINK, "").strip()
    return body


def clean_subject(text: str, fallback: str) -> str:
    s = re.sub(r"^\s*((re|fwd?|fw)\s*:\s*)+", "", str(text or ""), flags=re.I).strip().strip('"')
    s = re.sub(r"\s+", " ", s)
    if not s or RISKY.search(s) or s.isupper():
        return fallback
    return s[:90]


async def ai_drafts(llm: LLM, f: OutreachFacts, cfg: dict[str, Any], attach: bool = False) -> dict[str, Any]:
    fallback = template_drafts(f, cfg, attach)
    days = list(cfg.get("followup_days") or [3, 7, 14])[:3]
    link_rule = (f"Include the token {LINK} exactly once where the concept-site link goes."
                 if f.preview_url else
                 "A screenshot of the concept homepage is attached; mention it." if (f.preview_image and attach) else
                 "No concept site exists yet: offer to mock one up for free.")
    prompt = PROMPT.format(facts=_facts_for_prompt(f, attach), max_words=int(cfg.get("max_words", 150)),
                           n_followups=len(days), greeting=greeting(f), link_rule=link_rule)
    data = await llm.complete_json(prompt, max_tokens=3000)
    if not isinstance(data, dict):
        raise ValueError("AI reply was not a JSON object")
    by_angle = {str(v.get("angle", "")).lower(): v for v in data.get("variants") or [] if isinstance(v, dict)}
    variants = []
    for fb in fallback["variants"]:
        v = by_angle.get(fb["angle"])
        body = clean_body(str(v.get("body") or ""), f) if v else ""
        ok = len(body.split()) >= 25
        variants.append({"angle": fb["angle"], "subject": clean_subject(v.get("subject"), fb["subject"]) if ok else fb["subject"],
                         "body": body if ok else fb["body"], "source": "ai" if ok else "template"})
    followups = []
    ai_fu = [x for x in data.get("followups") or [] if isinstance(x, dict)]
    for i, fb in enumerate(fallback["followups"]):
        body = clean_body(str(ai_fu[i].get("body") or ""), f) if i < len(ai_fu) else ""
        ok = len(body.split()) >= 12
        followups.append({"day": fb["day"], "subject": "", "body": body if ok else fb["body"],
                          "source": "ai" if ok else "template"})
    return {"variants": variants, "followups": followups}


def add_extras(body: str, f: OutreachFacts) -> str:
    """Measured extras the reader can verify: map visibility and the audit link (added by code, not AI)."""
    paras = body.split("\n\n")
    extra = []
    if f.map_points and f.map_top3 is not None and f.map_top3 < f.map_points / 2 and "map" not in body.lower():
        extra.append(f"I also checked Google Maps from {f.map_points} spots around {f.city or 'your area'}: "
                     f"{f.name} shows in the top 3 at {f.map_top3} of them.")
    if f.audit_url and f.audit_url not in body:
        extra.append(f"Here's a one-page audit with everything I found: {f.audit_url}")
    if not extra:
        return body
    at = max(1, len(paras) - 1)            # before the closing question
    return "\n\n".join(paras[:at] + [" ".join(extra)] + paras[at:])


async def make_drafts(f: OutreachFacts, cfg: dict[str, Any], llm: LLM | None, attach: bool = False) -> dict[str, Any]:
    source = "template"
    drafts = None
    if llm is not None:
        try:
            drafts = await ai_drafts(llm, f, cfg, attach)
            source = "ai"
        except (LLMError, ValueError, TypeError, AttributeError) as exc:
            log.warning("AI drafts failed, using templates", extra={"data": {"name": f.name, "error": str(exc)[:150]}})
    if drafts is None:
        drafts = template_drafts(f, cfg, attach)
        for d in drafts["variants"] + drafts["followups"]:
            d["source"] = "template"
    for v in drafts["variants"]:
        if v["angle"] != "short":
            v["body"] = add_extras(v["body"], f)
    warnings = []
    if not f.email:
        warnings.append("no email address found yet - use the contact form or phone, or run `emails` first")
    elif f.email_status in ("invalid",):
        warnings.append(f"email {f.email} failed verification - do not send")
    elif f.email_status not in ("valid",):
        warnings.append(f"email {f.email} is not verified ({f.email_status or 'unchecked'})")
    if not f.preview_url:
        warnings.append("preview not published - build/deploy one for a link" if not f.preview_image
                        else "preview not published - the screenshot can be attached instead of a link")
    if not (cfg.get("physical_address") or "").strip():
        warnings.append("set physical_address in [outreach] (required by CAN-SPAM before sending)")
    return {**drafts, "source": source, "to": f.email, "to_status": f.email_status,
            "preview_url": f.preview_url, "attachment": f.preview_image, "warnings": warnings,
            "facts": f.as_dict(), "generated_at": utcnow().isoformat(timespec="seconds")}


# ── final text (what actually goes out) ──────────────────────────────
def footer(cfg: dict[str, Any], brand_name: str = "") -> str:
    agency = cfg.get("agency") or brand_name
    address = (cfg.get("physical_address") or "").strip() or ADDRESS_MISSING
    unsub = (cfg.get("unsubscribe_line") or "").strip() or "Reply \"unsubscribe\" and I won't email you again."
    return "\n".join(filter(None, ["--", " · ".join(filter(None, [agency, address])), unsub]))


def signature(cfg: dict[str, Any], brand_name: str = "") -> str:
    return "\n".join(filter(None, [cfg.get("sender_name", ""), cfg.get("agency") or brand_name]))


def final_body(body: str, cfg: dict[str, Any], *, preview_url: str | None = None, brand_name: str = "") -> str:
    text = body.replace(LINK, preview_url or "").rstrip()
    sig = signature(cfg, brand_name)
    return f"{text}\n\n{sig}\n\n{footer(cfg, brand_name)}" if sig else f"{text}\n\n{footer(cfg, brand_name)}"


def followup_subject(first_subject: str) -> str:
    return first_subject if first_subject.lower().startswith("re:") else f"Re: {first_subject}"
