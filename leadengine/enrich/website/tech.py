"""Technology detection with Wappalyzer-format fingerprints.

* Built-in rules (``BUILTIN``, written for this project) cover what matters for
  judging a local-business site: CMS/builders and their versions, old JS libraries,
  Flash, booking/review/chat widgets, analytics, ads and call tracking.
* Optional: the full open-source webappanalyzer set (GPL-3.0) can be downloaded to
  ``data/webappanalyzer/`` with ``python -m leadengine update-fingerprints``; it is
  merged automatically when present. It is not shipped with this project.

Pattern syntax: ``regex\\;version:\\1\\;confidence:50`` (Wappalyzer convention).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from leadengine.config import PROJECT_ROOT

FINGERPRINT_DIR = PROJECT_ROOT / "data" / "webappanalyzer"
WEBAPPANALYZER_BASE = "https://raw.githubusercontent.com/enthec/webappanalyzer/main/src"

CAT_CMS, CAT_JS_LIB, CAT_ANALYTICS, CAT_ADS, CAT_PAGE_BUILDER = 1, 59, 10, 36, 51
CAT_WIDGETS, CAT_LIVE_CHAT, CAT_BOOKING, CAT_REVIEWS, CAT_TAG_MANAGER = 5, 52, 72, 90, 42
CAT_RETARGETING, CAT_CALL_TRACKING, CAT_FORMS, CAT_PARKING = 77, 32, 110, 109

BUILTIN: dict[str, dict[str, Any]] = {
    # CMS / builders
    "WordPress": {"cats": [CAT_CMS], "meta": {"generator": r"^WordPress ?([\d.]+)?\;version:\1"},
                  "html": [r"/wp-(?:content|includes)/"], "scriptSrc": [r"/wp-includes/.*?ver=([\d.]+)\;version:\1"]},
    "Wix": {"cats": [CAT_CMS], "meta": {"generator": r"Wix\.com"}, "html": [r"static\.wixstatic\.com"],
            "headers": {"x-wix-request-id": ""}},
    "Squarespace": {"cats": [CAT_CMS], "html": [r"static1\.squarespace\.com|Static\.SQUARESPACE_CONTEXT"]},
    "Weebly": {"cats": [CAT_CMS], "html": [r"weebly\.com|wsite-"]},
    "GoDaddy Website Builder": {"cats": [CAT_CMS], "meta": {"generator": r"Starfield Technologies|Go Daddy Website Builder"},
                                "html": [r"img1\.wsimg\.com|godaddysites\.com"]},
    "Duda": {"cats": [CAT_CMS], "html": [r"dudamobile\.com|irp\.cdn-website\.com|lirp\.cdn-website\.com"]},
    "Shopify": {"cats": [CAT_CMS], "html": [r"cdn\.shopify\.com"]},
    "Joomla": {"cats": [CAT_CMS], "meta": {"generator": r"Joomla!? ?([\d.]+)?\;version:\1"}},
    "Drupal": {"cats": [CAT_CMS], "meta": {"generator": r"Drupal ?([\d.]+)?\;version:\1"}},
    "Webflow": {"cats": [CAT_CMS], "html": [r"data-wf-page|assets\.website-files\.com"]},
    "HubSpot CMS": {"cats": [CAT_CMS], "html": [r"hs-sites\.com|/hs/hsstatic/"]},
    "Microsoft FrontPage": {"cats": [CAT_CMS, 20], "meta": {"generator": r"Microsoft FrontPage ?([\d.]+)?\;version:\1"}},
    "Adobe Dreamweaver": {"cats": [20], "html": [r"MM_swapImage|MM_preloadImages|<!-- InstanceBegin"]},
    "Apple iWeb": {"cats": [CAT_CMS], "meta": {"generator": r"iWeb"}},
    "Homestead": {"cats": [CAT_CMS], "html": [r"homestead\.com"]},
    "Web.com Builder": {"cats": [CAT_CMS], "html": [r"websitebuilder\.web\.com|web\.com/static"]},
    "Yahoo SiteBuilder": {"cats": [CAT_CMS], "meta": {"generator": r"Yahoo!? SiteBuilder"}},
    "Elementor": {"cats": [CAT_PAGE_BUILDER], "html": [r"elementor-(?:widget|section|kit)"],
                  "scriptSrc": [r"elementor/assets/js/frontend(?:\.min)?\.js\?ver=([\d.]+)\;version:\1"]},
    "Divi": {"cats": [CAT_PAGE_BUILDER], "html": [r"et_pb_|Divi"]},
    "WPBakery": {"cats": [CAT_PAGE_BUILDER], "html": [r"vc_row|js_composer"]},
    # JS libraries / old tech
    "jQuery": {"cats": [CAT_JS_LIB], "scriptSrc": [r"jquery[.-]([\d]+\.[\d]+(?:\.[\d]+)?)(?:\.min)?\.js\;version:\1",
                                                   r"/([\d.]+)/jquery(?:\.min)?\.js\;version:\1",
                                                   r"jquery(?:\.min)?\.js\?ver=([\d.]+)\;version:\1",
                                                   r"jquery"],
               "js": {"jQuery.fn.jquery": r"([\d.]+)\;version:\1"}},
    "jQuery UI": {"cats": [CAT_JS_LIB], "scriptSrc": [r"jquery-ui[.-]?([\d.]*)\;version:\1"]},
    "Prototype": {"cats": [CAT_JS_LIB], "scriptSrc": [r"prototype(?:\.min)?\.js"]},
    "MooTools": {"cats": [CAT_JS_LIB], "scriptSrc": [r"mootools"]},
    "Bootstrap": {"cats": [66], "html": [r"bootstrap(?:\.min)?\.css"], "scriptSrc": [r"bootstrap[.-]?([\d.]*)\;version:\1"]},
    "Adobe Flash": {"cats": [19], "html": [r"\.swf[\"'?]|application/x-shockwave-flash|macromedia\.com/go/getflashplayer"]},
    "Microsoft Silverlight": {"cats": [19], "html": [r"application/x-silverlight"]},
    "React": {"cats": [12], "html": [r"data-reactroot|__NEXT_DATA__"]},
    "Next.js": {"cats": [18], "html": [r"__NEXT_DATA__|/_next/static/"]},
    # widgets that matter for conversion
    "Calendly": {"cats": [CAT_BOOKING], "html": [r"calendly\.com"]},
    "Housecall Pro": {"cats": [CAT_BOOKING], "html": [r"housecallpro\.com"]},
    "Jobber": {"cats": [CAT_BOOKING], "html": [r"getjobber\.com|clienthub\.getjobber"]},
    "ServiceTitan": {"cats": [CAT_BOOKING], "html": [r"servicetitan\.com|scheduler\.servicetitan"]},
    "Square Appointments": {"cats": [CAT_BOOKING], "html": [r"squareup\.com/appointments"]},
    "Acuity Scheduling": {"cats": [CAT_BOOKING], "html": [r"acuityscheduling\.com"]},
    "Podium": {"cats": [CAT_REVIEWS, CAT_LIVE_CHAT], "html": [r"podium\.com|podium-website-widget"]},
    "Birdeye": {"cats": [CAT_REVIEWS], "html": [r"birdeye\.com"]},
    "Elfsight": {"cats": [CAT_WIDGETS], "html": [r"elfsight\.com|elfsightcdn"]},
    "Trustindex": {"cats": [CAT_REVIEWS], "html": [r"trustindex\.io"]},
    "NiceJob": {"cats": [CAT_REVIEWS], "html": [r"nicejob\.(?:com|co)"]},
    "Tawk.to": {"cats": [CAT_LIVE_CHAT], "html": [r"embed\.tawk\.to"]},
    "Intercom": {"cats": [CAT_LIVE_CHAT], "html": [r"widget\.intercom\.io"]},
    "Drift": {"cats": [CAT_LIVE_CHAT], "html": [r"js\.driftt\.com"]},
    "Gravity Forms": {"cats": [CAT_FORMS], "html": [r"gform_wrapper|gravityforms"]},
    "Contact Form 7": {"cats": [CAT_FORMS], "html": [r"wpcf7"]},
    "JotForm": {"cats": [CAT_FORMS], "html": [r"jotform\.com"]},
    # analytics / ads / tracking (Phase 5 reads these too)
    "Google Analytics (UA)": {"cats": [CAT_ANALYTICS], "html": [r"google-analytics\.com/(?:ga|analytics)\.js|UA-\d{4,10}-\d+"]},
    "Google Analytics 4": {"cats": [CAT_ANALYTICS], "html": [r"gtag\(['\"]config['\"],\s*['\"]G-[A-Z0-9]+"]},
    "Google Tag Manager": {"cats": [CAT_TAG_MANAGER], "html": [r"googletagmanager\.com/gtm\.js|GTM-[A-Z0-9]{4,}"]},
    "Google Ads Conversion": {"cats": [CAT_ADS], "html": [r"AW-\d{6,}|googleadservices\.com/pagead/conversion|google_conversion_id"]},
    "Google Ads Remarketing": {"cats": [CAT_RETARGETING], "html": [r"googleads\.g\.doubleclick\.net/pagead/viewthroughconversion|google_remarketing_only"]},
    "Meta Pixel": {"cats": [CAT_RETARGETING], "html": [r"connect\.facebook\.net/[\w_]+/fbevents\.js|fbq\(['\"]init"]},
    "Microsoft Advertising": {"cats": [CAT_ADS], "html": [r"bat\.bing\.com/bat\.js"]},
    "CallRail": {"cats": [CAT_CALL_TRACKING], "html": [r"cdn\.callrail\.com|callrail\.com/companies"]},
    "CallTrackingMetrics": {"cats": [CAT_CALL_TRACKING], "html": [r"tctm\.co|calltrackingmetrics"]},
    "WhatConverts": {"cats": [CAT_CALL_TRACKING], "html": [r"whatconverts\.com"]},
    "Invoca": {"cats": [CAT_CALL_TRACKING], "html": [r"invoca\.net|invocacdn"]},
    "Google reCAPTCHA": {"cats": [16], "html": [r"google\.com/recaptcha"]},
    # parking
    "Domain parking": {"cats": [CAT_PARKING], "html": [r"sedoparking|parkingcrew|bodis\.com|domain is for sale|"
                                                        r"this domain may be for sale|buy this domain|parked free"]},
}


@dataclass
class Tech:
    name: str
    version: str | None
    categories: list[int]
    confidence: int = 100
    evidence: list[str] = field(default_factory=list)


@dataclass
class _Pattern:
    regex: re.Pattern
    version: str | None
    confidence: int


def _parse_pattern(raw: str) -> _Pattern:
    parts = raw.split("\\;")
    version, confidence = None, 100
    for extra in parts[1:]:
        if extra.startswith("version:"):
            version = extra[len("version:"):]
        elif extra.startswith("confidence:"):
            try:
                confidence = int(extra[len("confidence:"):])
            except ValueError:
                pass
    try:
        regex = re.compile(parts[0], re.I)
    except re.error:
        regex = re.compile(re.escape(parts[0]), re.I)
    return _Pattern(regex, version, confidence)


def _as_list(value) -> list[str]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _version(pattern: _Pattern, match: re.Match) -> str | None:
    if not pattern.version:
        return None
    out = pattern.version
    for i, group in enumerate(match.groups(), 1):
        out = out.replace(f"\\{i}", group or "")
    out = re.sub(r"\\\d", "", out).strip()
    return out or None


class Fingerprints:
    def __init__(self, techs: dict[str, dict[str, Any]]) -> None:
        self.raw = techs
        self.compiled: dict[str, dict[str, Any]] = {}
        for name, spec in techs.items():
            self.compiled[name] = {
                "cats": spec.get("cats", []),
                "implies": [i.split("\\;")[0] for i in _as_list(spec.get("implies"))],
                "html": [_parse_pattern(p) for p in _as_list(spec.get("html"))],
                "scriptSrc": [_parse_pattern(p) for p in _as_list(spec.get("scriptSrc"))],
                "scripts": [_parse_pattern(p) for p in _as_list(spec.get("scripts"))],
                "meta": {k.lower(): [_parse_pattern(p) for p in _as_list(v)] for k, v in (spec.get("meta") or {}).items()},
                "headers": {k.lower(): [_parse_pattern(p) for p in _as_list(v)] for k, v in (spec.get("headers") or {}).items()},
                "cookies": {k.lower(): [_parse_pattern(p) for p in _as_list(v)] for k, v in (spec.get("cookies") or {}).items()},
                "js": {k: [_parse_pattern(p) for p in _as_list(v)] for k, v in (spec.get("js") or {}).items()},
            }

    @property
    def js_properties(self) -> list[str]:
        return sorted({k for spec in self.compiled.values() for k in spec["js"]})

    def detect(self, *, html: str = "", script_srcs: list[str] | None = None, inline_scripts: str = "",
               meta: dict[str, str] | None = None, headers: dict[str, str] | None = None,
               cookies: dict[str, str] | None = None, js: dict[str, str] | None = None) -> list[Tech]:
        script_srcs = script_srcs or []
        meta = {k.lower(): v for k, v in (meta or {}).items()}
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        cookies = {k.lower(): v for k, v in (cookies or {}).items()}
        js = js or {}
        html = html[:600_000]
        found: dict[str, Tech] = {}

        def hit(name: str, pattern: _Pattern, text: str, where: str) -> None:
            m = pattern.regex.search(text)
            if not m:
                return
            spec = self.compiled[name]
            tech = found.setdefault(name, Tech(name, None, spec["cats"], 0))
            tech.confidence = min(100, tech.confidence + pattern.confidence)
            tech.version = tech.version or _version(pattern, m)
            tech.evidence.append(where)

        for name, spec in self.compiled.items():
            for p in spec["html"]:
                hit(name, p, html, "html")
            for p in spec["scripts"]:
                hit(name, p, inline_scripts, "script")
            for src in script_srcs:
                for p in spec["scriptSrc"]:
                    hit(name, p, src, "scriptSrc")
            for key, pats in spec["meta"].items():
                if key in meta:
                    for p in pats:
                        hit(name, p, meta[key], f"meta:{key}")
            for key, pats in spec["headers"].items():
                if key in headers:
                    for p in pats:
                        hit(name, p, headers[key], f"header:{key}")
            for key, pats in spec["cookies"].items():
                if key in cookies:
                    for p in pats:
                        hit(name, p, cookies[key], f"cookie:{key}")
            for key, pats in spec["js"].items():
                if key in js and js[key] is not None:
                    for p in pats:
                        hit(name, p, str(js[key]), f"js:{key}")
        for tech in list(found.values()):  # implied technologies
            for implied in self.compiled[tech.name]["implies"]:
                if implied in self.compiled and implied not in found:
                    found[implied] = Tech(implied, None, self.compiled[implied]["cats"], 50, [f"implied by {tech.name}"])
        return sorted(found.values(), key=lambda t: t.name.lower())


@lru_cache(maxsize=1)
def load_fingerprints() -> Fingerprints:
    techs: dict[str, dict[str, Any]] = {}
    if FINGERPRINT_DIR.exists():
        for f in sorted(FINGERPRINT_DIR.glob("*.json")):
            if f.name == "categories.json":
                continue
            try:
                techs.update(json.loads(f.read_text(encoding="utf-8")))
            except ValueError:
                continue
    techs.update(BUILTIN)  # our rules win where names overlap
    return Fingerprints(techs)


async def download_webappanalyzer(transport: httpx.AsyncBaseTransport | None = None) -> int:
    """Fetch the webappanalyzer fingerprint files (GPL-3.0) into data/webappanalyzer/."""
    FINGERPRINT_DIR.mkdir(parents=True, exist_ok=True)
    names = ["_"] + [chr(c) for c in range(ord("a"), ord("z") + 1)]
    count = 0
    async with httpx.AsyncClient(timeout=60, transport=transport) as client:
        for n in names:
            r = await client.get(f"{WEBAPPANALYZER_BASE}/technologies/{n}.json")
            if r.status_code == 200:
                (FINGERPRINT_DIR / f"{n}.json").write_text(r.text, encoding="utf-8")
                count += len(r.json())
        r = await client.get(f"{WEBAPPANALYZER_BASE}/categories.json")
        if r.status_code == 200:
            (FINGERPRINT_DIR / "categories.json").write_text(r.text, encoding="utf-8")
    (FINGERPRINT_DIR / "LICENSE-NOTE.txt").write_text(
        "Fingerprints from https://github.com/enthec/webappanalyzer (GPL-3.0). Downloaded for local use.\n",
        encoding="utf-8")
    load_fingerprints.cache_clear()
    return count
