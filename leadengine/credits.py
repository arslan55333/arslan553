"""API credit / usage tracker. Every external call is recorded in ``api_usage``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from leadengine.config import Settings
from leadengine.db.models import ApiUsage, utcnow


@dataclass
class UsageSummary:
    provider: str
    calls_month: int
    failed_month: int
    calls_total: int
    est_cost_month_usd: float
    est_cost_total_usd: float
    monthly_free_calls: int

    @property
    def free_remaining(self) -> int | None:
        if not self.monthly_free_calls:
            return None
        return max(0, self.monthly_free_calls - self.calls_month)


class CreditTracker:
    def __init__(self, session_factory: sessionmaker[Session], settings: Settings) -> None:
        self._sf = session_factory
        self._settings = settings

    def record(
        self,
        provider: str,
        endpoint: str,
        *,
        units: int = 1,
        success: bool = True,
        note: str | None = None,
    ) -> None:
        """Log one call. Failed calls are stored but cost nothing."""
        price = self._settings.provider(provider).cost_per_call_usd
        with self._sf() as s:
            s.add(ApiUsage(
                provider=provider,
                endpoint=endpoint,
                units=units,
                cost_usd=units * price if success else 0.0,
                success=success,
                note=note,
            ))
            s.commit()

    def summary(self, now: datetime | None = None) -> list[UsageSummary]:
        now = now or utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        in_month = ApiUsage.at >= month_start
        ok_units = case((ApiUsage.success, ApiUsage.units), else_=0)
        stmt = (
            select(
                ApiUsage.provider,
                func.sum(case((in_month, ok_units), else_=0)),
                func.sum(case((in_month & ~ApiUsage.success, 1), else_=0)),
                func.sum(ok_units),
                func.sum(case((in_month, ApiUsage.cost_usd), else_=0.0)),
                func.sum(ApiUsage.cost_usd),
            )
            .group_by(ApiUsage.provider)
            .order_by(ApiUsage.provider)
        )
        with self._sf() as s:
            rows = s.execute(stmt).all()
        return [
            UsageSummary(
                provider=p,
                calls_month=int(cm or 0),
                failed_month=int(fm or 0),
                calls_total=int(ct or 0),
                est_cost_month_usd=round(float(cost_m or 0), 4),
                est_cost_total_usd=round(float(cost_t or 0), 4),
                monthly_free_calls=self._settings.provider(p).monthly_free_calls,
            )
            for p, cm, fm, ct, cost_m, cost_t in rows
        ]
