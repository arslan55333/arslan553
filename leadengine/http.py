"""Shared async HTTP client: timeouts, retries with exponential backoff, concurrency limit."""

from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable

import httpx

from leadengine.config import HttpSettings
from leadengine.errors import NetworkError
from leadengine.log import get_logger

log = get_logger("http")

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class HttpClient:
    """Use as ``async with HttpClient(settings.http) as http: ...``.

    ``transport`` lets tests plug in ``httpx.MockTransport``.
    """

    def __init__(
        self,
        settings: HttpSettings,
        *,
        user_agent: str = "LeadEngine/0.1",
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._sem = asyncio.Semaphore(settings.concurrency)
        self._client = httpx.AsyncClient(
            timeout=settings.timeout_seconds,
            transport=transport,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"},
        )

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _backoff(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                return min(float(retry_after), 60.0)
        base = self.settings.backoff_base_seconds
        return base * (2 ** attempt) + random.uniform(0, base / 2 if base else 0)

    async def request(self, method: str, url: str, *, retries: int | None = None, **kwargs: Any) -> httpx.Response:
        """Send a request, retrying on timeouts, connection errors, 429 and 5xx.

        The last response is returned even if its status is an error; callers decide.
        Raises :class:`NetworkError` only when no response could be obtained at all.
        """
        retries = self.settings.max_retries if retries is None else retries
        host = httpx.URL(url).host
        for attempt in range(retries + 1):
            response: httpx.Response | None = None
            try:
                async with self._sem:
                    response = await self._client.request(method, url, **kwargs)
                    if self.settings.delay_seconds:
                        await self._sleep(self.settings.delay_seconds)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= retries:
                    raise NetworkError(f"{method} {host} failed after {attempt + 1} tries: {type(exc).__name__}") from exc
                log.warning("network error, retrying", extra={"data": {"host": host, "attempt": attempt + 1, "error": type(exc).__name__}})
            else:
                if response.status_code not in RETRY_STATUS or attempt >= retries:
                    return response
                log.warning("retryable status", extra={"data": {"host": host, "status": response.status_code, "attempt": attempt + 1}})
            await self._sleep(self._backoff(attempt, response))
        raise NetworkError(f"{method} {host} failed")  # pragma: no cover - loop always returns/raises
