"""Async Playwright Google Maps scraper (free).

How one search works:
1. Open ``/maps/search/<keyword>/@lat,lng,zoom`` in a fresh browser context
   (own proxy, images/fonts/media blocked, en-US, no "HeadlessChrome" UA).
2. Listen to network responses and keep every ``/search?tbm=map`` JSON payload,
   plus the JSON embedded in ``APP_INITIALIZATION_STATE``.
3. Scroll the results feed until enough results, the end of the list, or no progress.
4. Read the result cards from the DOM (order, "Sponsored" label, fallback fields).
5. Merge JSON + DOM by Google data_id. Optionally open place pages for missing
   fields, review dates, owner responses, claim status and photo count.

Several searches run in parallel, each in its own context (``contexts`` option).
"""

from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

from leadengine.db.models import utcnow
from leadengine.errors import ProviderBlocked, ProviderError
from leadengine.log import get_logger
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.providers.base import Provider, ProviderResult
from leadengine.providers.maps_parser import (
    businesses_from_payload,
    is_blocked_page,
    loads_maps_json,
    parse_card,
    parse_place_dom,
    parse_place_url,
)
from leadengine.proxy import Proxy

log = get_logger("providers.playwright")

DEFAULT_BLOCK = ("image", "media", "font")
BLOCK_URL_PARTS = ("doubleclick.net", "googlesyndication", "google-analytics", "googletagmanager", "/gen_204", "/log?")
END_OF_LIST_RE = re.compile(r"reached the end of the list", re.I)

CARDS_JS = r"""
() => {
  const feed = document.querySelector('div[role="feed"]');
  if (!feed) return [];
  const out = [], seen = new Set();
  for (const a of feed.querySelectorAll('a[href*="/maps/place/"]')) {
    if (seen.has(a.href)) continue; seen.add(a.href);
    let card = a.parentElement;
    for (let el = a; el && el !== feed; el = el.parentElement) { if (el.parentElement === feed) { card = el; break; } }
    const star = card.querySelector('[role="img"][aria-label*="star" i]');
    const site = card.querySelector('a[data-value="Website"], a[aria-label*="website" i]');
    const text = card.innerText || '';
    out.push({
      href: a.href, name: a.getAttribute('aria-label') || '', text,
      rating_label: star ? star.getAttribute('aria-label') : null,
      website: site ? site.href : null,
      sponsored: /(^|\n)\s*Sponsored\s*(\n|$)/.test(text) || !!card.querySelector('[aria-label="Sponsored"]'),
    });
  }
  return out;
}
"""

SCROLL_JS = r"""
() => {
  const feed = document.querySelector('div[role="feed"]');
  if (!feed) return {feed: false, end: false, count: 0};
  feed.scrollBy(0, feed.scrollHeight);
  return {
    feed: true,
    end: /reached the end of the list/i.test(feed.innerText),
    count: feed.querySelectorAll('a[href*="/maps/place/"]').length,
  };
}
"""

INITIAL_STATE_JS = r"""
() => {
  const out = [];
  const walk = (n, d) => {
    if (d > 8 || n == null) return;
    if (typeof n === 'string') { if (n.startsWith(")]}'")) out.push(n); return; }
    if (Array.isArray(n)) for (const c of n) walk(c, d + 1);
  };
  try { walk(window.APP_INITIALIZATION_STATE, 0); } catch (e) {}
  return out;
}
"""

