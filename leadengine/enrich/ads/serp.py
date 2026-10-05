"""Live Google search check for "keyword + city": who runs Search ads and Local Services Ads.

Two sources:
* ``playwright`` (free): a localised Google results page in the headless browser
  (uule location parameter, proxies supported); ads parsed from the DOM.
* ``serpapi`` (paid, 1 credit per query): SerpAPI's Google engine (``ads`` /
  ``local_ads`` blocks).
"""

from __future__ import annotations

import base64
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

from leadengine.errors import ProviderBlocked, ProviderError
from leadengine.log import get_logger
from leadengine.normalize import normalize_domain, normalize_phone

log = get_logger("ads.serp")

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}
_UULE_KEYS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


@dataclass
class SerpAd:
    kind: str                  # search | lsa
    title: str
    domain: str | None = None
    phone: str | None = None
    badge: str | None = None   # Google Guaranteed / Google Screened
    position: int | None = None


@dataclass
class SerpSnapshot:
    keyword: str
    location: str
    provider: str
    ads: list[SerpAd] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"keyword": self.keyword, "location": self.location, "provider": self.provider,
                "error": self.error, "ads": [asdict(a) for a in self.ads]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SerpSnapshot":
        return cls(d["keyword"], d["location"], d["provider"], [SerpAd(**a) for a in d.get("ads", [])], d.get("error"))


def canonical_location(city: str, state: str) -> str:
    return f"{city},{STATE_NAMES.get(state.upper(), state)},United States"


def uule(canonical: str) -> str:
    """Google's location parameter for localised results."""
    raw = canonical.encode()
    return "w+CAIQICI" + _UULE_KEYS[len(raw) % len(_UULE_KEYS)] + base64.b64encode(raw).decode()


def ad_landing_domain(href: str | None) -> str | None:
    """Ad click URLs wrap the real landing page (adurl=...)."""
    if not href:
        return None
    parsed = urlparse(href)
    qs = parse_qs(parsed.query)
    for key in ("adurl", "url", "q"):
        if qs.get(key) and qs[key][0].startswith("http"):
            return normalize_domain(qs[key][0])
    host = normalize_domain(href)
    return None if host and host.endswith(("google.com", "googleadservices.com", "doubleclick.net")) else host


# ── free: headless browser ───────────────────────────────────────────
SERP_JS = r"""
() => {
  const ads = [];
  let pos = 0;
  for (const el of document.querySelectorAll('div[data-text-ad], [data-text-ad="1"]')) {
    pos++;
    const a = el.querySelector('a[href]');
    const title = el.querySelector('[role="heading"], h3');
    ads.push({kind: 'search', title: title ? title.innerText : (el.innerText || '').split('\n')[0],
              dtld: (el.querySelector('[data-dtld]') || el).getAttribute('data-dtld'),
              href: a ? a.href : null, text: (el.innerText || '').slice(0, 400), position: pos});
  }
  // Local Services Ads: each provider card has a name and a "Google Guaranteed/Screened" badge
  let i = 0;
  const seen = new Set();
  for (const h of document.querySelectorAll('[data-lsa-name], [role="heading"]')) {
    if (h.closest('[data-text-ad]')) continue;
    let card = h.closest('[data-lsa-card]');
    if (!card) {
      let el = h.parentElement;
      for (let k = 0; k < 4 && el; k++, el = el.parentElement) {
        if (el.querySelectorAll('[role="heading"], [data-lsa-name]').length > 1) break;  // left the card
        if (/Google (Guaranteed|Screened)/.test(el.innerText || '')) { card = el; break; }
      }
    }
    if (!card) continue;
    const t = card.innerText || '';
    const badge = (t.match(/Google (Guaranteed|Screened)/) || [null])[0];
    const name = (h.getAttribute('data-lsa-name') || h.innerText || '').trim();
    if (!badge || !name || seen.has(name)) continue;
    seen.add(name);
    ads.push({kind: 'lsa', title: name, badge, text: t.slice(0, 300), position: ++i});
  }
  return ads;
}
"""


