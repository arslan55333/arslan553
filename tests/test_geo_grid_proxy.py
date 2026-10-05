import asyncio
import math

import pytest
from sqlalchemy import create_engine, inspect, text

from leadengine.db import init_db
from leadengine.discovery import run_adaptive_grid
from leadengine.geo.grid import Cell, plan_cells, zoom_for_span
from leadengine.geo.zipdata import haversine_km, zip_directory
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.providers.base import Provider, ProviderResult
from leadengine.proxy import ProxyPool, parse_proxy


# ── ZIP directory ────────────────────────────────────────────────────
def test_zip_directory_lookup_and_radius():
    d = zip_directory()
    assert len(d) > 33_000
    z = d.get("78245")
    assert (z.city, z.state) == ("San Antonio", "TX")
    assert z.population and z.population > 10_000
    assert z.land_sqmi == pytest.approx(33.22, rel=0.01)
    assert z.radius_km == pytest.approx(math.sqrt(33.22 * 2.589988 / math.pi), rel=0.01)
    assert d.get("00000") is None


def test_nearby_and_towns():
    d = zip_directory()
    z = d.get("10001")
    near = d.nearby(z.lat, z.lng, 2.0)
    assert near[0][0].zip == "10001" and near[0][1] == 0
    assert all(dist <= 2.0 for _, dist in near)
    towns = d.towns("90210")
    assert towns[0] == "Beverly Hills, CA"


def test_haversine():
    assert haversine_km(32.7767, -96.797, 29.4241, -98.4936) == pytest.approx(406, rel=0.02)  # Dallas -> San Antonio


# ── Grid ─────────────────────────────────────────────────────────────
def test_plan_cells_covers_circle_and_sorts_by_distance():
    cells = plan_cells(32.78, -96.80, radius_km=6, cell_km=3)
    assert 9 <= len(cells) <= 16
    assert all(c.half_km == pytest.approx(1.5) for c in cells)
    # every point of the circle edge is inside some cell
    for angle in range(0, 360, 15):
        lat = 32.78 + 6 * math.sin(math.radians(angle)) / 110.574 * 0.98
        lng = -96.80 + 6 * math.cos(math.radians(angle)) / (111.32 * math.cos(math.radians(32.78))) * 0.98
        assert any(c.bounds[0] <= lat <= c.bounds[2] and c.bounds[1] <= lng <= c.bounds[3] for c in cells)
    d = [math.hypot(c.lat - 32.78, c.lng + 96.80) for c in cells]
    assert d == sorted(d)


def test_cell_split_and_zoom():
    c = Cell(32.78, -96.80, half_km=4)
    kids = c.split()
    assert len(kids) == 4 and all(k.half_km == 2 and k.depth == 1 for k in kids)
    s, w, n, e = c.bounds
    assert all(s < k.lat < n and w < k.lng < e for k in kids)
    assert kids[0].zoom == c.zoom + 1
    assert zoom_for_span(32.78, 50) < zoom_for_span(32.78, 5) < zoom_for_span(32.78, 0.5)
    assert 11 <= zoom_for_span(0, 10_000) and zoom_for_span(0, 0.001) <= 18


class GridFake(Provider):
    """Saturated (not exhausted) when the cell is larger than 2 km; fails on one cell."""
    name = "fake"
    label = "fake"
    max_per_query = 5

    def __init__(self, fail_key=None):
        self.queries = []
        self.fail_key = fail_key

    async def search(self, q: SearchQuery) -> ProviderResult:
        self.queries.append(q)
        if self.fail_key and f"{q.lat:.5f}" == self.fail_key:
            raise RuntimeError("boom")
        half = (q.bounds[2] - q.bounds[0]) * 110.574 / 2
        recs = [BusinessRecord(name=f"b{q.lat:.4f}-{i}", provider="fake") for i in range(self.max_per_query)]
        return ProviderResult(recs, exhausted=half <= 1.01, api_calls=1)


def test_adaptive_grid_splits_saturated_cells():
    p = GridFake()
    base = SearchQuery("x", "75201")
    report = asyncio.run(run_adaptive_grid(p, base, [Cell(32.78, -96.8, 2.0)], max_depth=3, max_cells=50))
    # 2km cell saturated -> 4 x 1km cells, which are exhausted
    assert report.cells_run == 5 and report.cells_split == 1 and report.saturated_leaves == 0
    assert len(report.records) == 25 and report.api_calls == 5
    assert all(q.zoom and q.bounds and q.max_results == 5 for q in p.queries)


