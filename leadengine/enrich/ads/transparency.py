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
