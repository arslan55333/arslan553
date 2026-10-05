"""Pure parsing helpers for Google Maps pages (no browser code here, so it is unit-testable).

Two sources are combined:

1. **Network/initial-state JSON** — Maps ships results as XSSI-protected JSON
   (``)]}'`` prefix) inside ``APP_INITIALIZATION_STATE`` and in ``/search?tbm=map``
   XHR responses. Each business is a long positional array; field positions below
   follow widely used open-source scrapers and are validated by type before use,
   so a layout change degrades to "field missing" instead of wrong data.
2. **DOM** — card/place-page text, used as a fallback and for things the JSON
   does not expose reliably (Sponsored label, claim link, review dates).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Iterator
from urllib.parse import parse_qs, unquote, urlparse

XSSI_PREFIX = ")]}'"
DATA_ID_RE = re.compile(r"^0x[0-9a-f]+:0x[0-9a-f]+$", re.I)
PLACE_ID_RE = re.compile(r"^ChIJ[\w-]{10,}$")
PHONE_RE = re.compile(r"(?:\+1[\s.-]?)?\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
_URL_PLACE_ID_RE = re.compile(r"!19s(ChIJ[\w-]+)")
_URL_DATA_ID_RE = re.compile(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", re.I)
_URL_LATLNG_RE = re.compile(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)")
_RATING_LABEL_RE = re.compile(r"(\d(?:[.,]\d)?)\s*stars?", re.I)
_REVIEWS_LABEL_RE = re.compile(r"([\d,.]+)\s*reviews?", re.I)
_CARD_RATING_RE = re.compile(r"^(\d[.,]\d)\s*\(([\d,.]+[KkMm]?)\)")
_PHOTOS_RE = re.compile(r"([\d,]+)\s+photos?\b", re.I)
_REL_DATE_RE = re.compile(r"(a|an|one|\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", re.I)
_UNIT_DAYS = {"second": 1 / 86400, "minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30.44, "year": 365.25}


# ── JSON helpers ─────────────────────────────────────────────────────
def strip_xssi(text: str) -> str:
    text = text.strip()
    if text.startswith(XSSI_PREFIX):
        text = text[len(XSSI_PREFIX):]
    if text.endswith('/*""*/'):
        text = text[: -len('/*""*/')]
    return text.strip()


def loads_maps_json(text: str) -> Any | None:
    try:
        return json.loads(strip_xssi(text))
    except (ValueError, TypeError):
        return None


def get_path(node: Any, *path: int) -> Any:
    """``node[a][b][c]`` or ``None`` if any step is missing / not a list."""
    for idx in path:
        if not isinstance(node, list) or idx >= len(node) or idx < -len(node):
            return None
        node = node[idx]
    return node


def _is_business_array(node: Any) -> bool:
    return (
        isinstance(node, list)
        and len(node) > 11
        and isinstance(node[11], str)
        and bool(node[11].strip())
        and isinstance(node[10], str)
        and bool(DATA_ID_RE.match(node[10]))
    )


def iter_business_arrays(node: Any, _depth: int = 0) -> Iterator[list]:
    """Yield every business-shaped array found anywhere in ``node``.

    Strings that are themselves XSSI JSON (as inside APP_INITIALIZATION_STATE)
    are decoded and searched too.
    """
    if _depth > 14:
        return
    if isinstance(node, str):
        if node.lstrip().startswith(XSSI_PREFIX):
            inner = loads_maps_json(node)
            if inner is not None:
                yield from iter_business_arrays(inner, _depth + 1)
        return
    if not isinstance(node, list):
        return
    if _is_business_array(node):
        yield node
        return
    for child in node:
        if isinstance(child, (list, str)):
            yield from iter_business_arrays(child, _depth + 1)


def _find_string(node: Any, pattern: re.Pattern, depth: int = 0) -> str | None:
    if depth > 4:
        return None
    if isinstance(node, str):
        return node if pattern.match(node) else None
    if isinstance(node, list):
        for child in node:
            hit = _find_string(child, pattern, depth + 1)
            if hit:
                return hit
    return None


def _str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _float(value: Any, lo: float | None = None, hi: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        return None
    return float(value)


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def unwrap_google_redirect(url: str | None) -> str | None:
    """``https://www.google.com/url?q=https://site.com&...`` -> ``https://site.com``."""
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.path == "/url" and "google." in parsed.netloc:
        return parse_qs(parsed.query).get("q", [url])[0]
    return url