PLACE_JS = r"""
() => {
  const main = document.querySelector('div[role="main"]') || document.body;
  const q = (s) => main.querySelector(s);
  const txt = (s) => { const e = q(s); return e ? (e.innerText || '').trim() : null; };
  const attr = (s, a) => { const e = q(s); return e ? e.getAttribute(a) : null; };
  const star = q('div.F7nice span[aria-label*="star" i], span[role="img"][aria-label*="star" i]');
  const rev = q('div.F7nice span[aria-label*="review" i], button[aria-label*="reviews" i]');
  const claim = Array.from(main.querySelectorAll('a, button')).some(
    e => /claim this business|own this business\?/i.test((e.innerText || '') + ' ' + (e.getAttribute('aria-label') || '')));
  const photos = Array.from(main.querySelectorAll('button, div')).map(e => e.getAttribute('aria-label') || '')
    .find(l => /\d[\d,]*\s+photos?/i.test(l)) || ((main.innerText || '').match(/[\d,]+\s+photos?\b/i) || [''])[0];
  const addr = attr('button[data-item-id="address"]', 'aria-label');
  return {
    name: txt('h1'),
    rating_label: [star && star.getAttribute('aria-label'), rev && (rev.getAttribute('aria-label') || rev.innerText)].filter(Boolean).join(' '),
    rating_text: txt('div.F7nice span[aria-hidden="true"]'),
    category: txt('button[jsaction*="category"]') || txt('button.DkEaL'),
    address: addr ? addr.replace(/^Address:\s*/i, '').trim() : txt('button[data-item-id="address"]'),
    website: attr('a[data-item-id="authority"]', 'href'),
    phone_id: attr('button[data-item-id^="phone:tel:"]', 'data-item-id'),
    hours_label: attr('[data-hide-tooltip-on-mouse-move][aria-label], div[aria-label*="hours" i]', 'aria-label'),
    claim_link: claim,
    photos_text: photos,
  };
}
"""

REVIEWS_JS = r"""
(limit) => {
  const out = [], seen = new Set();
  for (const card of document.querySelectorAll('div[data-review-id]')) {
    const id = card.getAttribute('data-review-id');
    if (seen.has(id) || card.parentElement.closest('div[data-review-id]')) continue;
    seen.add(id);
    const t = card.innerText || '';
    const dateEl = card.querySelector('.rsqaWe, .xRkPPb');
    const m = t.match(/(?:a|an|\d+)\s+(?:second|minute|hour|day|week|month|year)s?\s+ago/i);
    const stars = card.querySelector('[role="img"][aria-label*="star" i]');
    const textEl = card.querySelector('.wiI7pd, [data-review-text]');
    const author = card.querySelector('.d4r55, [data-review-author]');
    out.push({
      date: dateEl ? dateEl.innerText : (m ? m[0] : null),
      owner_response: !!card.querySelector('.CDe7pd') || /Response from the owner/i.test(t),
      rating: stars ? parseFloat((stars.getAttribute('aria-label') || '').replace(',', '.')) : null,
      text: textEl ? textEl.innerText.slice(0, 400) : null,
      author: author ? author.innerText.trim().slice(0, 60) : null,
    });
    if (out.length >= limit) break;
  }
  return out;
}
"""

SCROLL_REVIEWS_JS = r"""
() => {
  const first = document.querySelector('div[data-review-id]');
  let el = first;
  while (el && el.scrollHeight <= el.clientHeight + 5) el = el.parentElement;
  if (el) el.scrollBy(0, el.scrollHeight);
  return !!el;
}
"""


@dataclass
class _Captured:
    payloads: list[Any]


