"""Who built / manages this website? (footer credits and the fingerprints of big local-marketing agencies)

Useful for the pitch: a business that already pays Scorpion / Hibu / Thryv … for a weak site is often unhappy
with the results; a business on a DIY builder (Wix, GoDaddy) built it itself.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

PLATFORMS: dict[str, str] = {
    "Scorpion": r"scorpion\.co\b|scorpioncms|scorpion internet",
    "Hibu": r"hibu\.com|hibusites|hibu-",
    "Thryv": r"thryv\.com|thryv\b",
    "ReachLocal / LOCALiQ": r"reachlocal|rlcdn\.com|reachedge|localiq",
    "Web.com / Yodle": r"yodle|\bweb\.com/|webcomcdn",
    "Townsquare Interactive": r"townsquareinteractive|townsquare interactive",
    "Footbridge Media": r"footbridgemedia",
    "Blue Corona": r"bluecorona",
    "SearchKings": r"searchkings",
    "Boostability": r"boostability",
    "Dex / YP Marketing": r"dexmedia|ypcdn|yp\.com/sites",
    "Hook Agency": r"hookagency",
    "Webmasters (Roofing/Plumbing/Contractor)": r"(?:roofing|plumbing|contractor|hvac)webmasters",
    "Ignite Local": r"ignitelocal",
    "Bluehost / Endurance": r"bluehost-sites",
}
BUILDERS = {"wix": "Wix", "squarespace": "Squarespace", "godaddy": "GoDaddy", "weebly": "Weebly", "wordpress": "WordPress",
            "shopify": "Shopify", "duda": "Duda", "jimdo": "Jimdo", "site123": "Site123", "ueni": "UENI",
            "google": "Google Sites", "webflow": "Webflow", "hostinger": "Hostinger", "strikingly": "Strikingly"}
CREDIT_RE = re.compile(r"\b(?:(?:website|web\s*site|site|web)\s+)?(?:design(?:ed)?|develop(?:ed|ment)|powered|built|"
                       r"created|managed|maintained|hosted|marketing|seo|website|site)(?:\s+(?:and|&)\s+\w+)?\s+by\s*"
                       r"[:\-–]?\s*([A-Z0-9][\w&.'’\- ]{1,40}?)(?=\s*(?:[|©,•·]|\.\s|\.$|$|\s{2}|\n|all rights|copyright))",
                       re.I)


def detect_agency(html: str) -> dict[str, Any] | None:
    low = html.lower()
    for name, pattern in PLATFORMS.items():
        if re.search(pattern, low):
            return {"name": name, "kind": "agency", "how": "agency platform fingerprint in the page code"}
    soup = BeautifulSoup(html, "html.parser")
    foot = soup.find("footer")
    text = (foot.get_text(" ", strip=True) if foot else soup.get_text(" ", strip=True)[-1500:])
    m = CREDIT_RE.search(text)
    if m:
        who = m.group(1).strip(" -–.")
        key = re.split(r"[^a-z0-9]+", who.lower())[0] if who else ""
        if key in BUILDERS:
            return {"name": BUILDERS[key], "kind": "builder", "how": f"footer: \"{m.group(0)[:80]}\""}
        if len(who) >= 3 and not who.lower().startswith(("the owner", "us", "our")):
            return {"name": who, "kind": "agency", "how": f"footer: \"{m.group(0)[:80]}\""}
    return None
