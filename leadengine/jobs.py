"""Background job queue stored in the database.

Jobs survive crashes: a ``running`` job whose heartbeat is older than ``stale_after``
is put back in the queue, and multi-step jobs (one ZIP per step) skip the steps they
already finished. Every expensive step is cached anyway, so a resumed run does not
pay twice.
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from leadengine.db.models import Job, utcnow
from leadengine.log import get_logger

log = get_logger("jobs")

MAX_LOG_LINES = 300

# kind -> async handler(ctx) ; ctx has .params, .log(msg), .step_done(key), .is_done(key), .cancelled()
Handler = Callable[["JobContext"], Awaitable[Any]]


class JobContext:
    def __init__(self, sf: sessionmaker[Session], job_id: int, params: dict[str, Any]) -> None:
        self._sf = sf
        self.job_id = job_id
        self.params = params

    def log(self, message: str) -> None:
        with self._sf() as s:
            job = s.get(Job, self.job_id)
            lines = list(job.progress or [])
            lines.append(f"{utcnow():%H:%M:%S} {message}")
            job.progress = lines[-MAX_LOG_LINES:]
            job.heartbeat_at = utcnow()
            s.commit()

    def is_done(self, key: str) -> bool:
        with self._sf() as s:
            return key in ((s.get(Job, self.job_id).done_steps) or [])

    def step_done(self, key: str) -> None:
        with self._sf() as s:
            job = s.get(Job, self.job_id)
            job.done_steps = list(job.done_steps or []) + [key]
            job.heartbeat_at = utcnow()
            s.commit()

    def cancelled(self) -> bool:
        with self._sf() as s:
            return s.get(Job, self.job_id).status == "cancelled"


def enqueue(sf: sessionmaker[Session], kind: str, params: dict[str, Any]) -> int:
    with sf() as s:
        job = Job(kind=kind, params=params, status="queued", progress=[], done_steps=[], attempts=0)
        s.add(job)
        s.commit()
        return job.id


def cancel(sf: sessionmaker[Session], job_id: int) -> None:
    with sf() as s:
        job = s.get(Job, job_id)
        if job and job.status in ("queued", "running"):
            job.status = "cancelled"
            job.finished_at = utcnow()
            s.commit()


def recover_stale(sf: sessionmaker[Session], stale_after: timedelta = timedelta(minutes=10)) -> list[int]:
    """Requeue jobs that were running when the process died."""
    cutoff = utcnow() - stale_after
    with sf() as s:
        stale = list(s.scalars(select(Job).where(Job.status == "running",
                                                 (Job.heartbeat_at < cutoff) | Job.heartbeat_at.is_(None))))
        for job in stale:
            job.status = "queued"
            job.progress = list(job.progress or []) + [f"{utcnow():%H:%M:%S} resumed after interruption"]
        s.commit()
        return [j.id for j in stale]


class JobRunner:
    """Runs queued jobs one at a time (scraping in parallel would just trigger captchas)."""

    def __init__(self, sf: sessionmaker[Session], handlers: dict[str, Handler], *, max_attempts: int = 3,
                 poll_seconds: float = 2.0) -> None:
        self._sf = sf
        self.handlers = handlers
        self.max_attempts = max_attempts
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def _claim(self) -> tuple[int, str, dict] | None:
        with self._sf() as s:
            job = s.scalar(select(Job).where(Job.status == "queued").order_by(Job.created_at, Job.id).limit(1))
            if job is None:
                return None
            job.status = "running"
            job.started_at = job.started_at or utcnow()
            job.heartbeat_at = utcnow()
            job.attempts = (job.attempts or 0) + 1
            s.commit()
            return job.id, job.kind, dict(job.params or {})

    async def run_once(self) -> bool:
        """Run the next queued job. Returns False when the queue is empty."""
        claimed = self._claim()
        if claimed is None:
            return False
        job_id, kind, params = claimed
        ctx = JobContext(self._sf, job_id, params)
        handler = self.handlers.get(kind)
        try:
            if handler is None:
                raise RuntimeError(f"no handler for job kind {kind!r}")
            result = await handler(ctx)
            final, error = "done", None
        except Exception as exc:  # job failures are recorded, never crash the runner
            log.exception("job failed", extra={"data": {"job": job_id}})
            result, error = None, f"{type(exc).__name__}: {exc}"
            ctx.log(f"ERROR {error}")
            with self._sf() as s:
                attempts = s.get(Job, job_id).attempts or 0
            final = "queued" if attempts < self.max_attempts and not isinstance(exc, (ValueError, KeyError)) else "failed"
            if final == "queued":
                ctx.log(f"will retry (attempt {attempts + 1} of {self.max_attempts})")
                log.debug(traceback.format_exc())
        with self._sf() as s:
            job = s.get(Job, job_id)
            if job.status != "cancelled":
                job.status = final
                job.result = result
                job.error = error
                if final in ("done", "failed"):
                    job.finished_at = utcnow()
            s.commit()
        return True

    async def run_forever(self) -> None:
        recover_stale(self._sf)
        while not self._stop.is_set():
            try:
                busy = await self.run_once()
            except Exception:  # pragma: no cover - defensive
                log.exception("runner loop error")
                busy = False
            if not busy:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
                except asyncio.TimeoutError:
                    pass
