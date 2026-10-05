"""Email pattern guesses from people's names. Always marked ``is_guess=True``."""

from __future__ import annotations

import re
import unicodedata

from leadengine.enrich.emails.filters import local_of

PATTERNS = {
    "first": "{f}",
    "first.last": "{f}.{l}",
    "firstlast": "{f}{l}",
    "flast": "{fi}{l}",
    "f.last": "{fi}.{l}",
    "first_last": "{f}_{l}",
    "firstl": "{f}{li}",
    "last": "{l}",
}
GENERIC_GUESSES = ("info", "contact", "office")


def _ascii(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", name.lower())


def render(pattern: str, first: str, last: str) -> str:
    f, l = _ascii(first), _ascii(last)
    return PATTERNS[pattern].format(f=f, l=l, fi=f[:1], li=l[:1])


def detect_pattern(known_emails: list[str], people: list[tuple[str, str]]) -> str | None:
    """If a found email matches a known person (e.g. john.smith@), return that pattern name."""
    locals_ = {local_of(e) for e in known_emails}
    for first, last in people:
        for name in PATTERNS:
            if name in ("first", "last"):
                continue  # too ambiguous to learn from
            if render(name, first, last) in locals_:
                return name
    return None


def guess_emails(domain: str, people: list[tuple[str, str]], known_emails: list[str],
                 max_people: int = 2) -> list[tuple[str, str]]:
    """``[(email, why)]``. With a learned pattern only that pattern is used; otherwise the
    most common small-business patterns. Generic inboxes are guessed when nothing was found."""
    known = {e.lower() for e in known_emails}
    learned = detect_pattern(known_emails, people)
    out: list[tuple[str, str]] = []
    for first, last in people[:max_people]:
        names = [learned] if learned else ["first", "first.last", "flast"]
        for pattern in names:
            local = render(pattern, first, last)
            email = f"{local}@{domain}"
            if len(local) >= 2 and email not in known and all(email != e for e, _ in out):
                why = f"{pattern}@ pattern for {first} {last}" + (" (pattern seen on site)" if learned else "")
                out.append((email, why))
    if not any(e.endswith("@" + domain) for e in known):
        for g in GENERIC_GUESSES:
            email = f"{g}@{domain}"
            if email not in known:
                out.append((email, "common inbox"))
    return out
