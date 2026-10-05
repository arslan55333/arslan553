"""Adaptive grid search: run a provider over grid cells, split saturated cells, merge."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Callable

from leadengine.geo.grid import Cell
from leadengine.log import get_logger
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.providers.base import Provider

log = get_logger("discovery")


@dataclass
class GridReport:
    records: list[BusinessRecord] = field(default_factory=list)
    cells_run: int = 0
    cells_split: int = 0
    saturated_leaves: int = 0     # still saturated at max depth (area may hide more results)
    cells_failed: int = 0
    cells_skipped: int = 0        # not run because of the max_cells budget
    api_calls: int = 0
    errors: list[str] = field(default_factory=list)


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
        results = await asyncio.gather(
            *(provider.search(cell_query(base, c, cap)) for c in batch), return_exceptions=True
        )
        for cell, result in zip(batch, results):
            report.cells_run += 1
            if isinstance(result, BaseException):
                report.cells_failed += 1
                report.errors.append(f"cell {cell.key()}: {result}")
                log.warning("grid cell failed", extra={"data": {"cell": cell.key(), "error": str(result)[:200]}})
                continue
            report.api_calls += result.api_calls
            report.records.extend(result.records)
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
