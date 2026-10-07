"""Citations / NAP consistency: where else on the web is this business listed, and do those listings show the
same Name, Address and Phone as its Google profile?

Google trusts a business more when its NAP is identical everywhere (Yelp, BBB, Facebook, Yellow Pages, Angi …).
Wrong or old phone numbers on directories also lose real calls.

How it works (2 web searches per business, no page scraping):
1. search the phone number in quotes → listings that carry this phone;
2. search the name + city → listings that may carry an OLD phone or address;
3. read phone / ZIP / street number from each result's title + snippet and compare with the Google listing;
4. report which important directories have it, which don't, and which disagree.

Search goes through Firecrawl (``/search``, ~2 credits per query) or SerpAPI (1 credit per query).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from leadengine.log import get_logger
from leadengine.normalize import normalize_phone

log = get_logger("citations")

# domain -> (label, core?) — "core" = the directories every local service business should be on
DIRECTORIES: dict[str, tuple[str, bool]] = {
    "yelp.com": ("Yelp", True), "bbb.org": ("BBB", True), "facebook.com": ("Facebook", True),
    "yellowpages.com": ("Yellow Pages", True), "angi.com": ("Angi", True), "nextdoor.com": ("Nextdoor", True),
    "mapquest.com": ("MapQuest", True), "apple.com": ("Apple Maps", True), "bing.com": ("Bing Places", True),
    "thumbtack.com": ("Thumbtack", False), "homeadvisor.com": ("HomeAdvisor", False), "houzz.com": ("Houzz", False),
    "manta.com": ("Manta", False), "superpages.com": ("Superpages", False), "chamberofcommerce.com": ("Chamber of Commerce", False),
    "chambermaster.com": ("Local Chamber", False), "merchantcircle.com": ("MerchantCircle", False),
    "hotfrog.com": ("Hotfrog", False), "brownbook.net": ("Brownbook", False), "foursquare.com": ("Foursquare", False),
    "porch.com": ("Porch", False), "expertise.com": ("Expertise", False), "bizapedia.com": ("Bizapedia", False),
    "instagram.com": ("Instagram", False), "x.com": ("X / Twitter", False), "twitter.com": ("X / Twitter", False),
    "linkedin.com": ("LinkedIn", False), "angieslist.com": ("Angi", True), "cylex.us.com": ("Cylex", False),
    "local.yahoo.com": ("Yahoo Local", False), "citysearch.com": ("Citysearch", False), "dexknows.com": ("DexKnows", False),
    "buildzoom.com": ("BuildZoom", False), "birdeye.com": ("Birdeye", False), "trustpilot.com": ("Trustpilot", False),
}
CORE = sorted({label for label, core in DIRECTORIES.values() if core})
# not findable by web search — always listed as "check by hand"
MANUAL = ("Apple Maps", "Bing Places")

# phone look-up / people-search sites list many numbers on one page: never citations
SKIP_HOSTS = ("whitepages.com", "spokeo.com", "fastpeoplesearch.com", "truepeoplesearch.com", "411.com", "anywho.com",
              "usphonebook.com", "numlookup.com", "callercenter.com", "sync.me", "800notes.com", "reversephonelookup",
              "beenverified.com", "radaris.com", "peoplefinders.com", "thatsthem.com", "zabasearch.com")
SOCIAL = {"Facebook", "Instagram", "X / Twitter", "LinkedIn", "Nextdoor"}
POST_PATH = re.compile(r"/(groups|posts|videos|p|reel|status|photos|events|story\.php|permalink)(/|$|\.)", re.I)
ADDRESS_RE = re.compile(r"\b(\d{1,6}(?:-\d{1,4})?)\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z0-9]+\s+){0,3}?"
                        r"(?:st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|ln|lane|way|hwy|highway|pkwy|parkway|"
                        r"pl|place|ct|court|ter|terrace|tpke|turnpike|cir|circle|sq|square)\b\.?", re.I)
PHONE_RE = re.compile(r"(?<!\d)\(?(\d{3})\)?[\s.\-]{0,2}(\d{3})[\s.\-]{1,2}(\d{4})(?!\d)")
ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
STREET_RE = re.compile(r"\b(\d{1,6}(?:-\d{1,4})?)\s+(?:[NSEW]\.?\s+)?([A-Za-z0-9]+)", re.I)


def directory_of(url: str) -> tuple[str | None, bool]:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.").removeprefix("m.")
    for dom, (label, core) in DIRECTORIES.items():
        if host == dom or host.endswith("." + dom):
            if dom == "apple.com" and "maps" not in host:
                return None, False
            if dom == "bing.com" and "/maps" not in url:
                return None, False
            return label, core
    return None, False


def phones_in(text: str) -> set[str]:
    return {"".join(m.groups()) for m in PHONE_RE.finditer(text or "")}


def street_key(address: str | None) -> tuple[str | None, str | None]:
    """('187-40', '11423') from '187-40 Hollis Ave, Jamaica, NY 11423'."""
    if not address:
        return None, None
    m = STREET_RE.search(address)
    z = ZIP_RE.findall(address)
    return (m.group(1) if m else None), (z[-1] if z else None)


def same_name(name: str | None, text: str) -> bool:
    """Most of the business's distinctive name words appear in the result (guards against look-alikes)."""
    from leadengine.enrich.ads.transparency import _norm_name

    words = [w for w in _norm_name(name or "").split() if len(w) > 2]
    hay = _norm_name(text)
    return bool(words) and sum(w in hay.split() for w in words) >= max(1, round(len(words) * 0.67))


