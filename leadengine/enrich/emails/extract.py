"""Find email candidates in one HTML page. Pure functions — no network.

Strategies (ported from LeadHunter v3 and extended):
mailto links, visible text, Cloudflare ``cfemail``, schema.org JSON-LD,
``data-*`` attributes, JS/JSON strings, HTML comments, entity / URL encoding,
and human obfuscations such as ``name [at] domain [dot] com``.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass
from urllib.parse import unquote

from bs4 import BeautifulSoup

EMAIL_RE = re.compile(
    r"(?<![\w.+-])([a-z0-9][a-z0-9._%+-]{0,63}@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})(?![\w-])",
    re.I,
)
_CF_HREF_RE = re.compile(r"/cdn-cgi/l/email-protection#([0-9a-f]+)", re.I)
_CF_ATTR_RE = re.compile(r"data-cfemail=[\"']([0-9a-f]+)[\"']", re.I)
# "[at]" style markers are unambiguous; a plain " at " is only trusted together with a word "dot"
# (otherwise "find us at facebook.com" would become us@facebook.com).
_AT_MARK = r"\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|<\s*at\s*>|_at_|＠|\s@\s|\(@\)|\[@\])\s*"
_DOT_ANY = r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|<\s*dot\s*>|\s+dot\s+|_dot_|\.)\s*"
_DOT_WORD = r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|<\s*dot\s*>|\s+dot\s+|_dot_)\s*"
_LOCAL = r"\b([a-z0-9][a-z0-9._+-]{0,40})"
_OBFUSCATED_RES = [
    re.compile(_LOCAL + _AT_MARK + r"([a-z0-9-]{1,63}(?:" + _DOT_ANY + r"[a-z0-9-]{1,63})*)" + _DOT_ANY + r"([a-z]{2,10})\b", re.I),
    re.compile(_LOCAL + r"\s+at\s+" + r"([a-z0-9-]{1,63}(?:" + _DOT_WORD + r"[a-z0-9-]{1,63})*)" + _DOT_WORD + r"([a-z]{2,10})\b", re.I),
]
_JS_STRING_CONCAT = re.compile(r"""['"]([a-z0-9._%+-]{1,64})['"]\s*\+\s*['"]@['"]\s*\+\s*['"]([a-z0-9.-]+\.[a-z]{2,24})['"]""", re.I)


@dataclass
class Candidate:
    email: str
    method: str          # mailto | text | cfemail | jsonld | attr | script | comment | obfuscated
    url: str = ""
    context: str = ""    # a little surrounding text, for debugging and name matching


def decode_cfemail(encoded: str) -> str | None:
    """Cloudflare email protection: first byte is the XOR key for the rest."""
    try:
        data = bytes.fromhex(encoded)
    except ValueError:
        return None
    if len(data) < 4:
        return None
    key = data[0]
    try:
        text = bytes(b ^ key for b in data[1:]).decode("utf-8")
    except UnicodeDecodeError:
        return None
    m = EMAIL_RE.fullmatch(text.strip())
    return m.group(1).lower() if m else None


def _norm(email: str) -> str:
    return unquote(email).strip().strip(".,;:'\"()<>[]").lower()


def _context(text: str, start: int, end: int, width: int = 60) -> str:
    return " ".join(text[max(0, start - width): end + width].split())


def _jsonld_emails(soup: BeautifulSoup) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    def walk(node, owner: str = "") -> None:
        if isinstance(node, dict):
            name = node.get("name") if isinstance(node.get("name"), str) else owner
            for key, value in node.items():
                if key.lower() in ("email", "contactemail") and isinstance(value, str):
                    out.append((value.replace("mailto:", ""), name or ""))
                else:
                    walk(value, name or owner)
        elif isinstance(node, list):
            for item in node:
                walk(item, owner)

    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            walk(json.loads(tag.string or tag.get_text() or "{}"))
        except (ValueError, TypeError):
            continue
    return out


def extract_candidates(html: str, url: str = "") -> list[Candidate]:
    """All email-looking strings in a page, with how they were found. Not filtered yet."""
    found: list[Candidate] = []
    soup = BeautifulSoup(html, "html.parser")

    for m in _CF_HREF_RE.finditer(html):
        if email := decode_cfemail(m.group(1)):
            found.append(Candidate(email, "cfemail", url))
    for m in _CF_ATTR_RE.finditer(html):
        if email := decode_cfemail(m.group(1)):
            found.append(Candidate(email, "cfemail", url))

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            target = unquote(href[7:].split("?", 1)[0])
            for part in target.split(","):
                if m := EMAIL_RE.search(html_lib.unescape(part)):
                    found.append(Candidate(_norm(m.group(1)), "mailto", url, a.get_text(" ", strip=True)[:80]))

    for email, owner in _jsonld_emails(soup):
        if m := EMAIL_RE.search(email):
            found.append(Candidate(_norm(m.group(1)), "jsonld", url, owner))

    for tag in soup.find_all(True):
        for attr, value in tag.attrs.items():
            if attr.startswith("data-") and isinstance(value, str) and "@" in value:
                for m in EMAIL_RE.finditer(html_lib.unescape(value)):
                    found.append(Candidate(_norm(m.group(1)), "attr", url))

    scripts = " ".join(s.get_text() for s in soup.find_all("script") if not (s.get("type") or "").endswith("ld+json"))
    for m in _JS_STRING_CONCAT.finditer(scripts):
        found.append(Candidate(f"{m.group(1)}@{m.group(2)}".lower(), "script", url))
    for m in EMAIL_RE.finditer(scripts):
        found.append(Candidate(_norm(m.group(1)), "script", url))

    for comment in re.findall(r"<!--(.*?)-->", html, re.S):
        for m in EMAIL_RE.finditer(comment):
            found.append(Candidate(_norm(m.group(1)), "comment", url))

    for s in soup(["script", "style", "noscript", "template"]):
        s.decompose()
    text = html_lib.unescape(soup.get_text(" "))
    text = text.replace("​", "").replace("­", "")
    for m in EMAIL_RE.finditer(text):
        found.append(Candidate(_norm(m.group(1)), "text", url, _context(text, m.start(), m.end())))
    for regex in _OBFUSCATED_RES:
        for m in regex.finditer(text):
            domain = re.sub(_DOT_ANY, ".", m.group(2), flags=re.I)
            email = f"{m.group(1)}@{domain}.{m.group(3)}".lower()
            if EMAIL_RE.fullmatch(email):
                found.append(Candidate(email, "obfuscated", url, _context(text, m.start(), m.end())))
    return found


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for s in soup(["script", "style", "noscript", "template"]):
        s.decompose()
    return " ".join(soup.get_text(" ").split())
