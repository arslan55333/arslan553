"""Who owns page 1 of Google for "service + town"?

One results page holds up to four kinds of spots: search ads, Local Services Ads, the map pack and the organic
(normal) results. A business can take several. This report lists every spot, groups them by business, and says where
the lead appears — usually nowhere, which is the pitch: "your competitor owns 3 spots on page 1, you own none".
"""

from __future__ import annotations

import re
from typing import Any

from leadengine.normalize import is_shared_domain, normalize_domain, normalize_phone

EXTRA_DIRECTORIES = ("reddit.com", "quora.com", "youtube.com", "tiktok.com", "pinterest.com", "wikipedia.org",
                     "nextdoor.com", "porch.com", "expertise.com", "buildzoom.com", "birdeye.com", "mapquest.com",
                     "yellowpages.com", "bbb.org", "angi.com", "thumbtack.com", "homeadvisor.com", "houzz.com",
                     "manta.com", "superpages.com", "chamberofcommerce.com", "trustpilot.com", "craigslist.org")


def is_directory(domain: str | None) -> bool:
    if not domain:
        return False
    from leadengine.enrich.citations import DIRECTORIES
    d = domain.lower().removeprefix("www.")
    return is_shared_domain(d) or any(d == x or d.endswith("." + x) for x in (*DIRECTORIES, *EXTRA_DIRECTORIES))

SECTIONS = (("lsa", "Local Services Ads"), ("search", "Google Ads"), ("places", "Sponsored map listing"),
            ("map", "Map pack"), ("organic", "Organic results"))


def _key(name: str | None, domain: str | None) -> str:
    if domain and not is_shared_domain(domain):
        return domain.removeprefix("www.")
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _same_name(a: str | None, b: str | None) -> bool:
    wa = {w for w in re.findall(r"[a-z0-9]+", (a or "").lower()) if len(w) > 2 and w not in ("llc", "inc", "the", "and")}
    wb = {w for w in re.findall(r"[a-z0-9]+", (b or "").lower()) if len(w) > 2 and w not in ("llc", "inc", "the", "and")}
    return bool(wa) and len(wa & wb) / len(wa) >= 0.8


def page1(snapshot: dict[str, Any], *, name: str, domain: str | None, phone: str | None = None,
          name_domains: dict[str, str] | None = None) -> dict[str, Any]:
    """``name_domains``: lower-case business name -> website domain, for businesses we know (merges a map-pack
    spot and an organic spot of the same business)."""
    nd = {k.lower(): v for k, v in (name_domains or {}).items() if v}
    spots: list[dict[str, Any]] = []
    for ad in snapshot.get("ads") or []:
        spots.append({"section": ad.get("kind"), "position": ad.get("position"), "name": ad.get("title"),
                      "domain": ad.get("domain"), "phone": ad.get("phone")})
    for m in snapshot.get("local_pack") or []:
        if not m.get("sponsored"):
            spots.append({"section": "map", "position": m.get("position"), "name": m.get("name"),
                          "domain": m.get("domain") or nd.get((m.get("name") or "").lower()),
                          "rating": m.get("rating"), "reviews": m.get("reviews")})
    for o in snapshot.get("organic") or []:
        d = o.get("domain") or normalize_domain(o.get("url"))
        spots.append({"section": "organic", "position": o.get("position"), "name": o.get("title"), "domain": d,
                      "directory": is_directory(d), "title": o.get("title")})
    my_dom = (domain or "").removeprefix("www.") or None
    my_phone = normalize_phone(phone)

    def mine(s: dict) -> bool:
        if my_dom and s.get("domain") and (s["domain"].removeprefix("www.") == my_dom or s["domain"].endswith("." + my_dom)):
            return True
        if my_phone and s.get("phone") and normalize_phone(s["phone"]) == my_phone:
            return True
        return s["section"] in ("map", "lsa", "places") and _same_name(name, s.get("name"))

    for s in spots:
        s["mine"] = mine(s)
    owners: dict[str, dict[str, Any]] = {}
    for s in spots:
        if s.get("directory"):
            continue                                   # Yelp / Angi / Facebook pages in organic: not a competitor
        k = "__me__" if s["mine"] else _key(s.get("name"), s.get("domain"))
        label = name if s["mine"] else (s.get("domain") if s["section"] == "organic" and s.get("domain")
                                         else s.get("name") or s.get("domain"))
        o = owners.setdefault(k, {"name": label, "domain": s.get("domain"), "spots": 0, "sections": [], "mine": s["mine"]})
        if s["section"] in ("map", "lsa", "places", "search") and not s["mine"] and s.get("name"):
            o["name"] = s["name"]                       # a business name beats a bare domain
        o["spots"] += 1
        if s["section"] not in o["sections"]:
            o["sections"].append(s["section"])
    ranked = sorted(owners.values(), key=lambda o: (-o["spots"], o["name"] or ""))
    me = next((o for o in ranked if o["mine"]), None)
    organic_pos = next((s["position"] for s in spots if s["mine"] and s["section"] == "organic"), None)
    map_pos = next((s["position"] for s in spots if s["mine"] and s["section"] == "map"), None)
    total = len([s for s in spots if not s.get("directory")])
    return {"keyword": snapshot.get("keyword"), "location": snapshot.get("location"), "spots": spots,
            "owners": ranked[:8], "my_spots": me["spots"] if me else 0, "total_spots": total,
            "organic_position": organic_pos, "map_position": map_pos,
            "directories": sorted({s["domain"] for s in spots if s.get("directory") and s.get("domain")}),
            "has_page1": bool(snapshot.get("organic") or snapshot.get("local_pack"))}


def page1_line(p: dict[str, Any]) -> str | None:
    """Outreach sentence: the competitor that owns page 1 vs this business."""
    if not p or not p.get("has_page1"):
        return None
    top = next((o for o in p["owners"] if not o["mine"]), None)
    if not top or top["spots"] < 2 or p["my_spots"] >= top["spots"]:
        return None
    where = " + ".join({"search": "ads", "lsa": "Local Services Ads", "places": "map ads", "map": "the map pack",
                        "organic": "the normal results"}[x] for x in top["sections"])
    mine = "you don't show up at all" if not p["my_spots"] else f"you have {p['my_spots']}"
    return (f"on Google's first page for \"{p['keyword']}\", {top['name']} holds {top['spots']} spots ({where}) — "
            f"{mine}")