def check_listing(result: dict[str, Any], *, phone: str | None, address: str | None, own_domain: str | None,
                  name: str | None = None) -> dict | None:
    url = result.get("url") or ""
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    if url.lower().endswith(".pdf") or (own_domain and host.endswith(own_domain)) or any(h in host for h in SKIP_HOSTS):
        return None
    label, core = directory_of(url)
    if label == "Facebook" and "/groups/" in url:
        return None          # someone else's post in a group, not the business page
    post = label in SOCIAL and bool(POST_PATH.search(urlsplit(url).path))
    text = f"{result.get('title', '')} {result.get('description', '')}"
    if result.get("q") == "name" and not same_name(name, text):
        return None          # found by name, but it's a different business
    phones = phones_in(text)
    my_phone = normalize_phone(phone)
    my_num, my_zip = street_key(address)
    zips = set(ZIP_RE.findall(text))
    issues = []
    phone_ok = None
    if phones and my_phone:
        phone_ok = my_phone in phones
        if not phone_ok:
            other = sorted(phones)[0]
            issues.append(f"shows a different phone: ({other[:3]}) {other[3:6]}-{other[6:]}")
    zip_ok = None
    if post:                                   # a social post: its text isn't a profile, only presence counts
        zips = set()
    if zips and my_zip:
        zip_ok = my_zip in zips
        if not zip_ok:
            issues.append(f"shows a different ZIP ({sorted(zips)[0]}) — old or wrong address?")
    street_ok = None
    addrs = [] if post else [m.group(1) for m in ADDRESS_RE.finditer(text)]
    if my_num and addrs:
        street_ok = my_num in addrs
        if not street_ok and zip_ok is not False:
            issues.append(f"shows a different street address ({addrs[0]} …)")
    if label is None and phone_ok is not True:
        return None          # a random page that doesn't even carry the phone: not a citation
    return {"site": label or host, "directory": label is not None, "core": core, "url": url,
            "phone_ok": phone_ok, "zip_ok": zip_ok, "street_ok": street_ok, "issues": issues,
            "snippet": result.get("description", "")[:200]}


def analyze(results: list[dict[str, Any]], *, phone: str | None, address: str | None,
            own_domain: str | None, queries: int, source: str, name: str | None = None) -> dict[str, Any]:
    listings: dict[str, dict] = {}
    for r in results:
        row = check_listing(r, phone=phone, address=address, own_domain=own_domain, name=name)
        if row is None:
            continue
        key = row["site"]
        old = listings.get(key)
        # keep the listing with the most evidence; a mismatch anywhere on that site is worth reporting
        if old is None or (row["issues"] and not old["issues"]) or (row["phone_ok"] and old["phone_ok"] is None):
            listings[key] = row
    rows = sorted(listings.values(), key=lambda x: (not x["core"], not x["directory"], x["site"]))
    found_core = sorted({x["site"] for x in rows if x["core"]})
    missing = [d for d in CORE if d not in found_core and d not in MANUAL]
    bad = [x for x in rows if x["issues"]]
    issues, positives = [], []
    for x in bad[:6]:
        issues.append(f"{x['site']} {x['issues'][0]}")
    if missing:
        issues.append(f"not found on {', '.join(missing)}")
    if found_core:
        positives.append(f"listed on {', '.join(found_core)}")
    consistent = [x for x in rows if x["phone_ok"] and not x["issues"]]
    if consistent:
        positives.append(f"{len(consistent)} listing(s) match the Google phone number")
    core_total = len([d for d in CORE if d not in MANUAL])
    score = round(100 * (0.6 * len([d for d in found_core if d not in MANUAL]) / max(1, core_total)
                         + 0.4 * (1 - len(bad) / max(1, len(rows)))))
    return {"listings": rows, "found_core": found_core, "missing_core": missing, "manual": list(MANUAL),
            "inconsistent": len(bad), "total": len(rows), "score": max(0, min(100, score)),
            "issues": issues, "positives": positives, "queries": queries, "source": source}


def format_phone(p: str | None) -> str | None:
    n = normalize_phone(p)
    return f"({n[:3]}) {n[3:6]}-{n[6:]}" if n and len(n) == 10 else p


async def web_search(query: str, *, firecrawl=None, http=None, serp_key: str = "", credits=None) -> tuple[list[dict], str]:
    if firecrawl is not None and firecrawl.enabled:
        got = await firecrawl.search(query, limit=10)
        if got is not None:
            return got, "firecrawl"
    if serp_key and http is not None:
        r = await http.request("GET", "https://serpapi.com/search.json", retries=1, timeout=60, params={
            "engine": "google", "q": query, "num": 10, "hl": "en", "gl": "us", "api_key": serp_key})
        data = r.json() if r.status_code == 200 else {}
        if credits is not None:
            credits.record("serpapi", "citations_search", success=not data.get("error"))
        return [{"url": x.get("link"), "title": x.get("title") or "", "description": x.get("snippet") or ""}
                for x in data.get("organic_results") or [] if x.get("link")], "serpapi"
    return [], "none"


async def audit_citations(biz, *, firecrawl=None, http=None, serp_key: str = "", credits=None) -> dict[str, Any] | None:
    phone = format_phone(biz.phone)
    queries = []
    if phone:
        queries.append(("phone", f'"{phone}"'))
    if biz.name:
        queries.append(("name", f'"{biz.name}" {biz.city or ""} {biz.state or ""}'.strip()))
    results: list[dict] = []
    source = "none"
    for kind, q in queries:
        got, source = await web_search(q, firecrawl=firecrawl, http=http, serp_key=serp_key, credits=credits)
        results += [{**r, "q": kind} for r in got]
    if source == "none":
        return None
    return analyze(results, phone=biz.phone, address=biz.address, own_domain=biz.domain, queries=len(queries),
                   source=source, name=biz.name)
