"""Legacy Selenium Google Maps scraper, ported from LeadHunter Pro v3.

Kept so the old free source still works behind the common interface; Phase 2
replaces it with an async Playwright engine. Changes vs v3:

* collects every place link first, then opens each one (no click-by-index drift)
* dedupes by Google place id / CID from the URL instead of by name
* captures place_id, lat/lng and the Maps URL
* uses Selenium Manager (built into Selenium >= 4.6) instead of webdriver-manager
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from leadengine.errors import ProviderError
from leadengine.log import get_logger
from leadengine.models import BusinessRecord, SearchQuery, to_float, to_int
from leadengine.providers.base import Provider, ProviderResult

log = get_logger("providers.selenium")

_PLACE_ID_RE = re.compile(r"!19s(ChIJ[\w-]+)")
_DATA_ID_RE = re.compile(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", re.I)
_LATLNG_RE = re.compile(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)")
_CONSENT_SELECTORS = (
    "button[jsname='higCR']", "#L2AGLb", "button[aria-label*='Accept all']",
    "button[aria-label*='Reject all']", "form[action*='consent'] button",
)
_STEALTH_JS = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
window.chrome={runtime:{}};
"""


def parse_place_url(url: str) -> dict[str, Any]:
    """Extract place_id, data_id (CID) and coordinates from a Google Maps place URL."""
    out: dict[str, Any] = {"place_id": None, "data_id": None, "lat": None, "lng": None}
    if not url:
        return out
    text = unquote(url)
    if m := _PLACE_ID_RE.search(text):
        out["place_id"] = m.group(1)
    if m := _DATA_ID_RE.search(text):
        out["data_id"] = m.group(1).lower()
    if m := _LATLNG_RE.search(text):
        out["lat"], out["lng"] = float(m.group(1)), float(m.group(2))
    return out


def unwrap_google_redirect(url: str | None) -> str | None:
    """``https://www.google.com/url?q=https://site.com&...`` -> ``https://site.com``."""
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        target = parse_qs(parsed.query).get("q", [None])[0]
        return target or url
    return url


