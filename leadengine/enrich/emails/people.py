"""Find owner / manager names (for personalised outreach and email pattern guesses)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

TITLES = (
    "owner", "co-owner", "founder", "co-founder", "president", "ceo", "general manager", "manager",
    "operations manager", "office manager", "principal", "proprietor", "partner", "director",
)
_TITLE_RE = "|".join(re.escape(t) for t in sorted(TITLES, key=len, reverse=True))
_WORD = r"[A-Z][a-z]*(?:['’-][A-Z][a-z]+)?"
_NAME = rf"({_WORD}(?:\s+[A-Z]\.)?\s+{_WORD})"
# Only the keyword parts are case-insensitive; names must be Capitalised.
_T = rf"(?i:{_TITLE_RE})"
# (pattern, fixed title or None = read the title from nearby text)
_PATTERNS = [
    (re.compile(rf"\b{_T}\s*[:\-–—,]\s*{_NAME}"), None),                          # Owner: John Smith
    (re.compile(rf"{_NAME}\s*[,\-–—|(]\s*(?i:the\s+)?{_T}\b"), None),            # John Smith, Owner
    (re.compile(rf"\b(?i:founded|started)\s+(?i:in\s+\d{{4}}\s+)?(?i:by)\s+{_NAME}"), "founder"),
    (re.compile(rf"\b(?i:owned|run|operated)\s+(?i:by)\s+{_NAME}"), "owner"),
    (re.compile(rf"\b(?i:meet)\s+(?:(?i:owner)\s+)?{_NAME}"), None),
    (re.compile(rf"\b(?i:my\s+name\s+is)\s+{_NAME}"), None),
]
_NOT_NAMES = {
    "contact us", "about us", "our team", "free estimate", "get started", "learn more", "read more",
    "privacy policy", "terms service", "united states", "call now", "home page", "service area",
    "general manager", "office manager", "best price", "dumpster rental", "lawn care",
}


@dataclass
class Person:
    first: str
    last: str
    title: str | None
    source: str        # jsonld | about-text
    url: str = ""

    @property
    def full(self) -> str:
        return f"{self.first} {self.last}"


def _clean_name(raw: str) -> tuple[str, str] | None:
    parts = [p for p in re.split(r"\s+", raw.strip()) if not re.fullmatch(r"[A-Z]\.", p)]
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]
    if f"{first} {last}".lower() in _NOT_NAMES or len(first) < 2 or len(last) < 2:
        return None
    if not (first[0].isupper() and last[0].isupper()):
        return None
    return first, last


def _title_near(text: str) -> str | None:
    m = re.search(rf"\b({_TITLE_RE})\b", text, re.I)
    return m.group(1).lower() if m else None


def people_from_html(html: str, url: str = "") -> list[Person]:
    soup = BeautifulSoup(html, "html.parser")
    found: list[Person] = []

    def add(raw: str, title: str | None, source: str) -> None:
        name = _clean_name(raw)
        if name and not any(p.first == name[0] and p.last == name[1] for p in found):
            found.append(Person(name[0], name[1], title, source, url))

    def walk(node) -> None:
        if isinstance(node, dict):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "Person" in types and isinstance(node.get("name"), str):
                add(node["name"], (node.get("jobTitle") or "").lower() or None, "jsonld")
            for key in ("founder", "founders", "employee", "employees", "owner", "member", "author"):
                if key in node:
                    value = node[key]
                    for item in value if isinstance(value, list) else [value]:
                        if isinstance(item, dict) and isinstance(item.get("name"), str):
                            add(item["name"], (item.get("jobTitle") or key).lower(), "jsonld")
                        elif isinstance(item, str):
                            add(item, key, "jsonld")
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            walk(json.loads(tag.string or tag.get_text() or "{}"))
        except (ValueError, TypeError):
            continue

    for s in soup(["script", "style", "noscript"]):
        s.decompose()
    text = " ".join(soup.get_text(" ").split())
    for pattern, fixed_title in _PATTERNS:
        for m in pattern.finditer(text):
            add(m.group(1), fixed_title or _title_near(m.group(0)) or _title_near(text[m.end(): m.end() + 30]),
                "about-text")
    return found


def rank_people(people: list[Person]) -> list[Person]:
    """Owners first, then other titled people, schema data before free text."""
    def score(p: Person) -> tuple[int, int]:
        t = p.title or ""
        title_rank = 0 if any(k in t for k in ("owner", "founder", "president", "ceo", "proprietor")) else (
            1 if t else 2)
        return title_rank, 0 if p.source == "jsonld" else 1
    return sorted(people, key=score)
