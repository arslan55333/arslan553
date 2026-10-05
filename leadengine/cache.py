"""Generic "look in the DB first" helper for enrichments.

Every later phase (emails, website score, ads ...) calls :func:`cached_enrichment`
so a fresh result is reused and no API/website call is made twice.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from leadengine.db.models import utcnow
from leadengine.db.repo import Repository
from leadengine.log import get_logger

log = get_logger("cache")


def is_fresh(fetched_at: datetime | None, ttl_days: float, now: datetime | None = None) -> bool:
    if fetched_at is None:
        return False
    return fetched_at >= (now or utcnow()) - timedelta(days=ttl_days)


async def cached_enrichment(
    repo: Repository,
    business_id: int,
    kind: str,
    ttl_days: float,
    compute: Callable[[], Awaitable[Any]],
    *,
    refresh: bool = False,
    source: str | None = None,
) -> tuple[Any, bool]:
    """Return ``(payload, from_cache)``. Runs ``compute`` only when nothing fresh is stored."""
    if not refresh:
        hit = repo.latest_enrichment(business_id, kind)
        if hit is not None:
            log.debug("cache hit", extra={"data": {"business_id": business_id, "kind": kind}})
            return hit.payload, True
    payload = await compute()
    repo.set_enrichment(business_id, kind, payload, ttl_days=ttl_days, source=source)
    return payload, False