def _parse_hours(raw: Any) -> dict[str, str] | None:
    """``[["Monday", ["8 AM–5 PM"]], ...]`` -> ``{"Monday": "8 AM–5 PM"}``."""
    if not isinstance(raw, list):
        return None
    out: dict[str, str] = {}
    for day in raw:
        name = _str(get_path(day, 0))
        slots = get_path(day, 1)
        if name and isinstance(slots, list):
            out[name] = ", ".join(s for s in slots if isinstance(s, str)) or "Closed"
    return out or None


def parse_business_array(d: list) -> dict[str, Any]:
    """Map a Maps business array to plain fields. Anything not matching its expected type is dropped."""
    name = d[11].strip()
    place_id = _str(get_path(d, 78))
    if not (place_id and PLACE_ID_RE.match(place_id)):
        place_id = _find_string(d, PLACE_ID_RE)

    address = _str(get_path(d, 18))
    if address and address.startswith(name + ","):
        address = address[len(name) + 1:].strip()

    phone = _str(get_path(d, 178, 0, 0))
    if phone and not PHONE_RE.search(phone):
        phone = None

    website = _str(get_path(d, 7, 0))
    website = unwrap_google_redirect(website) if website and website.startswith("http") else None

    categories = [c for c in (get_path(d, 13) or []) if isinstance(c, str)]
    return {
        "name": name,
        "data_id": d[10],
        "place_id": place_id,
        "rating": _float(get_path(d, 4, 7), 0, 5),
        "review_count": _int(get_path(d, 4, 8)),
        "website": website,
        "phone": phone,
        "address": address,
        "lat": _float(get_path(d, 9, 2), -90, 90),
        "lng": _float(get_path(d, 9, 3), -180, 180),
        "categories": categories,
        "hours": _parse_hours(get_path(d, 34, 1)),
        "timezone": _str(get_path(d, 30)),
    }


def businesses_from_payload(payload: Any) -> list[dict[str, Any]]:
    """All businesses found in a decoded payload (deduped by data_id, first wins)."""
    seen: dict[str, dict[str, Any]] = {}
    for arr in iter_business_arrays(payload):
        parsed = parse_business_array(arr)
        seen.setdefault(parsed["data_id"].lower(), parsed)
    return list(seen.values())


# ── URL / DOM helpers ────────────────────────────────────────────────
def parse_place_url(url: str | None) -> dict[str, Any]:
    """place_id, data_id and coordinates from a Google Maps place URL."""
    out: dict[str, Any] = {"place_id": None, "data_id": None, "lat": None, "lng": None}
    if not url:
        return out
    text = unquote(url)
    if m := _URL_PLACE_ID_RE.search(text):
        out["place_id"] = m.group(1)
    if m := _URL_DATA_ID_RE.search(text):
        out["data_id"] = m.group(1).lower()
    if m := _URL_LATLNG_RE.search(text):
        out["lat"], out["lng"] = float(m.group(1)), float(m.group(2))
    return out


def _count(text: str) -> int | None:
    text = text.replace(",", "").strip()
    mult = 1
    if text[-1:].lower() == "k":
        mult, text = 1000, text[:-1]
    elif text[-1:].lower() == "m":
        mult, text = 1_000_000, text[:-1]
    try:
        return int(float(text) * mult)
    except ValueError:
        return None


def parse_rating_label(label: str | None) -> tuple[float | None, int | None]:
    """``"4.6 stars 1,234 Reviews"`` -> ``(4.6, 1234)``."""
    if not label:
        return None, None
    rating = reviews = None
    if m := _RATING_LABEL_RE.search(label):
        rating = float(m.group(1).replace(",", "."))
    if m := _REVIEWS_LABEL_RE.search(label):
        reviews = _count(m.group(1))
    return rating, reviews


