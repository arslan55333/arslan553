"""Adaptive grid search: run a provider over grid cells, split saturated cells, merge."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Protocol

from leadengine.geo.grid import Cell
from leadengine.log import get_logger
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.providers.base import Provider, ProviderResult

log = get_logger("discovery")


@dataclass
class GridReport:
    records: list[BusinessRecord] = field(default_factory=list)
    cells_run: int = 0
    cells_split: int = 0
    saturated_leaves: int = 0     # still saturated at max depth (area may hide more results)
    cells_failed: int = 0
    cells_skipped: int = 0        # not run because of the max_cells budget
    cells_cached: int = 0         # reused from an earlier (interrupted) run
    api_calls: int = 0
    errors: list[str] = field(default_factory=list)


class CellCache(Protocol):
    def get(self, key: str) -> tuple[list[BusinessRecord], bool] | None: ...
    def put(self, key: str, records: list[BusinessRecord], exhausted: bool) -> None: ...


def record_to_json(rec: BusinessRecord) -> dict[str, Any]:
    d = {k: v for k, v in vars(rec).items() if k != "raw"}
    if isinstance(d.get("last_review_at"), datetime):
        d["last_review_at"] = d["last_review_at"].isoformat()
    return d


def record_from_json(d: dict[str, Any]) -> BusinessRecord:
    d = dict(d)
    if d.get("last_review_at"):
        d["last_review_at"] = datetime.fromisoformat(d["last_review_at"])
    known = BusinessRecord.__dataclass_fields__
    return BusinessRecord(**{k: v for k, v in d.items() if k in known})


def cell_query(base: SearchQuery, cell: Cell, max_results: int) -> SearchQuery:
    return replace(
        base, lat=cell.lat, lng=cell.lng, zoom=cell.zoom, bounds=cell.bounds,
        radius_m=cell.radius_m, max_results=max_results,
    )


async def run_adaptive_grid(
    provider: Provider,
    base: SearchQuery,
    cells: list[Cell],
    *,
    max_depth: int = 2,
    max_cells: int = 40,
    concurrency: int = 3,
    on_cell: Callable[[Cell, int, bool], None] | None = None,
    cache: CellCache | None = None,
    on_records: Callable[[list[BusinessRecord]], None] | None = None,
) -> GridReport:
    """Breadth-first over cells. A cell whose result is not exhausted (hit the
    provider's cap) is split into 4 children until ``max_depth``."""
    report = GridReport()
    queue: deque[Cell] = deque(cells)
    seen: set[str] = set()
    cap = provider.max_per_query

    while queue and report.cells_run < max_cells:
        batch: list[Cell] = []
        while queue and len(batch) < concurrency and report.cells_run + len(batch) < max_cells:
            cell = queue.popleft()
            if cell.key() not in seen:
                seen.add(cell.key())
                batch.append(cell)
        if not batch:
            break
        async def run_cell(c: Cell) -> ProviderResult:
            if cache is not None:
                hit = cache.get(c.key())
                if hit is not None:
                    report.cells_cached += 1
                    return ProviderResult(records=hit[0], exhausted=hit[1], api_calls=0)
            res = await provider.search(cell_query(base, c, cap))
            if cache is not None and res.records:           # never checkpoint an empty (possibly blocked) page
                cache.put(c.key(), res.records, res.exhausted)
            return res

        results = await asyncio.gather(*(run_cell(c) for c in batch), return_exceptions=True)
        for cell, result in zip(batch, results):
            report.cells_run += 1
            if isinstance(result, BaseException):
                report.cells_failed += 1
                report.errors.append(f"cell {cell.key()}: {result}")
                log.warning("grid cell failed", extra={"data": {"cell": cell.key(), "error": str(result)[:200]}})
                continue
            report.api_calls += result.api_calls
            report.records.extend(result.records)
            if on_records and result.records:
                try:
                    on_records(result.records)
                except Exception:   # live display must never break the scan
                    log.debug("on_records callback failed", exc_info=True)
            saturated = not result.exhausted
            if saturated and cell.depth < max_depth:
                queue.extend(cell.split())
                report.cells_split += 1
            elif saturated:
                report.saturated_leaves += 1
            if on_cell:
                on_cell(cell, len(result.records), saturated)
            log.info("grid cell done", extra={"data": {
                "cell": cell.key(), "depth": cell.depth, "zoom": cell.zoom,
                "results": len(result.records), "saturated": saturated}})
    report.cells_skipped = len(queue)
    return report


class DbCellCache:
    """Grid-cell checkpoint in the database (``grid_cell_cache``)."""

    def __init__(self, sf, prefix: str, ttl_days: float, *, read: bool = True) -> None:
        self.sf, self.prefix, self.ttl_days, self.read = sf, prefix, ttl_days, read

    def get(self, key: str) -> tuple[list[BusinessRecord], bool] | None:
        from datetime import timedelta

        from leadengine.db.models import GridCellCache, utcnow

        if not self.read:
            return None
        with self.sf() as s:
            row = s.get(GridCellCache, f"{self.prefix}|{key}")
            if row is None or row.fetched_at < utcnow() - timedelta(days=self.ttl_days):
                return None
            try:
                return [record_from_json(d) for d in row.records or []], bool(row.exhausted)
            except (TypeError, ValueError):
                return None

    def put(self, key: str, records: list[BusinessRecord], exhausted: bool) -> None:
        from leadengine.db.models import GridCellCache, utcnow

        try:
            with self.sf() as s:
                row = s.get(GridCellCache, f"{self.prefix}|{key}") or GridCellCache(key=f"{self.prefix}|{key}")
                row.records, row.exhausted, row.fetched_at = [record_to_json(r) for r in records], exhausted, utcnow()
                s.add(row)
                s.commit()
        except Exception:      # a checkpoint must never break the scan
            log.exception("could not save grid cell checkpoint", extra={"data": {"cell": key}})
