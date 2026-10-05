"""Headless-browser checks: full-page screenshot, real mobile layout test, JS-reported versions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from leadengine.log import get_logger

log = get_logger("website.render")

MOBILE_JS = r"""
() => {
  const w = window.innerWidth;
  const overflow = Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0) - w;
  let small = 0, total = 0;
  for (const el of document.querySelectorAll('p, li, a, span, td')) {
    const t = (el.innerText || '').trim();
    if (t.length < 20 || el.children.length > 2) continue;
    total++;
    if (parseFloat(getComputedStyle(el).fontSize) < 12) small++;
  }
  const taps = Array.from(document.querySelectorAll('a, button')).filter(e => {
    const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.height < 24 && r.width < 24; }).length;
  return {viewport_width: w, horizontal_overflow_px: Math.max(0, overflow),
          small_text_ratio: total ? small / total : 0, tiny_tap_targets: taps};
}
"""

JS_PROPS_JS = r"""
(props) => {
  const out = {};
  for (const p of props) {
    try {
      let v = window;
      for (const part of p.split('.')) { if (v == null) break; v = v[part]; }
      if (v !== undefined && v !== null && (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean'))
        out[p] = String(v);
      else if (v !== undefined && v !== null) out[p] = '';
    } catch (e) {}
  }
  return out;
}
"""


class WebsiteRenderer:
    def __init__(self, *, headless: bool = True, timeout_ms: int = 25_000, executable_path: str = "",
                 max_height: int = 6000) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.executable_path = executable_path
        self.max_height = max_height
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        async with self._lock:
            if self._browser is None:
                from playwright.async_api import async_playwright

                self._pw = await async_playwright().start()
                args: dict[str, Any] = {"headless": self.headless}
                if self.executable_path:
                    args["executable_path"] = self.executable_path
                self._browser = await self._pw.chromium.launch(**args)
            return self._browser

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def render(self, url: str, shot_path: Path | None, js_props: list[str] | None = None,
                     ignore_https_errors: bool = True) -> dict[str, Any]:
        browser = await self._ensure()
        out: dict[str, Any] = {"ok": False}
        desktop = await browser.new_context(viewport={"width": 1366, "height": 900},
                                            ignore_https_errors=ignore_https_errors)
        try:
            page = await desktop.new_page()
            page.set_default_timeout(self.timeout_ms)
            resp = await page.goto(url, wait_until="load", timeout=self.timeout_ms)
            await page.wait_for_timeout(1200)
            out["status"] = resp.status if resp else None
            out["final_url"] = page.url
            out["ok"] = bool(resp and resp.status < 400)
            if js_props:
                out["js"] = await page.evaluate(JS_PROPS_JS, js_props)
            if shot_path is not None:
                shot_path.parent.mkdir(parents=True, exist_ok=True)
                height = await page.evaluate("() => document.documentElement.scrollHeight")
                clip = {"x": 0, "y": 0, "width": 1366, "height": min(int(height or 900), self.max_height)}
                await page.screenshot(path=str(shot_path), full_page=True, clip=clip, type="jpeg", quality=60)
                out["screenshot"] = str(shot_path)
        except Exception as exc:
            out["error"] = f"{type(exc).__name__}: {str(exc)[:150]}"
        finally:
            await desktop.close()

        mobile = await browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                                           is_mobile=True, has_touch=True, ignore_https_errors=ignore_https_errors,
                                           user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                                                      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
                                                      "Mobile/15E148 Safari/604.1")
        try:
            page = await mobile.new_page()
            page.set_default_timeout(self.timeout_ms)
            await page.goto(url, wait_until="load", timeout=self.timeout_ms)
            await page.wait_for_timeout(800)
            out["mobile"] = await page.evaluate(MOBILE_JS)
            if shot_path is not None:
                mpath = shot_path.with_name(shot_path.stem + "-mobile.jpg")
                await page.screenshot(path=str(mpath), type="jpeg", quality=60)
                out["mobile_screenshot"] = str(mpath)
        except Exception as exc:
            out.setdefault("error", f"mobile: {type(exc).__name__}")
        finally:
            await mobile.close()
        return out