class PlaywrightMapsProvider(Provider):
    name = "playwright"
    label = "Playwright browser (free, fast)"
    wants_coordinates = True
    max_per_query = 120

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pw = None
        self._browser = None
        self._start_lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(self.opt("contexts", 3))

    # ── options ──────────────────────────────────────────────────────
    def opt(self, key: str, default: Any) -> Any:
        return self.config.extra.get(key, default)

    @property
    def base_url(self) -> str:
        return str(self.opt("base_url", "https://www.google.com")).rstrip("/")

    def configured(self) -> tuple[bool, str]:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False, "pip install playwright  then  python -m playwright install chromium"
        return True, "ready"

    # ── browser lifecycle ────────────────────────────────────────────
    async def _ensure_browser(self):
        async with self._start_lock:
            if self._browser is not None:
                return self._browser
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            launch: dict[str, Any] = {
                "headless": self.opt("headless", True),
                "args": ["--disable-blink-features=AutomationControlled", "--lang=en-US"],
            }
            if self.opt("executable_path", ""):
                launch["executable_path"] = self.opt("executable_path", "")
            if self.opt("channel", ""):
                launch["channel"] = self.opt("channel", "")
            if self.proxies.enabled:
                launch["proxy"] = {"server": "http://per-context"}  # each context sets its own
            try:
                self._browser = await self._pw.chromium.launch(**launch)
            except Exception as exc:
                await self._pw.stop()
                self._pw = None
                raise ProviderError(self.name, f"could not start Chromium ({exc}). "
                                               "Run: python -m playwright install chromium") from exc
            return self._browser

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def _new_context(self, proxy: Proxy | None, query: SearchQuery | None):
        browser = await self._ensure_browser()
        major = (browser.version or "141").split(".")[0]
        ctx_args: dict[str, Any] = {
            "locale": "en-US",
            "viewport": {"width": 1366, "height": 900},
            "user_agent": (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"),
        }
        if proxy is not None:
            ctx_args["proxy"] = proxy.playwright()
        if query is not None and query.has_coordinates:
            ctx_args["geolocation"] = {"latitude": query.lat, "longitude": query.lng}
            ctx_args["permissions"] = ["geolocation"]
        ctx = await browser.new_context(**ctx_args)
        ctx.set_default_timeout(int(self.opt("timeout_ms", 20_000)))
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        if "google." in self.base_url:
            await ctx.add_cookies([
                {"name": "CONSENT", "value": "YES+cb", "domain": ".google.com", "path": "/"},
                {"name": "SOCS", "value": "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg", "domain": ".google.com", "path": "/"},
            ])
        blocked_types = set(self.opt("block_resources", list(DEFAULT_BLOCK)))

        async def route(r):
            req = r.request
            if req.resource_type in blocked_types or any(p in req.url for p in BLOCK_URL_PARTS):
                await r.abort()
            else:
                await r.continue_()

        await ctx.route("**/*", route)
        return ctx

    # ── search ───────────────────────────────────────────────────────
    def search_url(self, query: SearchQuery) -> str:
        # In a grid cell the map viewport defines the area, so the text is just the keyword.
        text = query.keyword if query.zoom else f"{query.keyword} {query.location_text()}".strip()
        url = f"{self.base_url}/maps/search/{quote(text)}"
        if query.has_coordinates:
            url += f"/@{query.lat:.6f},{query.lng:.6f},{query.zoom or 13}z"
        return url + "?hl=en&gl=us"

    async def search(self, query: SearchQuery) -> ProviderResult:
        attempts = max(1, int(self.opt("max_attempts", 3)))
        last_error: Exception | None = None
        for attempt in range(attempts):
            proxy = self.proxies.next()
            try:
                async with self._slots:
                    result = await self._search_once(query, proxy)
                self.proxies.report(proxy, True)
                self._record("maps_search")
                return result
            except ProviderBlocked as exc:
                self._record("maps_search", success=False, note="blocked")
                self.proxies.ban(proxy, "captcha")
                last_error = exc
                if not self.proxies.available():
                    raise
            except ProviderError:
                raise
            except Exception as exc:  # timeouts, navigation errors, closed pages
                self._record("maps_search", success=False, note=type(exc).__name__)
                self.proxies.report(proxy, False, type(exc).__name__)
                last_error = exc
                log.warning("maps search attempt failed", extra={"data": {"attempt": attempt + 1, "error": str(exc)[:200]}})
            await asyncio.sleep(random.uniform(1.0, 3.0) * (attempt + 1))
        raise ProviderError(self.name, f"search failed after {attempts} attempts: {last_error}")

    async def _search_once(self, query: SearchQuery, proxy: Proxy | None) -> ProviderResult:
        ctx = await self._new_context(proxy, query)
        captured = _Captured(payloads=[])
        pending: list[asyncio.Task] = []

        async def grab(response) -> None:
            try:
                body = await response.text()
            except Exception:
                return
            data = loads_maps_json(body)
            if data is not None:
                captured.payloads.append(data)

        def on_response(response) -> None:
            url = response.url
            if ("/search?" in url and "tbm=map" in url) or "/maps/preview/place" in url:
                pending.append(asyncio.create_task(grab(response)))

        try:
            page = await ctx.new_page()
            page.on("response", on_response)
            await page.goto(self.search_url(query), wait_until="domcontentloaded")
            await self._check_blocked(page)
            await self._accept_consent(page)

            if "/maps/place/" in page.url:  # query matched a single business
                await page.wait_for_selector("h1", timeout=10_000)
                records = await self._single_place(page)
                return ProviderResult(records, True, 1)

            try:
                await page.wait_for_selector('div[role="feed"]', timeout=15_000)
            except Exception:
                await self._check_blocked(page)
                return ProviderResult([], True, 1)  # no results for this cell

            stop_reason = await self._scroll_feed(page, query.max_results)
            for s in await page.evaluate(INITIAL_STATE_JS):
                if (data := loads_maps_json(s)) is not None:
                    captured.payloads.append(data)
            cards = await page.evaluate(CARDS_JS)
            if pending:
                await asyncio.wait(pending, timeout=5)

            json_items: dict[str, dict] = {}
            for payload in captured.payloads:
                for item in businesses_from_payload(payload):
                    json_items.setdefault(item["data_id"].lower(), item)

            records = self._merge(json_items, cards)
            log.info("maps search parsed", extra={"data": {
                "cards": len(cards), "json": len(json_items), "records": len(records), "stop": stop_reason}})
        finally:
            await ctx.close()

        if self.opt("details", "missing") != "none":
            await self.fill_details(records, only_missing=self.opt("details", "missing") == "missing",
                                    reviews=False)
        # "end" = Google said no more results. "stale" with a short list = nothing more to load.
        # "max" (we stopped at max_results) or a long stale list = there may be more (saturated).
        exhausted = stop_reason == "end" or (stop_reason == "stale" and len(records) < int(self.max_per_query * 0.8))
        return ProviderResult(records[: query.max_results], exhausted, 1)

    async def _check_blocked(self, page) -> None:
        try:
            text = await page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 3000)")
        except Exception:
            text = ""
        if is_blocked_page(page.url, text):
            raise ProviderBlocked(self.name, "Google is showing a captcha (unusual traffic). Slow down or add proxies.")

    async def _accept_consent(self, page) -> None:
        if "consent." not in page.url:
            return
        for sel in ('button[aria-label*="Accept all"]', "form[action*='consent'] button", "#L2AGLb"):
            try:
                await page.click(sel, timeout=3000)
                await page.wait_for_load_state("domcontentloaded")
                return
            except Exception:
                continue

    async def _scroll_feed(self, page, max_results: int) -> str:
        """Scroll the results list. Returns why it stopped: "end", "max" or "stale"."""
        stale, last = 0, -1
        pause = float(self.opt("scroll_pause_s", 1.2))
        for _ in range(int(self.opt("max_scrolls", 40))):
            state = await page.evaluate(SCROLL_JS)
            if state["end"]:
                return "end"
            if state["count"] >= max_results:
                return "max"
            stale = stale + 1 if state["count"] == last else 0
            last = state["count"]
            if stale >= int(self.opt("stale_scrolls", 4)):
                return "stale"
            await page.wait_for_timeout(int(pause * 1000 * random.uniform(0.8, 1.3)))
        return "stale"

    def _merge(self, json_items: dict[str, dict], raw_cards: list[dict]) -> list[BusinessRecord]:
        """DOM order is the ranking users see; JSON fills the fields."""
        by_name = {j["name"].lower(): j for j in json_items.values()}
        records: dict[str, BusinessRecord] = {}
        for card_raw in raw_cards:
            card = parse_card(card_raw)
            key = (card["data_id"] or "").lower()
            item = json_items.get(key) or by_name.get(card["name"].lower())
            rec = self._to_record(item, card, rank=len(records) + 1)
            if rec is None:
                continue
            rid = (rec.data_id or rec.place_id or rec.name).lower()
            if rid in records:
                records[rid].sponsored = records[rid].sponsored or rec.sponsored
                continue
            records[rid] = rec
        for key, item in json_items.items():  # in network data but never rendered
            if key not in records:
                rec = self._to_record(item, None, rank=len(records) + 1)
                if rec is not None:
                    records[key] = rec
        return list(records.values())

    def _to_record(self, item: dict | None, card: dict | None, rank: int) -> BusinessRecord | None:
        item, card = item or {}, card or {}
        name = item.get("name") or card.get("name")
        if not name:
            return None
        data_id = (item.get("data_id") or card.get("data_id") or "").lower() or None
        place_id = item.get("place_id") or card.get("place_id")
        url = card.get("href") or (f"{self.base_url}/maps/place/data=!4m2!3m1!1s{data_id}" if data_id else None)
        categories = item.get("categories") or ([card["category"]] if card.get("category") else [])
        return BusinessRecord(
            name=name,
            provider=self.name,
            provider_id=data_id or place_id,
            place_id=place_id,
            data_id=data_id,
            phone=item.get("phone") or card.get("phone"),
            website=item.get("website") or card.get("website"),
            address=item.get("address"),
            lat=item.get("lat") if item.get("lat") is not None else card.get("lat"),
            lng=item.get("lng") if item.get("lng") is not None else card.get("lng"),
            rating=item.get("rating") if item.get("rating") is not None else card.get("rating"),
            review_count=item.get("review_count") if item.get("review_count") is not None else card.get("review_count"),
            categories=categories,
            hours=item.get("hours"),
            google_maps_url=url,
            sponsored=bool(card.get("sponsored")),
            rank=rank,
            raw={"json": {k: v for k, v in item.items() if k != "hours"}, "card": {k: v for k, v in card.items() if k != "text"}},
        )

    async def _single_place(self, page) -> list[BusinessRecord]:
        items = []
        for s in await page.evaluate(INITIAL_STATE_JS):
            if (data := loads_maps_json(s)) is not None:
                items += businesses_from_payload(data)
        dom = parse_place_dom(await page.evaluate(PLACE_JS), utcnow())
        ids = parse_place_url(page.url)
        item = items[0] if items else {}
        card = {"name": dom["name"], "href": page.url, **ids, "category": dom["category"],
                "rating": dom["rating"], "review_count": dom["review_count"], "website": dom["website"],
                "phone": dom["phone"]}
        rec = self._to_record(item, card, rank=1)
        return [rec] if rec else []

    # ── place details (activity signals) ─────────────────────────────
    async def fill_details(self, records: list[BusinessRecord], *, only_missing: bool = True, reviews: bool = True) -> int:
        """Open place pages (in parallel) and add phone/website/claimed/photos and, with
        ``reviews=True``, newest review dates + owner-response rate. Returns pages opened."""
        todo = [r for r in records if r.google_maps_url and (
            not only_missing or not (r.phone and r.website and r.review_count is not None))]
        if not todo:
            return 0
        sem = asyncio.Semaphore(self.opt("contexts", 3))

        async def one(rec: BusinessRecord) -> None:
            async with sem:
                proxy = self.proxies.next()
                try:
                    details = await self.place_details(rec.google_maps_url, reviews=reviews, proxy=proxy)
                except ProviderBlocked:
                    self.proxies.ban(proxy, "captcha")
                    return
                except Exception as exc:
                    self.proxies.report(proxy, False, type(exc).__name__)
                    log.warning("place details failed", extra={"data": {"name": rec.name, "error": str(exc)[:150]}})
                    return
                self.proxies.report(proxy, True)
                apply_details(rec, details)

        await asyncio.gather(*(one(r) for r in todo))
        return len(todo)

    async def place_details(self, url: str, *, reviews: bool = True, proxy: Proxy | None = None,
                            review_limit: int = 20) -> dict[str, Any]:
        ctx = await self._new_context(proxy, None)
        try:
            page = await ctx.new_page()
            sep = "&" if "?" in url else "?"
            await page.goto(url + f"{sep}hl=en", wait_until="domcontentloaded")
            await self._check_blocked(page)
            await self._accept_consent(page)
            await page.wait_for_selector("h1", timeout=12_000)
            await page.wait_for_timeout(800)
            dom = await page.evaluate(PLACE_JS)
            items: list[dict] = []
            for s in await page.evaluate(INITIAL_STATE_JS):
                if (data := loads_maps_json(s)) is not None:
                    items += businesses_from_payload(data)
            dom["reviews"] = await self._newest_reviews(page, review_limit) if reviews else []
            parsed = parse_place_dom(dom, utcnow())
            parsed["top_reviews"] = top_reviews(parsed.pop("reviews_sample", []))
            parsed["json"] = items[0] if items else {}
            parsed["url_ids"] = parse_place_url(page.url)
            return parsed
        finally:
            await ctx.close()

    async def _newest_reviews(self, page, limit: int) -> list[dict]:
        try:
            await page.get_by_role("tab", name=re.compile(r"^Reviews", re.I)).first.click(timeout=5000)
            await page.wait_for_timeout(1200)
            try:
                await page.locator('button[aria-label*="Sort reviews" i], button[aria-label="Sort"]').first.click(timeout=4000)
                await page.get_by_role("menuitemradio", name=re.compile("Newest", re.I)).first.click(timeout=4000)
                await page.wait_for_timeout(1500)
            except Exception:
                log.debug("review sort menu not found; using default order")
            for _ in range(3):
                await page.evaluate(SCROLL_REVIEWS_JS)
                await page.wait_for_timeout(700)
            return await page.evaluate(REVIEWS_JS, limit)
        except Exception as exc:
            log.debug("reviews tab not available", extra={"data": {"error": str(exc)[:120]}})
            return []


