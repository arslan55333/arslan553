import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from leadengine.config import ProviderSettings
from leadengine.db import Business, Repository
from leadengine.db.models import RankGrid
from leadengine.geo.rankgrid import grid_points, rank_at, summarize, svg_heatmap
from leadengine.models import BusinessRecord
from leadengine.service import LeadService
from tests.fake_maps import FakeMaps

pytest.importorskip("playwright.async_api")


def test_grid_points_are_centered_and_spaced():
    pts = grid_points(40.75, -73.99, 7, 1.0)
    assert len(pts) == 49 and pts[24]["lat"] == 40.75 and pts[24]["lng"] == -73.99
    assert pts[0]["lat"] > pts[-1]["lat"] and pts[0]["lng"] < pts[-1]["lng"]          # row 0 = north-west
    assert abs((pts[1]["lng"] - pts[0]["lng"]) * 111.32 * 0.7581 - 1.0) < 0.02         # ~1 km apart
    assert len(grid_points(40.75, -73.99, 6, 1.0)) == 49                              # sizes are made odd


def test_summary_share_of_local_voice_and_svg():
    pts = [{"r": 0, "c": i, "lat": 40.75, "lng": -73.99 + i / 100, "ranks": ranks}
           for i, ranks in enumerate([[1, 2, 3], [2, 1], [3, 9, 8, 7, 1], [5]])]
    pts.append({"r": 1, "c": 0, "lat": 40.74, "lng": -73.99, "error": "boom"})
    board = summarize(pts, {1: "Acme", 2: "Bravo"})
    acme = next(r for r in board if r["business_id"] == 1)
    assert acme["points"] == 4 and acme["top3"] == 2 and acme["found"] == 3 and acme["solv"] == 50
    assert acme["avg_rank"] == round((1 + 2 + 5 + 21) / 4, 1) and board[0]["solv"] >= board[-1]["solv"]
    assert rank_at(pts[2], 1) == 5 and rank_at(pts[3], 1) is None
    svg = svg_heatmap(pts, 1, title="x")
    assert svg.startswith("<svg") and "tile.openstreetmap.org" in svg and ">20+<" in svg and ">5<" in svg
    assert "#16a34a" in svg and "#dc2626" in svg and ">?<" in svg
    assert "tile.openstreetmap" not in svg_heatmap(pts, 1, tiles=False)


@pytest.fixture
def fake_maps():
    with FakeMaps() as fm:
        yield fm


def test_rank_grid_end_to_end(settings, session_factory, make_http, fake_maps):
    extra = {"base_url": fake_maps.base_url, "scroll_pause_s": 0.05, "stale_scrolls": 2, "contexts": 2,
             "details": "none", "max_attempts": 1, "timeout_ms": 10000}
    st = replace(settings, providers={**settings.providers, "playwright": ProviderSettings(extra=extra)},
                 sections={**settings.sections, "rank": {"size": 3, "depth": 10, "concurrency": 2, "map_tiles": False}})
    import httpx

    async def go():
        async with make_http(lambda r: httpx.Response(404)) as http:
            svc = LeadService(st, session_factory, http)
            try:
                return await svc.rank_grid("dumpster rental", zip_code="75201", size=3)
            finally:
                await svc.aclose()

    out = asyncio.run(go())
    assert out["points"] == 9 and out["failed"] == 0 and out["businesses"] >= 3
    assert sum(1 for r in fake_maps.requests if r.startswith("/maps/search/")) == 9
    with session_factory() as s:
        grid = s.get(RankGrid, out["grid_id"])
        assert len(grid.points) == 9 and all(p["ranks"] for p in grid.points)
        # the fake map returns different businesses around each point: 3 in the top 3 at each of 9 points
        assert sum(r["top3"] for r in grid.summary) == 27 and all(r["points"] == 9 for r in grid.summary)
        leader = grid.summary[0]
        assert leader["solv"] == round(100 * leader["top3"] / 9)
        enr = Repository(s).latest_enrichment(leader["business_id"], "rank", fresh_only=False).payload
        assert enr["grid_id"] == grid.id and enr["solv"] == leader["solv"]


def test_rank_pages(settings):
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        sf = app.state.sf
        with sf() as s:
            b = Repository(s).upsert_business(BusinessRecord(name="Acme", provider="t", place_id="A", lat=40.75, lng=-73.99))
            s.flush()
            pts = [dict(p, ranks=[b.id] if i % 2 else []) for i, p in enumerate(grid_points(40.75, -73.99, 3, 1.0))]
            board = summarize(pts, {b.id: "Acme"})
            g = RankGrid(keyword="dumpster rental", label="10001", center_lat=40.75, center_lng=-73.99, size=3,
                         spacing_km=1, points=pts, summary=board)
            s.add(g)
            s.flush()
            Repository(s).set_enrichment(b.id, "rank", {**board[0], "grid_id": g.id, "keyword": "dumpster rental"})
            s.commit()
            bid, gid = b.id, g.id
        page = c.get(f"/rank/{gid}").text
        assert "Who owns this area" in page and "<svg" in page and "Acme" in page
        lead = c.get(f"/leads/{bid}").text
        assert "Google Maps visibility" in lead and "Rank map around this business" in lead and "<svg" in lead
        assert "Past rank maps" in c.get("/rank").text
        r = c.post("/rank", data={"keyword": "dumpster rental", "business_id": str(bid), "size": "5"}, follow_redirects=False)
        assert r.status_code == 303 and "Rank heatmap" in c.get(r.headers["location"]).text
        assert c.post("/rank", data={"keyword": "x", "zip": "abc"}).status_code == 400
