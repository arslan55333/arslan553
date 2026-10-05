from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from leadengine.config import HttpSettings, Settings
from leadengine.credits import CreditTracker
from leadengine.db import init_db, make_engine, make_session_factory
from leadengine.http import HttpClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    shutil.copy(PROJECT_ROOT / "config.toml", tmp_path / "config.toml")
    s = Settings.load(
        root=tmp_path,
        env={"SERPAPI_API_KEY": "test-serp-key", "GOOGLE_PLACES_API_KEY": "test-places-key"},
    )
    return replace(s, http=HttpSettings(timeout_seconds=5, max_retries=2, backoff_base_seconds=0, concurrency=3))


@pytest.fixture
def session_factory(settings: Settings):
    engine = make_engine(settings.database_url)
    init_db(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def credits(session_factory, settings) -> CreditTracker:
    return CreditTracker(session_factory, settings)


@pytest.fixture
def make_http(settings):
    """``make_http(handler)`` -> HttpClient whose requests go to ``handler(request)``."""

    def factory(handler) -> HttpClient:
        return HttpClient(settings.http, transport=httpx.MockTransport(handler), sleep=_no_sleep)

    return factory
