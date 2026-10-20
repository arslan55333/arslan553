"""Domain Rescue: is the business's domain alive, expiring, expired, or already free to register?

Free sources:
* RDAP (the modern WHOIS, via rdap.org → the registry, then the registrar): registered or not, statuses
  (client hold, redemption period, pending delete …), registration / expiry dates, registrar;
* the website itself: does it load, or does it show a registrar "expired" / "for sale" / parking page;
* the Wayback Machine (optional, rate-limited): when the site was last seen working, with a link to the old site;
* name-based guesses for businesses whose Google listing has no website any more (``antsjunkremoval.com`` …),
  confirmed by the Wayback Machine.

Optional: a Porkbun API key confirms "available to register" and the price.

The point is a **rescue offer to the owner** ("your domain expired 12 days ago — you can still renew it until …"),
never registering a working business's name to sell it back (that is cybersquatting under US law).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from leadengine.log import get_logger
from leadengine.normalize import normalize_domain

log = get_logger("domains")

EXPIRED_PAGE = re.compile(r"this domain (?:name )?(?:has )?expired|domain (?:has )?expired|expired domain|"
                          r"renew (?:this|your) domain|this domain is pending renewal|domain registration has expired|"
                          r"is expired and pending renewal or deletion", re.I)
FOR_SALE_PAGE = re.compile(r"(?:this|the) domain (?:name )?(?:is|may be) for sale|buy this domain|"
                           r"make an offer on this domain|domain is available for purchase|inquire about this domain|"
                           r"get this domain|the domain .{0,40} is for sale", re.I)
PARKED_PAGE = re.compile(r"domain parking|parked free|this domain is parked|parked domain|related searches|"
                         r"sponsored listings", re.I)
SALE_HOSTS = ("sedo.com", "dan.com", "afternic.com", "hugedomains.com", "godaddy.com/domainsearch", "bodis.com",
              "parkingcrew", "above.com", "undeveloped.com", "atom.com", "squadhelp", "brandbucket", "buydomains.com",
              "perfectdomain", "spaceship.com", "uniregistry")
BAD_STATUSES = {"client hold": "expired_grace", "server hold": "expired_grace", "auto renew period": "expired_grace",
                "renew period": None, "redemption period": "redemption", "pending delete": "pending_delete",
                "pending restore": "redemption"}

LABELS = {
    "available": ("Available to register", "🟢", "The domain is free. Offer the owner a rescue: you register it for them and "
                                               "relaunch the site (never hold it against them)."),
    "pending_delete": ("Pending delete", "🟠", "It drops back to the public in a few days. Tell the owner now; with their "
                                              "OK you can backorder it for them."),
    "redemption": ("Redemption period", "🔴", "Only the owner can still get it back (registrar fee ~$100+). Call them today."),
    "expired_grace": ("Expired — grace period", "🔴", "The site is offline but only the owner can renew it — the hottest lead. "
                                                     "Call / email today."),
    "for_sale": ("Taken / for sale by someone else", "🟣", "A domainer owns it now. Offer a new domain + a new site."),
    "expiring_soon": ("Expiring soon", "🟡", "Still working, expires within 60 days. A friendly heads-up opens the door."),
    "site_down": ("Domain fine, site down", "🟠", "Hosting / DNS problem — website repair lead."),
    "ok": ("OK", "⚪", ""),
    "unknown": ("Unknown", "⚪", "No RDAP / WHOIS answer for this domain extension right now."),
    "no_own_domain": ("No own domain", "🔗", "The site lives on a website-builder subdomain."),
}


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


def parse_rdap(data: dict[str, Any]) -> dict[str, Any]:
    events = {e.get("eventAction"): _dt(e.get("eventDate")) for e in data.get("events") or [] if isinstance(e, dict)}
    registrar = None
    for ent in data.get("entities") or []:
        if "registrar" in (ent.get("roles") or []):
            for item in (ent.get("vcardArray") or [None, []])[1]:
                if isinstance(item, list) and item and item[0] == "fn":
                    registrar = item[3]
    related = next((l.get("href") for l in data.get("links") or [] if l.get("rel") == "related" and l.get("href")), None)
    return {"statuses": [s.lower() for s in data.get("status") or []], "registered": events.get("registration"),
            "expires": events.get("expiration"), "registrar": registrar, "related": related,
            "nameservers": [n.get("ldhName", "").lower() for n in data.get("nameservers") or []]}


def classify(*, registered: bool | None, statuses: list[str], expires: datetime | None, registered_at: datetime | None,
             page: dict[str, Any] | None, was_ours_until: datetime | None = None,
             now: datetime | None = None) -> str:
    now = now or datetime.utcnow()
    if registered is None:
        return "unknown"
    if registered is False:
        return "available"
    for s in statuses:
        for key, label in BAD_STATUSES.items():
            if key in s and label:
                return label
    page = page or {}
    if page.get("expired_notice"):
        return "expired_grace"
    if expires and expires < now:
        return "expired_grace"
    if page.get("for_sale"):
        # bought by a domainer after the business let it go (registered recently, or the business site was seen later)
        return "for_sale"
    if page.get("loads") is False or (page.get("parked") and not page.get("blocks_bots")):
        return "site_down"
    if expires and expires - now <= timedelta(days=60):
        return "expiring_soon"
    return "ok"


def read_page(status: int | None, final_url: str, html: str) -> dict[str, Any]:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html or "")
    text = re.sub(r"<[^>]+>", " ", text)[:20000]
    final = final_url.lower()
    blocked = status in (401, 403, 429, 503)          # bot protection (Cloudflare …): the site is up
    return {"loads": None if blocked else bool(status and status < 400 and len(text.split()) > 3), "status": status,
            "final_url": final_url, "blocks_bots": blocked,
            "digits": re.sub(r"\D", "", text)[:20000],
            "expired_notice": bool(EXPIRED_PAGE.search(text)),
            "for_sale": bool(FOR_SALE_PAGE.search(text)) or any(h in final for h in SALE_HOSTS),
            "parked": bool(PARKED_PAGE.search(text)) and len(text.split()) < 400}


def name_guesses(name: str, city: str | None = None, limit: int = 4) -> list[str]:
    """Likely domains for a business name: 'Ant's Junk Removal' -> antsjunkremoval.com, ants-junk-removal.com …"""
    base = re.sub(r"['’]", "", name.lower())
    base = re.sub(r"\b(llc|inc|corp|co|company|the|and|of)\b", " ", base.replace("&", " and "))
    words = re.findall(r"[a-z0-9]+", base)
    if not words:
        return []
    out = ["".join(words) + ".com", "-".join(words) + ".com"]
    if city:
        out.append("".join(words) + re.sub(r"[^a-z]", "", city.lower()) + ".com")
    out.append("".join(words) + ".net")
    return list(dict.fromkeys(d for d in out if 4 < len(d) <= 70))[:limit]