def test_adaptive_grid_respects_depth_budget_and_failures():
    p = GridFake()
    report = asyncio.run(run_adaptive_grid(p, SearchQuery("x"), [Cell(32.78, -96.8, 8.0)], max_depth=1, max_cells=3))
    assert report.cells_run == 3 and report.cells_skipped == 2   # budget stopped it
    p2 = GridFake(fail_key="32.78000")
    report2 = asyncio.run(run_adaptive_grid(p2, SearchQuery("x"), [Cell(32.78, -96.8, 1.0), Cell(33.0, -96.8, 1.0)]))
    assert report2.cells_failed == 1 and len(report2.records) == 5 and "boom" in report2.errors[0]


def test_grid_resumes_from_cell_checkpoints(session_factory):
    """A scan that dies half-way reuses the finished cells next time (0 provider calls for them)."""
    from datetime import datetime

    from leadengine.discovery import DbCellCache

    cells = [Cell(32.78, -96.8, 1.0), Cell(33.0, -96.8, 1.0), Cell(33.2, -96.8, 1.0)]
    first = GridFake(fail_key="33.20000")                 # last cell crashes (captcha, network...)
    cache = DbCellCache(session_factory, "fake|x", 14)
    asyncio.run(run_adaptive_grid(first, SearchQuery("x"), cells, cache=cache))
    again = GridFake()
    report = asyncio.run(run_adaptive_grid(again, SearchQuery("x"), cells, cache=cache))
    assert report.cells_cached == 2 and len(again.queries) == 1 and len(report.records) == 15
    assert report.api_calls == 1
    fresh = GridFake()
    asyncio.run(run_adaptive_grid(fresh, SearchQuery("x"), cells,
                                  cache=DbCellCache(session_factory, "fake|x", 14, read=False)))
    assert len(fresh.queries) == 3                        # --refresh ignores checkpoints
    rec = BusinessRecord(name="A", provider="p", last_review_at=datetime(2026, 1, 2), categories=["x"], raw={"big": 1})
    cache.put("k", [rec], True)
    back, exhausted = cache.get("k")
    assert back[0].last_review_at == datetime(2026, 1, 2) and back[0].raw == {} and exhausted


# ── Proxies ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("line, expected", [
    ("1.2.3.4:8080", "http://1.2.3.4:8080"),
    ("1.2.3.4:8080:user:p@ss", "http://user:p%40ss@1.2.3.4:8080"),
    ("http://u:p@proxy.io:9000", "http://u:p@proxy.io:9000"),
    ("socks5://10.0.0.1:1080", "socks5://10.0.0.1:1080"),
    ("# comment", None),
    ("garbage", None),
])
def test_parse_proxy(line, expected):
    assert parse_proxy(line) == expected


def test_proxy_rotation_and_bans(tmp_path):
    f = tmp_path / "proxies.txt"
    f.write_text("3.3.3.3:3000\n# skip\n", encoding="utf-8")
    pool = ProxyPool.from_env("1.1.1.1:1000, http://u:secret@2.2.2.2:2000", str(f), max_failures=2)
    assert len(pool) == 3
    order = [pool.next().label for _ in range(4)]
    assert order == ["1.1.1.1:1000", "2.2.2.2:2000", "3.3.3.3:3000", "1.1.1.1:1000"]
    first = pool.proxies()[0]
    pool.report(first, False, "timeout")
    assert first in pool.available()
    pool.report(first, False, "timeout")
    assert first not in pool.available()           # benched after 2 failures
    pool.ban(pool.proxies()[1], "captcha")
    assert [p.label for p in pool.available()] == ["3.3.3.3:3000"]
    second = pool.proxies()[1]
    assert second.playwright() == {"server": "http://2.2.2.2:2000", "username": "u", "password": "secret"}
    assert "secret" not in second.label


def test_empty_pool_means_direct():
    pool = ProxyPool.from_env("")
    assert not pool.enabled and pool.next() is None
    pool.report(None, False)  # no-op


# ── DB migration ─────────────────────────────────────────────────────
def test_init_db_adds_new_columns_to_old_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as c:  # a Phase 1 style table without the new columns
        c.execute(text("CREATE TABLE businesses (id INTEGER PRIMARY KEY, place_id VARCHAR(255), name VARCHAR(500), "
                       "categories JSON, first_seen DATETIME, last_seen DATETIME, updated_at DATETIME)"))
        c.execute(text("INSERT INTO businesses (id, name) VALUES (1, 'Old Co')"))
    added = init_db(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("businesses")}
    assert {"data_id", "owner_response_rate", "recent_review_dates"} <= cols
    assert "businesses.data_id" in added
    assert init_db(engine) == []  # idempotent
    with engine.connect() as c:
        assert c.execute(text("SELECT name FROM businesses")).scalar() == "Old Co"
