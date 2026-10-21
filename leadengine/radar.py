"""New-business radar: companies that registered with the state in the last weeks — before they have a Google
listing or a website. They need everything (website, Google Business Profile, reviews), and nobody has pitched them yet.

Free state open-data registries (Socrata, no key), updated every few days:
* New York  — data.ny.gov "Active Corporations" (name, filing date, county, process address)
* Colorado  — data.colorado.gov "Business Entities" (principal address, status)
* Oregon    — data.oregon.gov "Active Businesses" (principal place of business)
* Connecticut — data.ct.gov "Business Registry" (mailing address, owner flags)

Other states (Florida's Sunbiz, Texas, California …) publish bulk files or no open API; they can be added the same way.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from leadengine.log import get_logger

log = get_logger("radar")

SOURCES: dict[str, dict[str, Any]] = {
    "NY": {"name": "New York", "url": "https://data.ny.gov/resource/n9v6-gdp6.json", "id": "dos_id",
           "title": "current_entity_name", "date": "initial_dos_filing_date", "type": "entity_type",
           "city": "dos_process_city", "zip": "dos_process_zip", "county": "county",
           "address": ("dos_process_address_1", "dos_process_address_2"), "contact": "dos_process_name"},
    "CO": {"name": "Colorado", "url": "https://data.colorado.gov/resource/4ykn-tg5h.json", "id": "entityid",
           "title": "entityname", "date": "entityformdate", "type": "entitytype", "city": "principalcity",
           "zip": "principalzipcode", "county": None, "address": ("principaladdress1",), "contact": "agentorganizationname",
           "where": "entitystatus = 'Good Standing'"},
    "OR": {"name": "Oregon", "url": "https://data.oregon.gov/resource/tckn-sxa6.json", "id": "registry_number",
           "title": "business_name", "date": "registry_date", "type": "entity_type", "city": "city", "zip": "zip",
           "county": None, "address": ("address",), "contact": None,
           "where": "associated_name_type = 'PRINCIPAL PLACE OF BUSINESS'"},
    "CT": {"name": "Connecticut", "url": "https://data.ct.gov/resource/n7gp-d28j.json", "id": "accountnumber",
           "title": "name", "date": "date_registration", "type": None, "city": None, "zip": None, "county": None,
           "address": ("mailing_address",), "contact": None, "where": "status = 'Active'"},
}
# words that make a name too generic to search for ("NEW YORK LLC")
_STOP = {"LLC", "INC", "CORP", "CO", "THE", "AND", "OF", "SERVICES", "SERVICE", "GROUP", "COMPANY"}


GENERIC = {"removal", "rental", "service", "services", "repair", "clean", "cleaning", "install", "company", "contractor",
           "house", "home", "near", "best"}


def search_words(keyword: str) -> list[str]:
    """'junk removal' -> ['JUNK', 'HAULING', 'CLEANOUT', 'DEBRIS', 'RUBBISH'] — distinctive niche words only (phrases kept
    whole, so 'clean out' never turns into 'clean' and pulls in cleaning companies)."""
    from leadengine.insights import niche_for

    n = niche_for(keyword)
    kw_words = [w for w in re.findall(r"[a-z]+", keyword.lower()) if len(w) > 3]
    words = [w for w in kw_words if w not in GENERIC] or sorted(kw_words, key=len)[-1:]
    for m in n["match"]:
        if " " in m.strip():
            continue                                   # phrases ('clean out', 'roll off') are too loose in names
        if len(m) > 3 and m not in GENERIC:
            words.append(m)
    extra = {"junk removal": ["rubbish"], "dumpster rental": ["dumpster", "roll-off"], "plumber": ["plumbing"],
             "hvac": ["heating", "cooling"], "landscaping": ["lawn"]}.get(n["niche"], [])
    return list(dict.fromkeys(w.upper() for w in words + extra))[:6] or [keyword.upper()]


def soql(src: dict[str, Any], words: list[str], since: date, limit: int) -> dict[str, Any]:
    like = " OR ".join(f"upper({src['title']}) like '%{w.replace(chr(39), '')}%'" for w in words)
    where = f"({like}) AND {src['date']} > '{since.isoformat()}'"
    if src.get("where"):
        where += f" AND {src['where']}"
    return {"$where": where, "$order": f"{src['date']} DESC", "$limit": limit}


def _zip(text: str | None) -> str | None:
    m = re.search(r"\b(\d{5})(?:-\d{4})?\b", text or "")
    return m.group(1) if m else None


def normalize_row(state: str, src: dict[str, Any], r: dict[str, Any]) -> dict[str, Any] | None:
    title = (r.get(src["title"]) or "").strip()
    reg_id = str(r.get(src["id"]) or "")
    if not title or not reg_id:
        return None
    addr = ", ".join(x for x in (r.get(f) for f in src["address"]) if x and x.strip(" ,"))
    addr = re.sub(r"\s*,\s*(,\s*)+", ", ", addr).strip(" ,")
    city = (r.get(src["city"]) if src["city"] else None) or None
    if not city and state == "CT":                       # "223 N Bishop Ave , ,Bridgeport ,CT ,United States ,06610"
        parts = [p.strip() for p in (r.get("mailing_address") or "").split(",") if p.strip()]
        city = parts[-4] if len(parts) >= 4 else None
    return {"state": state, "reg_id": reg_id, "name": title.title() if title.isupper() else title,
            "filed": (r.get(src["date"]) or "")[:10], "entity_type": (r.get(src["type"]) or "").title() if src["type"] else None,
            "city": city.title() if city else None, "zip": (r.get(src["zip"]) if src["zip"] else None) or _zip(addr),
            "county": r.get(src["county"]) if src["county"] else None, "address": addr[:250] or None,
            "contact": (r.get(src["contact"]) or None) if src["contact"] else None}


async def fetch_new(http, keyword: str, states: list[str], *, days: int = 60, limit: int = 200,
                    today: date | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """(rows, errors) — newest registrations whose name matches the niche, per state."""
    since = (today or date.today()) - timedelta(days=days)
    words = search_words(keyword)
    rows, errors = [], []
    for st in states:
        src = SOURCES.get(st.upper())
        if src is None:
            errors.append(f"{st}: no open registry yet")
            continue
        try:
            r = await http.request("GET", src["url"], params=soql(src, words, since, limit), retries=1, timeout=45)
            data = r.json() if r.status_code == 200 else []
            if r.status_code != 200:
                errors.append(f"{st}: HTTP {r.status_code}")
        except Exception as exc:
            errors.append(f"{st}: {type(exc).__name__}")
            continue
        seen = set()
        for item in data if isinstance(data, list) else []:
            row = normalize_row(st.upper(), src, item)
            if row and row["reg_id"] not in seen:
                seen.add(row["reg_id"])
                rows.append(row)
    rows.sort(key=lambda x: x["filed"], reverse=True)
    return rows, errors


AGGREGATORS = ("bizapedia", "opencorporates", "issuu", "recordwire", "corporationwiki", "bizstanding", "buzzfile",
               "dnb.com", "zoominfo", "bizprofile", "companiesny", "nycompanies", "opengovus", "sec.gov", "ny.gov",
               "colorado.gov", "oregon.gov", "ct.gov", "manta", "chamberofcommerce", "bisprofiles", "allbiz")


def guess_contact(results: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """From web-search results for the company name: its website, phone and any directory listing."""
    from leadengine.enrich.citations import directory_of, phones_in, same_name
    from leadengine.enrich.page1 import is_directory
    from leadengine.normalize import normalize_domain
    from leadengine.scoring.spam import name_matches_domain

    out: dict[str, Any] = {"website": None, "phones": [], "listings": []}
    for r in results:
        text = f"{r.get('title', '')} {r.get('description', '')}"
        url = r.get("url") or ""
        if not same_name(name, text) or re.search(r"/(search|groups|find)[/?]|[?&](q|find_desc)=", url):
            continue                                   # a search-results page, not the company's own listing
        d = normalize_domain(r.get("url") or "")
        label, _ = directory_of(r.get("url") or "")
        if label or is_directory(d):
            out["listings"].append({"site": label or d, "url": r.get("url")})
        elif d and out["website"] is None and name_matches_domain(name, d) and not any(a in d for a in AGGREGATORS):
            out["website"] = f"https://{d}"             # only a domain that looks like the company's own name
        out["phones"] += [p for p in phones_in(text) if p not in out["phones"]]
    out["phones"] = out["phones"][:3]
    out["listings"] = out["listings"][:6]
    out["on_google"] = any(x["site"] in ("Google", "google.com") for x in out["listings"])
    return out