async def rdap_lookup(http, domain: str) -> tuple[bool | None, dict[str, Any]]:
    """(registered?, parsed). None = this TLD has no RDAP answer."""
    try:
        r = await http.request("GET", f"https://rdap.org/domain/{domain}", retries=1, timeout=20,
                               headers={"Accept": "application/rdap+json"})
    except Exception as exc:
        log.info("rdap failed", extra={"data": {"domain": domain, "error": type(exc).__name__}})
        return None, {}
    if r.status_code == 404:
        if not r.history:                # rdap.org itself: this extension (.us …) isn't in RDAP → ask WHOIS
            return await whois_registered(domain), {}
        return False, {}                 # the registry answered "not found" → not registered
    if r.status_code != 200:
        return None, {}
    try:
        info = parse_rdap(r.json())
    except ValueError:
        return None, {}
    if info.get("related"):                      # the registrar's record has the date the owner actually paid until
        try:
            r2 = await http.request("GET", info["related"], retries=0, timeout=15,
                                    headers={"Accept": "application/rdap+json"})
            if r2.status_code == 200:
                reg = parse_rdap(r2.json())
                info["registrar_expires"] = reg.get("expires")
                info["statuses"] = sorted(set(info["statuses"]) | set(reg.get("statuses") or []))
        except Exception:
            pass
    return True, info


WHOIS_SERVERS = {"us": "whois.nic.us", "co": "whois.registry.co", "io": "whois.nic.io", "me": "whois.nic.me",
                 "biz": "whois.nic.biz", "info": "whois.nic.info", "org": "whois.pir.org"}
NOT_FOUND = re.compile(r"no match|not found|no data found|no entries found|status:\s*free|available for registration", re.I)


async def whois_registered(domain: str, *, timeout: float = 10.0) -> bool | None:
    """Classic WHOIS (port 43) for extensions without RDAP. None when it can't be reached."""
    import asyncio

    server = WHOIS_SERVERS.get(domain.rsplit(".", 1)[-1])
    if not server:
        return None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(server, 43), timeout)
        writer.write(f"{domain}\r\n".encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.read(20000), timeout)
        writer.close()
    except Exception:
        return None
    text = data.decode("utf-8", "ignore")
    return not bool(NOT_FOUND.search(text))


BUILDER_HOSTS = ("wixsite.com", "weebly.com", "godaddysites.com", "business.site", "square.site", "squarespace.com",
                 "wordpress.com", "blogspot.com", "jimdosite.com", "site123.me", "ueniweb.com", "webflow.io",
                 "carrd.co", "mystrikingly.com", "wix.com", "yolasite.com", "hibu.site", "thryv.site", "dudaone.com",
                 "my.canva.site", "netlify.app", "vercel.app", "github.io")