def parse_card(card: dict[str, Any]) -> dict[str, Any]:
    """Normalise one result card scraped from the results feed."""
    text = card.get("text") or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rating, reviews = parse_rating_label(card.get("rating_label"))
    category = None
    for ln in lines:
        if m := _CARD_RATING_RE.match(ln):
            rating = rating if rating is not None else float(m.group(1).replace(",", "."))
            reviews = reviews if reviews is not None else _count(m.group(2))
        parts = [p.strip() for p in ln.split("·") if p.strip()]
        for p in parts:
            if category is None and not _CARD_RATING_RE.match(p) and not any(ch.isdigit() for ch in p) \
                    and p not in ("Sponsored", card.get("name")) and "$" not in p and len(p) < 60:
                category = p
    phone_match = PHONE_RE.search(text)
    ids = parse_place_url(card.get("href"))
    return {
        "name": (card.get("name") or (lines[0] if lines else "")).strip(),
        "href": card.get("href"),
        "rating": rating,
        "review_count": reviews,
        "category": category,
        "phone": phone_match.group(0) if phone_match else None,
        "website": card.get("website"),
        "sponsored": bool(card.get("sponsored")) or "Sponsored" in lines[:3],
        **ids,
    }


def parse_relative_date(text: str | None, now: datetime) -> datetime | None:
    """``"2 weeks ago"`` / ``"Edited a month ago"`` / ``"yesterday"`` -> approximate datetime."""
    if not text:
        return None
    t = text.lower()
    if "yesterday" in t:
        return now - timedelta(days=1)
    if "today" in t or "just now" in t:
        return now
    m = _REL_DATE_RE.search(t)
    if not m:
        return None
    n = 1 if m.group(1) in ("a", "an", "one") else int(m.group(1))
    return now - timedelta(days=n * _UNIT_DAYS[m.group(2)])


def parse_place_dom(dom: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Normalise the fields scraped from a place page (see ``PLACE_JS`` in the provider)."""
    rating, reviews = parse_rating_label(dom.get("rating_label"))
    if rating is None and dom.get("rating_text"):
        try:
            rating = float(str(dom["rating_text"]).replace(",", "."))
        except ValueError:
            pass
    phone = None
    if pid := dom.get("phone_id"):
        phone = pid.split("phone:tel:", 1)[-1] or None
    phone = phone or dom.get("phone_text") or None

    photo_count = None
    if m := _PHOTOS_RE.search(dom.get("photos_text") or ""):
        photo_count = _count(m.group(1))

    dates = [d for d in (parse_relative_date(r.get("date"), now) for r in dom.get("reviews") or []) if d]
    sampled = dom.get("reviews") or []
    owner_rate = (sum(1 for r in sampled if r.get("owner_response")) / len(sampled)) if sampled else None
    claimed = None
    if dom.get("claim_link") is not None:
        claimed = not dom["claim_link"]
    return {
        "name": (dom.get("name") or "").strip() or None,
        "rating": rating,
        "review_count": reviews,
        "category": dom.get("category") or None,
        "address": dom.get("address") or None,
        "website": unwrap_google_redirect(dom.get("website")) or None,
        "phone": phone,
        "hours_label": dom.get("hours_label") or None,
        "claimed": claimed,
        "photo_count": photo_count,
        "recent_review_dates": [d.date().isoformat() for d in sorted(dates, reverse=True)] or None,
        "last_review_at": max(dates) if dates else None,
        "owner_response_rate": round(owner_rate, 2) if owner_rate is not None else None,
        "reviews_sample": [{k: r.get(k) for k in ("rating", "text", "author")} for r in sampled if r.get("text")],
    }


def is_blocked_page(url: str, text: str) -> bool:
    """Google's captcha / unusual-traffic interstitial."""
    return "/sorry/" in url or "unusual traffic from your computer network" in text.lower()