def _phone_in(text: str | None) -> str | None:
    m = re.search(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", text or "")
    return m.group(0) if m else None


def ads_from_dom(raw: list[dict[str, Any]]) -> list[SerpAd]:
    out: list[SerpAd] = []
    seen = set()
    for a in raw:
        title = (a.get("title") or "").strip()
        if not title:
            continue
        domain = normalize_domain(a["dtld"]) if a.get("dtld") else ad_landing_domain(a.get("href"))
        key = (a["kind"], title.lower(), domain)
        if key in seen:
            continue
        seen.add(key)
        out.append(SerpAd(a["kind"], title[:200], domain, _phone_in(a.get("text")), a.get("badge"), a.get("position")))
    return out


async def serp_browser(provider, keyword: str, city: str, state: str, base_url: str = "https://www.google.com") -> SerpSnapshot:
    """Google results page in the Playwright provider's browser (same proxies / stealth / blocking)."""
    loc = canonical_location(city, state)
    url = f"{base_url}/search?q={quote_plus(keyword + ' ' + city)}&hl=en&gl=us&pws=0&uule={quote_plus(uule(loc))}"
    snap = SerpSnapshot(keyword, f"{city}, {state}", "playwright")
    proxy = provider.proxies.next()
    ctx = await provider._new_context(proxy, None)
    try:
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        await provider._check_blocked(page)
        await provider._accept_consent(page)
        await page.wait_for_timeout(1500)
        snap.ads = ads_from_dom(await page.evaluate(SERP_JS))
        provider.proxies.report(proxy, True)
    except ProviderBlocked:
        provider.proxies.ban(proxy, "captcha")
        snap.error = "blocked by Google (captcha)"
    except Exception as exc:
        provider.proxies.report(proxy, False, type(exc).__name__)
        snap.error = f"{type(exc).__name__}: {str(exc)[:120]}"
    finally:
        await ctx.close()
    return snap


# ── paid: SerpAPI ────────────────────────────────────────────────────
def ads_from_serpapi(data: dict[str, Any]) -> list[SerpAd]:
    out: list[SerpAd] = []
    for i, ad in enumerate(data.get("ads") or [], 1):
        out.append(SerpAd("search", ad.get("title", "")[:200],
                          normalize_domain(ad.get("displayed_link") or "") or ad_landing_domain(ad.get("link")),
                          ad.get("phone"), None, ad.get("position", i)))
    local = data.get("local_ads") or data.get("local_services_ads") or {}
    items = local.get("ads") if isinstance(local, dict) else local
    for i, ad in enumerate(items or [], 1):
        badge = ad.get("badge") or ("Google Guaranteed" if ad.get("google_guaranteed") else None)
        out.append(SerpAd("lsa", ad.get("title", "")[:200], normalize_domain(ad.get("link") or "") if ad.get("link")
                          and "google." not in ad.get("link", "") else None, ad.get("phone"), badge, i))
    return [a for a in out if a.title]


async def serp_serpapi(provider, keyword: str, city: str, state: str) -> SerpSnapshot:
    snap = SerpSnapshot(keyword, f"{city}, {state}", "serpapi")
    r = await provider.http.request("GET", "https://serpapi.com/search.json", params={
        "engine": "google", "q": f"{keyword} {city}", "location": canonical_location(city, state).replace(",", ", "),
        "gl": "us", "hl": "en", "api_key": provider.settings.serpapi_api_key})
    data = provider._json(r)
    if data.get("error") and "hasn't returned any results" not in data["error"]:
        provider._record("google_search", success=False, note=data["error"][:200])
        raise ProviderError("serpapi", data["error"])
    provider._record("google_search")
    snap.ads = ads_from_serpapi(data)
    return snap


# ── matching ads to a business ───────────────────────────────────────
_STOP = {"the", "and", "of", "llc", "inc", "co", "company", "services", "service", "&", "-", "|"}


def _tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in _STOP}


def name_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def match_ads(ads: list[SerpAd], *, name: str, domain: str | None, phone: str | None) -> list[SerpAd]:
    phone_n = normalize_phone(phone)
    hits = []
    for ad in ads:
        if domain and ad.domain and (ad.domain == domain or ad.domain.endswith("." + domain)):
            hits.append(ad)
        elif phone_n and ad.phone and normalize_phone(ad.phone) == phone_n:
            hits.append(ad)
        elif name and name_similarity(name, ad.title) >= 0.8 and len(_tokens(name)) >= 2:
            hits.append(ad)
    return hits
