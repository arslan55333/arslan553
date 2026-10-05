"""Signals read straight from a page's HTML (no network)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup

_COPYRIGHT_RE = re.compile(
    r"(?:©|&copy;|\(c\)|copyright)\s*(?:[^0-9]{0,30}?)((?:19|20)\d{2})(?:\s*[-–—]\s*((?:19|20)\d{2}))?", re.I)
_CTA_RE = re.compile(r"\b(free (?:estimate|quote|inspection|consultation)|get (?:a |your )?(?:free )?(?:quote|estimate)|"
                     r"request (?:a |your )?(?:quote|estimate|service|appointment)|call (?:us )?(?:now|today)|"
                     r"book (?:now|online|an? appointment)|schedule (?:now|online|service|an? appointment))\b", re.I)
_REVIEW_WORDS = re.compile(r"\b(testimonials?|reviews?|what our (?:customers|clients) say)\b", re.I)
_PARKED_RE = re.compile(r"domain (?:name )?(?:is|may be) for sale|buy this domain|this domain is parked|"
                        r"parked (?:free|courtesy)|domain parking|future home of|under construction|"
                        r"coming soon|website (?:is )?(?:coming soon|under construction)", re.I)
_DEFAULT_PAGE_RE = re.compile(r"apache2 (?:ubuntu|debian) default page|welcome to nginx|it works!|"
                              r"index of /|default web site page", re.I)


@dataclass
class HtmlSignals:
    title: str | None = None
    meta_description: bool = False
    viewport: bool = False
    viewport_content: str | None = None
    html5_doctype: bool = False
    copyright_year: int | None = None
    tel_links: int = 0
    phone_in_text: bool = False
    forms: int = 0
    quote_form: bool = False
    cta_phrases: list[str] = field(default_factory=list)
    reviews_section: bool = False
    table_layout: bool = False
    frames: bool = False
    flash: bool = False
    deprecated_tags: list[str] = field(default_factory=list)
    inline_styles: int = 0
    word_count: int = 0
    images: int = 0
    images_without_alt: int = 0
    parked: bool = False
    default_page: bool = False
    script_srcs: list[str] = field(default_factory=list)
    inline_scripts: str = ""
    meta: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data.pop("inline_scripts")
        data["script_srcs"] = self.script_srcs[:30]
        return data


def latest_copyright_year(text: str, current_year: int) -> int | None:
    years = []
    for m in _COPYRIGHT_RE.finditer(text):
        for g in m.groups():
            if g and 1995 <= int(g) <= current_year + 1:
                years.append(int(g))
    return max(years) if years else None


def analyze_html(html: str, current_year: int) -> HtmlSignals:
    soup = BeautifulSoup(html, "html.parser")
    s = HtmlSignals()
    s.html5_doctype = bool(re.match(r"\s*<!doctype html\s*>", html[:300], re.I))
    if soup.title and soup.title.string:
        s.title = soup.title.string.strip()[:200]
    for m in soup.find_all("meta"):
        name = (m.get("name") or m.get("property") or m.get("http-equiv") or "").lower()
        if name:
            s.meta[name] = (m.get("content") or "")[:300]
    s.meta_description = bool(s.meta.get("description"))
    if "viewport" in s.meta:
        s.viewport = "width=device-width" in s.meta["viewport"].replace(" ", "").lower()
        s.viewport_content = s.meta["viewport"]

    s.script_srcs = [t["src"] for t in soup.find_all("script", src=True)]
    s.inline_scripts = "\n".join(t.get_text() for t in soup.find_all("script", src=False))[:300_000]
    s.tel_links = sum(1 for a in soup.find_all("a", href=True) if a["href"].lower().startswith("tel:"))
    for form in soup.find_all("form"):
        inputs = form.find_all(["input", "textarea", "select"])
        visible = [i for i in inputs if (i.get("type") or "text").lower() not in ("hidden", "submit", "button", "search")]
        if len(visible) >= 2 or form.find("textarea"):
            s.forms += 1
            fields = " ".join((i.get("name") or "") + " " + (i.get("placeholder") or "") for i in visible).lower()
            if any(k in fields for k in ("phone", "email", "message", "service", "address", "name")):
                s.quote_form = True
    tables = soup.find_all("table")
    layout_tables = [t for t in tables if t.find("table") or t.get("width") or t.get("bgcolor")
                     or (t.find("td") and t.find("td").get("valign"))]
    s.table_layout = len(layout_tables) >= 1 and len(soup.find_all("div")) < 15
    s.frames = bool(soup.find(["frameset", "frame"]))
    s.flash = bool(re.search(r"\.swf\b|application/x-shockwave-flash", html, re.I))
    s.deprecated_tags = sorted({t.name for t in soup.find_all(["font", "center", "marquee", "blink", "bgsound"])})
    s.inline_styles = len(soup.find_all(style=True))
    imgs = soup.find_all("img")
    s.images = len(imgs)
    s.images_without_alt = sum(1 for i in imgs if not (i.get("alt") or "").strip())

    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    text = " ".join(soup.get_text(" ").split())
    s.word_count = len(text.split())
    s.copyright_year = latest_copyright_year(text, current_year)
    s.phone_in_text = bool(re.search(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b", text))
    s.cta_phrases = sorted({m.group(1).lower() for m in _CTA_RE.finditer(text)})[:10]
    s.reviews_section = bool(_REVIEW_WORDS.search(text))
    s.parked = bool(_PARKED_RE.search(text)) and s.word_count < 400
    s.default_page = bool(_DEFAULT_PAGE_RE.search(text)) and s.word_count < 300
    return s


def version_tuple(version: str | None) -> tuple[int, ...]:
    if not version:
        return ()
    parts = []
    for p in version.split("."):
        m = re.match(r"\d+", p)
        if not m:
            break
        parts.append(int(m.group()))
    return tuple(parts)


# (technology, version below which it counts as outdated, why)
OUTDATED_VERSIONS = [
    ("jQuery", (1, 12), "jQuery {v} (released before 2016)"),
    ("WordPress", (5, 0), "WordPress {v} (before 2018)"),
    ("Joomla", (3, 0), "Joomla {v}"),
    ("Drupal", (8, 0), "Drupal {v}"),
    ("Bootstrap", (3, 0), "Bootstrap {v}"),
]
OUTDATED_TECH = {
    "Adobe Flash": "uses Flash (dead since 2020)",
    "Microsoft Silverlight": "uses Silverlight",
    "Microsoft FrontPage": "built with FrontPage (discontinued 2006)",
    "Apple iWeb": "built with iWeb (discontinued 2011)",
    "Adobe Dreamweaver": "Dreamweaver template code",
    "Homestead": "Homestead site builder",
    "Yahoo SiteBuilder": "Yahoo SiteBuilder",
    "Prototype": "Prototype.js (2000s library)",
    "MooTools": "MooTools (2000s library)",
    "Web.com Builder": "Web.com template builder",
}
