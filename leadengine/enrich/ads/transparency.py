"""Google Ads Transparency Center, read in the free headless browser (no API key).

The public page lists every ad an advertiser ran, with "last shown" dates. We open the advertiser's page
for a domain and read the ad count and the most recent "last shown" date. Google can change this page at
any time, so a failure just returns ``None`` (the other ad signals still work).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from leadengine.log import get_logger

log = get_logger("ads.transparency")
URL = "https://adstransparency.google.com/?region=US&domain={domain}"
MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"


def parse_transparency_text(text: str, today: date) -> dict[str, Any] | None:
    """Body text of the Transparency Center results page -> {creatives, last_shown, days_ago}."""
    low = text.lower()
    if "no ads" in low and not re.search(r"\d+\s+ads?\b", low):
        return {"creatives": 0, "last_shown": None, "days_ago": None}
    m = re.search(r"(?:~\s*)?([\d,]+)\+?\s+ads?\b", text, re.I)
    creatives = int(m.group(1).replace(",", "")) if m else None
    shown = []
    for mm in re.finditer(rf"(?:last shown|shown)[:\s]+({MONTHS})[a-z]*\.?\s+(\d{{1,2}}),?\s+(\d{{4}})", text, re.I):
        try:
            shown.append(datetime.strptime(f"{mm.group(1)[:3]} {mm.group(2)} {mm.group(3)}", "%b %d %Y").date())
        except ValueError:
            continue
    if creatives is None and not shown:
        return None
    last = max(shown) if shown else None
    return {"creatives": creatives if creatives is not None else len(shown),
            "last_shown": last.isoformat() if last else None, "days_ago": (today - last).days if last else None}


async def transparency_browser(provider, domain: str, *, today: date | None = None,
                               url_template: str = URL) -> dict[str, Any] | None:
    today = today or date.today()
    proxy = provider.proxies.next()
    ctx = await provider._new_context(proxy, None)
    try:
        page = await ctx.new_page()
        await page.goto(url_template.format(domain=domain), wait_until="domcontentloaded")
        await page.wait_for_timeout(3500)          # results are rendered by JavaScript
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        out = parse_transparency_text(text or "", today)
        if out is not None:
            out["source"] = "transparency_center"
        return out
    except Exception as exc:
        log.warning("transparency center read failed", extra={"data": {"domain": domain, "error": str(exc)[:120]}})
        return None
    finally:
        await ctx.close()


# ── free JSON endpoint (same calls the Transparency Center page makes) ─────────
RPC = "https://adstransparency.google.com/anji/_/rpc/SearchService/"
RPC_HEADERS = {"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://adstransparency.google.com",
               "Referer": "https://adstransparency.google.com/?region=US",
               "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/141.0.0.0 Safari/537.36"}
_STOP = {"llc", "inc", "co", "corp", "corporation", "company", "the", "ltd", "of", "and", "pllc", "lp", "group"}


class TransparencyBlocked(Exception):
    """Google answered with its captcha page (datacenter IPs); try again from a normal connection."""


def _norm_name(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " and "))
    return " ".join(w for w in words if w not in _STOP)


def name_similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    x, y = _norm_name(a), _norm_name(b)
    if not x or not y:
        return 0.0
    if x == y or set(x.split()) == set(y.split()):
        return 1.0
    return SequenceMatcher(None, x, y).ratio()


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def parse_suggestions(data: Any) -> list[dict[str, Any]]:
    """Advertisers in a SearchSuggestions response: {"name", "id", "region", "ads"}."""
    out, seen = [], set()
    for d in _walk(data):
        name, adv = d.get("1"), d.get("2")
        if isinstance(name, str) and isinstance(adv, str) and adv.startswith("AR") and adv not in seen:
            seen.add(adv)
            ads = None
            for sub in _walk(d.get("4")):
                for v in sub.values():
                    if isinstance(v, str) and v.isdigit():
                        ads = max(ads or 0, int(v))
            out.append({"name": name, "id": adv, "region": d.get("3") if isinstance(d.get("3"), str) else None,
                        "ads": ads})
    return out


def parse_creatives(data: Any, today: date) -> dict[str, Any]:
    """SearchCreatives response -> {creatives, first_shown, last_shown, days_ago}."""
    items = data.get("1") if isinstance(data, dict) else None
    items = items if isinstance(items, list) else []
    firsts, lasts = [], []
    for c in items:
        if not isinstance(c, dict):
            continue
        for key, bucket in (("6", firsts), ("7", lasts)):
            v = c.get(key)
            ts = v.get("1") if isinstance(v, dict) else None
            if isinstance(ts, (str, int)) and str(ts).isdigit() and len(str(ts)) >= 9:
                bucket.append(datetime.utcfromtimestamp(int(ts)).date())
    last = max(lasts) if lasts else None
    return {"creatives": len(items), "first_shown": min(firsts).isoformat() if firsts else None,
            "last_shown": last.isoformat() if last else None, "days_ago": (today - last).days if last else None}


async def _rpc(http, method: str, payload: dict[str, Any]) -> Any:
    import json

    r = await http.request("POST", RPC + method + "?authuser=0", data={"f.req": json.dumps(payload)},
                           headers=RPC_HEADERS, follow_redirects=False, retries=0, timeout=20)
    if r.status_code in (301, 302, 429) or "google.com/sorry" in r.headers.get("location", ""):
        raise TransparencyBlocked(f"HTTP {r.status_code}")
    if r.status_code != 200:
        raise TransparencyBlocked(f"HTTP {r.status_code}")
    return json.loads(r.text.lstrip(")]}'\n"))


async def creatives_free(http, *, advertiser_id: str | None = None, domain: str | None = None,
                         today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    filt: dict[str, Any] = {"12": {"1": domain or "", "2": True}}
    if advertiser_id:
        filt["13"] = {"1": [advertiser_id]}
    data = await _rpc(http, "SearchCreatives", {"2": 40, "3": filt, "7": {"1": 1, "2": 30, "3": 2840}})
    return parse_creatives(data, today)


async def advertiser_by_name(http, name: str, *, city: str | None = None, today: date | None = None,
                             min_similarity: float = 0.85) -> dict[str, Any]:
    """Is there a Google Ads advertiser account with this business's name? (free; for businesses with no
    website, which can still run Local Services Ads, Maps ads or call-only ads)."""
    today = today or date.today()
    data = await _rpc(http, "SearchSuggestions", {"1": name, "2": 10, "3": 10})
    cands = [c for c in parse_suggestions(data) if c["region"] in (None, "US")]
    scored = sorted(((name_similarity(name, c["name"]), c) for c in cands), key=lambda t: -t[0])
    if not scored or scored[0][0] < min_similarity:
        return {"creatives": 0, "last_shown": None, "first_shown": None, "by_name": True,
                "candidates": [c["name"] for c in cands[:5]], "source": "transparency_free"}
    sim, best = scored[0]
    out = {"advertiser": best["name"], "advertiser_id": best["id"], "by_name": True, "name_match": round(sim, 2),
           "creatives": best["ads"], "last_shown": None, "first_shown": None, "source": "transparency_free"}
    try:
        out.update({k: v for k, v in (await creatives_free(http, advertiser_id=best["id"], today=today)).items()
                    if v is not None})
    except TransparencyBlocked:
        pass
    return out


async def domain_free(http, domain: str, *, today: date | None = None) -> dict[str, Any]:
    out = await creatives_free(http, domain=domain, today=today)
    out["source"] = "transparency_free"
    return out
