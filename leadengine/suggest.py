"""Search-box suggestions, like the Google Maps search bar.

* Keywords: your past keywords + a built-in list of local-service niches + (online) Google's own
  autocomplete, which is what people actually type.
* Places: ZIPs and cities from the bundled ZIP data (instant, offline) + (online) neighbourhoods,
  villages and towns from OpenStreetMap (Photon search, Overpass "what's around this ZIP").
  Every place is mapped to the nearest ZIP code(s), because scans run per ZIP.
Online sources are free, cached, and silently skipped when offline.
"""

from __future__ import annotations

import json
import re
from typing import Any

from leadengine.geo.zipdata import haversine_km, zip_directory
from leadengine.http import HttpClient
from leadengine.log import get_logger

log = get_logger("suggest")

NICHES = sorted({
    "air conditioning repair", "appliance repair", "asphalt paving", "auto body shop", "auto repair", "bathroom remodeling",
    "carpet cleaning", "chimney sweep", "chiropractor", "concrete contractor", "cosmetic dentist", "deck builder",
    "demolition contractor", "dentist", "drain cleaning", "driveway paving", "dumpster rental", "electrician",
    "emergency plumber", "excavation contractor", "fence contractor", "flooring contractor", "foundation repair",
    "garage door repair", "gutter cleaning", "handyman", "hardwood floor refinishing", "home builder", "home inspector",
    "house cleaning", "hvac contractor", "insulation contractor", "interior designer", "irrigation contractor",
    "junk removal", "kitchen remodeling", "landscaping", "lawn care", "locksmith", "medical spa", "mold removal",
    "moving company", "painting contractor", "personal injury lawyer", "pest control", "plumber", "pool cleaning",
    "pool contractor", "pressure washing", "remodeling contractor", "roll off dumpster", "roof repair", "roofing contractor",
    "septic pumping", "septic service", "siding contractor", "solar installer", "storage units", "tile contractor",
    "towing service", "tree service", "water damage restoration", "water heater repair", "waterproofing contractor",
    "window cleaning", "window replacement", "criminal defense lawyer", "dui lawyer", "family lawyer", "orthodontist",
    "physical therapist", "plastic surgeon", "veterinarian", "auto glass repair", "car detailing", "tutoring",
})
PLACE_TYPES = {"city", "town", "village", "hamlet", "suburb", "neighbourhood", "quarter", "borough", "locality"}
_mem: dict[str, Any] = {}   # tiny in-process cache for online lookups


async def _get_json(http: HttpClient | None, url: str, params: dict[str, Any], key: str) -> Any:
    if http is None:
        return None
    if key in _mem:
        return _mem[key]
    try:
        r = await http.request("GET", url, params=params, retries=0, timeout=6)
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except ValueError:
            data = json.loads(r.content.decode("latin-1"))
    except Exception as exc:   # offline / blocked: suggestions are a nicety
        log.debug("suggestion source failed", extra={"data": {"url": url, "error": type(exc).__name__}})
        return None
    _mem[key] = data
    return data


# ── keywords ─────────────────────────────────────────────────────────
async def keyword_suggestions(q: str, *, http: HttpClient | None = None, past: list[str] | None = None,
                              online: bool = True, limit: int = 10) -> list[dict[str, str]]:
    ql = q.strip().lower()
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(text: str, source: str) -> None:
        t = re.sub(r"\s+", " ", text.strip().lower())
        if t and t not in seen and len(out) < limit:
            seen.add(t)
            out.append({"text": t, "source": source})

    for k in past or []:
        if not ql or ql in k.lower():
            add(k, "your searches")
    if ql:
        for k in NICHES:
            if k.startswith(ql):
                add(k, "niche")
        for k in NICHES:
            if ql in k:
                add(k, "niche")
    if online and len(ql) >= 2:
        data = await _get_json(http, "https://suggestqueries.google.com/complete/search",
                               {"client": "firefox", "hl": "en", "gl": "us", "q": ql}, f"kw:{ql}")
        if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
            for s in data[1]:
                if isinstance(s, str) and not re.search(r"\b(jobs?|salary|near me now|reddit|wiki)\b", s):
                    add(s, "google")
    if not ql:
        for k in NICHES[:limit]:
            add(k, "niche")
    return out


# ── places ───────────────────────────────────────────────────────────
def _zip_item(z, kind: str = "zip", label: str | None = None) -> dict[str, Any]:
    return {"type": kind, "label": label or f"{z.zip} · {z.city}, {z.state}", "zips": [z.zip], "lat": z.lat, "lng": z.lng,
            "population": z.population}


