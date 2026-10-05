"""Common provider interface. The rest of the engine only talks to :class:`Provider`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from leadengine.config import ProviderSettings, Settings
from leadengine.credits import CreditTracker
from leadengine.errors import ProviderError
from leadengine.http import HttpClient
from leadengine.models import BusinessRecord, SearchQuery


@dataclass
class ProviderResult:
    records: list[BusinessRecord]
    exhausted: bool  # True when the provider has no more results for this query
    api_calls: int


class Provider(ABC):
    name: ClassVar[str]
    label: ClassVar[str]
    paid: ClassVar[bool] = False
    needs_coordinates: ClassVar[bool] = False   # cannot search without lat/lng
    wants_coordinates: ClassVar[bool] = False   # works better with lat/lng

    def __init__(self, settings: Settings, http: HttpClient, credits: CreditTracker | None = None) -> None:
        self.settings = settings
        self.http = http
        self.credits = credits

    @property
    def config(self) -> ProviderSettings:
        return self.settings.provider(self.name)

    def configured(self) -> tuple[bool, str]:
        """``(ready, reason)`` — e.g. ``(False, "SERPAPI_API_KEY missing in .env")``."""
        return True, "ready"

    @abstractmethod
    async def search(self, query: SearchQuery) -> ProviderResult:
        """Find businesses for ``query``. Must not raise for an empty result."""

    def _record(self, endpoint: str, *, units: int = 1, success: bool = True, note: str | None = None) -> None:
        if self.credits is not None:
            self.credits.record(self.name, endpoint, units=units, success=success, note=note)

    def _json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(self.name, f"non-JSON response (HTTP {response.status_code})") from exc