def registrable(domain: str) -> str:
    """'www.shop.acme.com' -> 'acme.com' (US-focused; two-label suffixes like co.uk handled)."""
    parts = domain.lower().strip(".").split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in ("co.uk", "com.au", "co.nz", "com.mx", "co.za", "com.br"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def on_builder(domain: str) -> str | None:
    d = domain.lower()
    return next((h for h in BUILDER_HOSTS if d == h or d.endswith("." + h)), None)


async def fetch_page(http, domain: str) -> dict[str, Any]:
    for url in (f"https://{domain}/", f"http://{domain}/"):
        try:
            r = await http.request("GET", url, retries=0, timeout=15, follow_redirects=True)
            return read_page(r.status_code, str(r.url), r.text[:300000])
        except Exception:
            continue
    return {"loads": False, "status": None, "final_url": None, "expired_notice": False, "for_sale": False, "parked": False}


async def wayback(http, domain: str) -> dict[str, Any] | None:
    """Last time the Wayback Machine saw a working site (+ link), first capture year."""
    try:
        r = await http.request("GET", "https://web.archive.org/cdx/search/cdx", retries=0, timeout=25, params={
            "url": domain, "output": "json", "fl": "timestamp", "filter": "statuscode:200", "collapse": "timestamp:6"})
        rows = r.json() if r.status_code == 200 else []
    except Exception:
        return None
    stamps = [row[0] for row in rows[1:] if row]
    if not stamps:
        return {"captures": 0}
    first, last = stamps[0], stamps[-1]
    return {"captures": len(stamps), "first_year": int(first[:4]),
            "last_seen": f"{last[:4]}-{last[4:6]}-{last[6:8]}", "snapshot": f"https://web.archive.org/web/{last}/{domain}"}


async def porkbun_available(http, domain: str, key: str, secret: str) -> dict[str, Any] | None:
    if not (key and secret):
        return None
    try:
        r = await http.request("POST", f"https://api.porkbun.com/api/json/v3/domain/checkDomain/{domain}", retries=0,
                               timeout=20, json={"apikey": key, "secretapikey": secret})
        d = r.json()
    except Exception:
        return None
    resp = d.get("response") or {}
    if d.get("status") != "SUCCESS":
        return None
    return {"available": resp.get("avail") == "yes", "price": resp.get("price")}


async def check_domain(http, domain: str, *, now: datetime | None = None, use_wayback: bool = True,
                       porkbun: tuple[str, str] = ("", ""), phone: str | None = None) -> dict[str, Any]:
    now = now or datetime.utcnow()
    if host := on_builder(domain):
        return {"domain": domain, "status": "no_own_domain", "label": "No own domain", "icon": "🔗",
                "action": f"The site lives on a {host} subdomain — offer a real domain + website.", "builder": host,
                "statuses": [], "page": None, "phone_match": False}
    domain = registrable(domain)
    registered, info = await rdap_lookup(http, domain)
    page = await fetch_page(http, domain) if registered else None
    expires = info.get("registrar_expires") or info.get("expires")
    status = classify(registered=registered, statuses=info.get("statuses") or [], expires=expires,
                      registered_at=info.get("registered"), page=page, now=now)
    out: dict[str, Any] = {
        "domain": domain, "status": status, "label": LABELS[status][0], "icon": LABELS[status][1],
        "action": LABELS[status][2], "statuses": info.get("statuses") or [], "registrar": info.get("registrar"),
        "registered": info["registered"].date().isoformat() if info.get("registered") else None,
        "expires": expires.date().isoformat() if expires else None,
        "days_to_expiry": (expires - now).days if expires else None,
        "page": {k: v for k, v in (page or {}).items() if k != "digits"} or None,
    }
    from leadengine.normalize import normalize_phone
    p = normalize_phone(phone)
    out["phone_match"] = bool(p and page and p in (page.get("digits") or ""))
    if status in ("expired_grace", "redemption", "pending_delete") and expires:
        # typical ICANN timeline: ~0-45 days grace (registrar), 30 days redemption, 5 days pending delete
        out["owner_can_renew_until"] = (expires + timedelta(days=75)).date().isoformat()
        out["drops_around"] = (expires + timedelta(days=80)).date().isoformat()
    if status == "available":
        pb = await porkbun_available(http, domain, *porkbun)
        if pb is not None:
            out["confirmed_available"], out["price"] = pb["available"], pb["price"]
    if use_wayback and status != "ok":
        out["wayback"] = await wayback(http, domain)
    return out


def rescue_rank(d: dict[str, Any]) -> int:
    """Sort key: hottest first."""
    return {"expired_grace": 0, "redemption": 1, "pending_delete": 2, "available": 3, "for_sale": 4,
            "site_down": 5, "no_own_domain": 6, "expiring_soon": 6, "missing_from_listing": 7, "unknown": 8,
            "ok": 9}.get(d.get("status"), 9)


def domain_of_site(url: str | None) -> str | None:
    d = normalize_domain(url) if url else None
    return d.removeprefix("www.") if d else None


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