def offline_places(q: str, limit: int = 8) -> list[dict[str, Any]]:
    d = zip_directory()
    q = q.strip()
    if not q:
        return []
    if q.isdigit():
        rows = [z for z in d.all() if z.zip.startswith(q)]
        rows.sort(key=lambda z: (z.zip != q, -(z.population or 0)))
        return [_zip_item(z) for z in rows[:limit]]
    name, _, state = q.partition(",")
    name, state = name.strip().lower(), state.strip().upper()
    groups: dict[tuple[str, str], list] = {}
    for z in d.all():
        if z.city.lower().startswith(name) and (not state or z.state.startswith(state)):
            groups.setdefault((z.city, z.state), []).append(z)
    ranked = sorted(groups.items(), key=lambda kv: -sum(z.population or 0 for z in kv[1]))[:limit]
    out = []
    for (city, st), zs in ranked:
        zs.sort(key=lambda z: -(z.population or 0))
        out.append({"type": "city", "label": f"{city}, {st} · {len(zs)} ZIP{'s' if len(zs) > 1 else ''}",
                    "zips": [z.zip for z in zs], "lat": zs[0].lat, "lng": zs[0].lng,
                    "population": sum(z.population or 0 for z in zs)})
    return out


def zips_near(lat: float, lng: float, radius_km: float = 2.5, limit: int = 3) -> list[str]:
    near = zip_directory().nearby(lat, lng, radius_km)
    if not near:                                    # rural places: big ZIPs, take the closest one
        near = zip_directory().nearby(lat, lng, 30)[:1]
    return [z.zip for z, _ in near[:limit]]


async def online_places(q: str, http: HttpClient | None, limit: int = 6) -> list[dict[str, Any]]:
    """Neighbourhoods / villages / towns by name (Photon, OpenStreetMap)."""
    if len(q.strip()) < 3 or q.strip().isdigit():
        return []
    data = await _get_json(http, "https://photon.komoot.io/api/",
                           {"q": q, "limit": 15, "lang": "en", "bbox": "-171,18,-66,72"}, f"ph:{q.lower()}")
    out = []
    for f in (data or {}).get("features", []) if isinstance(data, dict) else []:
        p = f.get("properties") or {}
        if p.get("countrycode") not in (None, "US") or p.get("osm_value") not in PLACE_TYPES:
            continue
        lng, lat = (f.get("geometry") or {}).get("coordinates", [None, None])[:2]
        if lat is None:
            continue
        where = ", ".join(x for x in (p.get("city") or p.get("county"), p.get("state")) if x and x != p.get("name"))
        out.append({"type": p.get("osm_value"), "label": f"{p.get('name')} ({p.get('osm_value')})" + (f" · {where}" if where else ""),
                    "zips": zips_near(lat, lng), "lat": lat, "lng": lng, "population": None})
        if len(out) >= limit:
            break
    return out


async def areas_for_zip(zip_code: str, *, http: HttpClient | None = None, repo=None, online: bool = True,
                        radius_km: float | None = None) -> dict[str, Any]:
    """Towns, villages and neighbourhoods in and around a ZIP, each with the ZIP(s) to scan."""
    d = zip_directory()
    z = d.get(zip_code)
    if z is None:
        return {"zip": zip_code, "label": None, "areas": []}
    radius = radius_km or max(4.0, z.radius_km * 2.5)
    areas: dict[str, dict[str, Any]] = {}
    for other, dist in d.nearby(z.lat, z.lng, radius):
        key = other.city.lower()
        a = areas.setdefault(key, {"name": other.city, "type": "town (ZIP data)", "state": other.state, "zips": [],
                                   "km": round(dist, 1), "lat": other.lat, "lng": other.lng})
        a["zips"].append(other.zip)
    if online:
        cache_key = f"areas:{zip_code}:{int(radius)}"
        cached = repo.get_geo(cache_key, 180) if repo is not None else None
        osm = cached.payload if cached is not None else None
        if osm is None:
            query = (f'[out:json][timeout:15];node(around:{int(radius * 1000)},{z.lat},{z.lng})'
                     '[place~"^(city|town|village|hamlet|suburb|neighbourhood|quarter|borough)$"][name];out 80;')
            data = await _get_json(http, "https://overpass-api.de/api/interpreter", {"data": query}, cache_key)
            if isinstance(data, dict):
                osm = [{"name": e["tags"]["name"], "type": e["tags"].get("place"), "lat": e["lat"], "lng": e["lon"]}
                       for e in data.get("elements", []) if e.get("tags", {}).get("name") and "lat" in e]
                if repo is not None:
                    repo.set_geo(cache_key, z.lat, z.lng, osm)
        for e in osm or []:
            key = e["name"].lower()
            if key in areas:
                areas[key]["type"] = e["type"] or areas[key]["type"]
                continue
            areas[key] = {"name": e["name"], "type": e["type"], "state": z.state, "zips": zips_near(e["lat"], e["lng"]),
                          "km": round(haversine_km(z.lat, z.lng, e["lat"], e["lng"]), 1), "lat": e["lat"], "lng": e["lng"]}
    from leadengine.geo.explorer import wealth_label, wealth_score
    for a in areas.values():
        zs = [d.get(c) for c in a["zips"] if d.get(c)]
        inc = next((x.median_household_income for x in zs if x.median_household_income), None)
        home = next((x.median_home_value for x in zs if x.median_home_value), None)
        a["income"], a["wealth"] = inc, wealth_score(inc, home)
        a["wealth_label"] = wealth_label(a["wealth"])
    ordered = sorted(areas.values(), key=lambda a: a["km"])
    return {"zip": zip_code, "label": z.label, "lat": z.lat, "lng": z.lng, "radius_km": round(radius, 1), "areas": ordered}