class SeleniumMapsProvider(Provider):
    name = "selenium"
    label = "Selenium browser (free, legacy)"
    wants_coordinates = True

    def configured(self) -> tuple[bool, str]:
        try:
            import selenium  # noqa: F401
        except ImportError:
            return False, "pip install selenium (and have Google Chrome installed)"
        return True, "ready (needs Chrome)"

    async def search(self, query: SearchQuery) -> ProviderResult:
        ok, why = self.configured()
        if not ok:
            raise ProviderError(self.name, why)
        records = await asyncio.to_thread(self._scrape, query)
        self._record("maps_page", units=1)
        return ProviderResult(records, len(records) < query.max_results, 1)

    # ── blocking browser code (runs in a worker thread) ──────────────
    def _driver(self):
        from selenium import webdriver

        opts = webdriver.ChromeOptions()
        for arg in (
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled",
            "--window-size=1440,900", "--lang=en-US", "--log-level=3", "--disable-notifications",
        ):
            opts.add_argument(arg)
        opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        if self.config.extra.get("headless", True):
            opts.add_argument("--headless=new")
        driver = webdriver.Chrome(options=opts)
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _STEALTH_JS})
        return driver

    def _scrape(self, query: SearchQuery) -> list[BusinessRecord]:
        from selenium.webdriver.common.by import By

        q = quote_plus(f"{query.keyword} {query.location_text()}".strip())
        url = f"https://www.google.com/maps/search/{q}"
        if query.has_coordinates:
            url += f"/@{query.lat},{query.lng},13z"
        url += "?hl=en"

        try:
            driver = self._driver()
        except Exception as exc:  # Chrome missing, driver download blocked, ...
            raise ProviderError(self.name, f"could not start Chrome: {exc}") from exc

        records: list[BusinessRecord] = []
        try:
            driver.get(url)
            time.sleep(3)
            for sel in _CONSENT_SELECTORS:
                try:
                    driver.find_element(By.CSS_SELECTOR, sel).click()
                    time.sleep(1.5)
                    break
                except Exception:
                    continue

            links = self._collect_links(driver, query.max_results)
            log.info("selenium found place links", extra={"data": {"count": len(links)}})
            for href in links[: query.max_results]:
                try:
                    rec = self._open_place(driver, href, rank=len(records) + 1)
                except Exception as exc:  # one bad listing must not stop the run
                    log.warning("selenium listing skipped", extra={"data": {"error": str(exc)[:200]}})
                    continue
                if rec is not None:
                    records.append(rec)
                time.sleep(random.uniform(1.0, 2.5))
        finally:
            try:
                driver.quit()
            except Exception:
                pass
        return records

    def _collect_links(self, driver, max_results: int) -> list[str]:
        from selenium.webdriver.common.by import By

        scroll_js = """
        var feed = document.querySelector('div[role="feed"]');
        if (feed) { feed.scrollTop = feed.scrollHeight; return true; } return false;
        """
        links: dict[str, str] = {}
        stale = 0
        while stale < 6 and len(links) < max_results:
            before = len(links)
            for a in driver.find_elements(By.CSS_SELECTOR, "a[href*='/maps/place/']"):
                href = a.get_attribute("href") or ""
                ids = parse_place_url(href)
                key = ids["place_id"] or ids["data_id"] or href.split("?")[0]
                links.setdefault(key, href)
            stale = stale + 1 if len(links) == before else 0
            try:
                driver.execute_script(scroll_js)
                body = driver.find_element(By.TAG_NAME, "body").text.lower()
                if "reached the end of the list" in body:
                    break
            except Exception:
                pass
            time.sleep(random.uniform(1.2, 2.0))
        return list(links.values())

    def _open_place(self, driver, href: str, rank: int) -> BusinessRecord | None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        driver.get(href)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "h1")))
        time.sleep(1.0)

        def first(selectors, attr=None) -> str:
            for sel in selectors:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    value = el.get_attribute(attr) if attr else el.text
                    if value and value.strip():
                        return value.strip()
                except Exception:
                    continue
            return ""

        name = first(["h1.DUwDvf", "h1[class*='fontHeadline']", "h1"])
        if not name:
            return None

        rating = first(["div.F7nice span[aria-hidden='true']", "span.MW4etd"])
        reviews = None
        for el in driver.find_elements(By.CSS_SELECTOR, "div.F7nice span[aria-label], button[aria-label*='review']"):
            label = el.get_attribute("aria-label") or el.text or ""
            if m := re.search(r"([\d,]+)\s*review", label, re.I):
                reviews = to_int(m.group(1))
                break

        phone = ""
        phone_id = first(["button[data-item-id^='phone:tel:']"], "data-item-id")
        if phone_id:
            phone = phone_id.split("phone:tel:", 1)[-1]
        phone = phone or first(["a[href^='tel:']"], "href").replace("tel:", "")

        website = unwrap_google_redirect(first(["a[data-item-id='authority']", "a[aria-label^='Website']"], "href"))
        address = first(["button[data-item-id='address'] .Io6YTe", "button[data-item-id*='address']"])
        category = first(["button.DkEaL", "span.DkEaL"])

        ids = parse_place_url(driver.current_url) | {
            k: v for k, v in parse_place_url(href).items() if v is not None
        }
        return BusinessRecord(
            name=name,
            provider=self.name,
            provider_id=ids["data_id"] or ids["place_id"],
            place_id=ids["place_id"],
            phone=phone or None,
            website=website or None,
            address=address or None,
            lat=ids["lat"],
            lng=ids["lng"],
            rating=to_float(rating.replace(",", ".")) if rating else None,
            review_count=reviews,
            categories=[category] if category else [],
            google_maps_url=href,
            rank=rank,
            raw={"href": href},
        )
