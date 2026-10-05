"""Geo-grid rank tracking (like Local Falcon / BrightLocal grids), free.

Lay an N×N grid of points over an area, search Google Maps *from each point*, and record where every
business ranks there. One grid run gives the ranking of every business in the area at once.

Metrics per business:
* ``top3`` — points where it is in the top 3 (the map pack people actually see)
* ``solv`` — Share of Local Voice = top3 / points (0–100 %)
* ``avg_rank`` — average position, counting "not in the top 20" as 21
"""

from __future__ import annotations

import html
import math
from typing import Any

NOT_FOUND = 21


def grid_points(lat: float, lng: float, size: int, spacing_km: float) -> list[dict[str, Any]]:
    """``size`` x ``size`` points, ``spacing_km`` apart, centred on (lat, lng). Row 0 = north."""
    size = max(3, min(15, size | 1))                    # odd, so there is a centre point
    half = size // 2
    dlat = spacing_km / 110.574
    dlng = spacing_km / (111.320 * max(0.2, math.cos(math.radians(lat))))
    return [{"r": r, "c": c, "lat": round(lat + (half - r) * dlat, 6), "lng": round(lng + (c - half) * dlng, 6)}
            for r in range(size) for c in range(size)]


def rank_at(point: dict[str, Any], business_id: int) -> int | None:
    ids = point.get("ranks") or []
    return ids.index(business_id) + 1 if business_id in ids else None


def summarize(points: list[dict[str, Any]], names: dict[int, str]) -> list[dict[str, Any]]:
    """Leaderboard of every business seen anywhere on the grid, best Share of Local Voice first."""
    ok = [p for p in points if not p.get("error")]
    ids = {i for p in ok for i in p.get("ranks") or []}
    out = []
    for bid in ids:
        ranks = [rank_at(p, bid) for p in ok]
        top3 = sum(1 for r in ranks if r is not None and r <= 3)
        found = sum(1 for r in ranks if r is not None)
        avg = sum(r if r is not None else NOT_FOUND for r in ranks) / max(1, len(ranks))
        out.append({"business_id": bid, "name": names.get(bid, f"#{bid}"), "top3": top3, "found": found,
                    "points": len(ok), "avg_rank": round(avg, 1), "solv": round(100 * top3 / max(1, len(ok)))})
    return sorted(out, key=lambda x: (-x["solv"], x["avg_rank"]))


def color(rank: int | None) -> str:
    if rank is None:
        return "#dc2626"
    if rank <= 3:
        return "#16a34a"
    if rank <= 10:
        return "#eab308"
    return "#f97316"


# ── Web Mercator helpers for the map background ─────────────────────
def _px(lat: float, lng: float, z: int) -> tuple[float, float]:
    n = 256 * 2 ** z
    x = (lng + 180) / 360 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def svg_heatmap(points: list[dict[str, Any]], business_id: int | None, *, title: str = "", width: int = 460,
                tiles: bool = True) -> str:
    """Self-contained SVG: coloured rank circles over an OpenStreetMap background (optional)."""
    if not points:
        return ""
    lats = [p["lat"] for p in points]
    lngs = [p["lng"] for p in points]
    # zoom so the grid fills ~80 % of the width
    z = 18
    while z > 3:
        x0, y0 = _px(max(lats), min(lngs), z)
        x1, y1 = _px(min(lats), max(lngs), z)
        if max(x1 - x0, y1 - y0) <= width * 0.8:
            break
        z -= 1
    x0, y0 = _px(max(lats), min(lngs), z)
    x1, y1 = _px(min(lats), max(lngs), z)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left, top = cx - width / 2, cy - width / 2
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {width + 34}" width="100%" '
             f'role="img" aria-label="{html.escape(title or "rank heatmap")}" style="max-width:{width}px;font-family:system-ui,sans-serif">',
             f'<rect width="{width}" height="{width + 34}" fill="#ffffff"/>',
             f'<rect width="{width}" height="{width}" fill="#eef2f7"/>']
    if tiles:
        t0x, t0y = int(left // 256), int(top // 256)
        t1x, t1y = int((left + width) // 256), int((top + width) // 256)
        clip = f"mapclip{abs(hash((round(left), round(top), business_id))) % 10**8}"
        parts.append(f'<clipPath id="{clip}"><rect width="{width}" height="{width}"/></clipPath>'
                     f'<g opacity="0.85" clip-path="url(#{clip})">')
        for tx in range(t0x, t1x + 1):
            for ty in range(t0y, t1y + 1):
                parts.append(f'<image href="https://tile.openstreetmap.org/{z}/{tx}/{ty}.png" x="{tx * 256 - left:.1f}" '
                             f'y="{ty * 256 - top:.1f}" width="256" height="256"/>')
        parts.append('</g>')
    spacing = min((abs(_px(points[0]["lat"], points[0]["lng"], z)[0] - _px(points[1]["lat"], points[1]["lng"], z)[0])
                   if len(points) > 1 else 40), 60)
    r = max(9, min(18, spacing * 0.42))
    centre = points[len(points) // 2]
    for p in points:
        px, py = _px(p["lat"], p["lng"], z)
        px, py = px - left, py - top
        if p.get("error"):
            parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{r:.1f}" fill="#9ca3af" stroke="#fff" stroke-width="2"/>'
                         f'<text x="{px:.1f}" y="{py + 4:.1f}" font-size="{r * 0.8:.0f}" text-anchor="middle" fill="#fff">?</text>')
            continue
        rank = rank_at(p, business_id) if business_id is not None else None
        label = str(rank) if rank is not None else "20+"
        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{r:.1f}" fill="{color(rank)}" stroke="#fff" stroke-width="2">'
                     f'<title>{html.escape(label)} at {p["lat"]:.4f},{p["lng"]:.4f}</title></circle>'
                     f'<text x="{px:.1f}" y="{py + r * 0.32:.1f}" font-size="{r * (0.85 if len(label) < 3 else 0.62):.0f}" '
                     f'font-weight="700" text-anchor="middle" fill="#fff">{label}</text>')
    mx, my = _px(centre["lat"], centre["lng"], z)
    parts.append(f'<circle cx="{mx - left:.1f}" cy="{my - top:.1f}" r="{r + 4:.1f}" fill="none" stroke="#111827" '
                 f'stroke-width="2" stroke-dasharray="4 3"/>')
    legend = [("#16a34a", "1–3"), ("#eab308", "4–10"), ("#f97316", "11–20"), ("#dc2626", "not in top 20")]
    x = 6
    for col, text in legend:
        parts.append(f'<circle cx="{x + 6}" cy="{width + 17}" r="6" fill="{col}"/>'
                     f'<text x="{x + 16}" y="{width + 21}" font-size="11" fill="#374151">{text}</text>')
        x += 22 + len(text) * 6.5
    if tiles:
        parts.append(f'<text x="{width - 4}" y="{width - 4}" font-size="9" text-anchor="end" fill="#374151">'
                     '© OpenStreetMap contributors</text>')
    parts.append("</svg>")
    return "".join(parts)