def apply_details(rec: BusinessRecord, details: dict[str, Any]) -> None:
    """Fill a record with place-page data; existing values win except activity signals."""
    j = details.get("json") or {}
    for field, value in (
        ("phone", details.get("phone") or j.get("phone")),
        ("website", details.get("website") or j.get("website")),
        ("address", details.get("address") or j.get("address")),
        ("rating", details.get("rating") if details.get("rating") is not None else j.get("rating")),
        ("review_count", details.get("review_count") if details.get("review_count") is not None else j.get("review_count")),
        ("place_id", j.get("place_id") or (details.get("url_ids") or {}).get("place_id")),
    ):
        if value is not None and getattr(rec, field) in (None, ""):
            setattr(rec, field, value)
    if not rec.categories and details.get("category"):
        rec.categories = [details["category"]]
    if rec.hours is None and j.get("hours"):
        rec.hours = j["hours"]
    for field in ("claimed", "photo_count", "last_review_at", "recent_review_dates", "owner_response_rate"):
        if details.get(field) is not None:
            setattr(rec, field, details[field])


def top_reviews(reviews: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Best short testimonials: 4-5 stars, with text, author shortened to first name + initial."""
    good = [r for r in reviews or [] if r.get("text") and (r.get("rating") or 5) >= 4 and len(r["text"]) >= 30]
    good.sort(key=lambda r: (-(r.get("rating") or 5), -min(len(r["text"]), 260)))
    out = []
    for r in good[:limit]:
        parts = (r.get("author") or "").split()
        name = f"{parts[0]} {parts[-1][0]}." if len(parts) >= 2 else (parts[0] if parts else "Google reviewer")
        out.append({"text": r["text"].strip(), "rating": r.get("rating") or 5, "author": name})
    return out


def activity_payload(details: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe subset stored as the ``maps_activity`` enrichment."""
    last: datetime | None = details.get("last_review_at")
    return {
        "top_reviews": details.get("top_reviews") or [],
        "claimed": details.get("claimed"),
        "photo_count": details.get("photo_count"),
        "recent_review_dates": details.get("recent_review_dates"),
        "last_review_at": last.isoformat() if last else None,
        "owner_response_rate": details.get("owner_response_rate"),
        "review_count": details.get("review_count"),
        "rating": details.get("rating"),
    }
