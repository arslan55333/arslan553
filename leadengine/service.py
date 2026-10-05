"""Search pipeline: cache check -> (geocode) -> provider -> merge into DB -> record search."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy.orm import Session, sessionmaker

from leadengine.config import Settings
from leadengine.credits import CreditTracker
from leadengine.db.models import Business
from leadengine.db.repo import Repository
from leadengine.errors import ProviderError, ProviderNotConfigured
from leadengine.geo.geocode import geocode_zip
from leadengine.http import HttpClient
from leadengine.log import get_logger
from leadengine.models import SearchQuery
from leadengine.providers import Provider, build_provider

log = get_logger("service")


@dataclass
class SearchOutcome:
    search_id: int
    provider: str
    from_cache: bool
    api_calls: int
    results: list[tuple[Business, int]]
    skipped: int = 0


class LeadService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        http: HttpClient,
        credits: CreditTracker | None = None,
        providers: dict[str, Provider] | None = None,
    ) -> None:
        self.settings = settings
        self._sf = session_factory
        self.http = http
        self.credits = credits
        self._providers = dict(providers or {})

    def provider(self, name: str) -> Provider:
        if name not in self._providers:
            self._providers[name] = build_provider(name, self.settings, self.http, self.credits)
        return self._providers[name]

    async def search(self, query: SearchQuery, provider_name: str | None = None, *, refresh: bool = False) -> SearchOutcome:
        """Run a search, reusing a fresh cached one when possible (0 API calls)."""
        provider = self.provider(provider_name or self.settings.default_provider)
        ready, reason = provider.configured()
        if not ready:
            raise ProviderNotConfigured(provider.name, reason)

        with self._sf() as session:
            repo = Repository(session)
            if not refresh:
                hit = repo.find_fresh_search(provider.name, query, self.settings.ttl("search"))
                if hit is not None:
                    log.info("search cache hit", extra={"data": {"search_id": hit.id, "provider": provider.name}})
                    return SearchOutcome(
                        hit.id, provider.name, True, 0, repo.search_results(hit.id, limit=query.max_results)
                    )

            if query.zip_code and not query.has_coordinates and (provider.needs_coordinates or provider.wants_coordinates):
                coords = await geocode_zip(
                    query.zip_code, http=self.http, repo=repo, settings=self.settings, credits=self.credits
                )
                session.commit()  # keep the geocode cache even if the provider call fails
                if coords:
                    query = replace(query, lat=coords[0], lng=coords[1])
            if provider.needs_coordinates and not query.has_coordinates:
                raise ProviderError(provider.name, f"could not find coordinates for {query.location_text()!r}")

            log.info("provider search", extra={"data": {"provider": provider.name, "keyword": query.keyword,
                                                         "where": query.location_text(), "max": query.max_results}})
            result = await provider.search(query)

            ranked: list[tuple[Business, int]] = []
            skipped = 0
            for i, rec in enumerate(result.records, 1):
                try:
                    with session.begin_nested():  # one bad record must not break the run
                        biz = repo.upsert_business(rec)
                    ranked.append((biz, rec.rank or i))
                except Exception:
                    skipped += 1
                    log.exception("could not store record", extra={"data": {"name": rec.name, "provider": rec.provider}})

            search = repo.record_search(
                query, provider.name, ranked, exhausted=result.exhausted, api_calls=result.api_calls
            )
            session.commit()
            log.info("search stored", extra={"data": {"search_id": search.id, "results": len(ranked),
                                                       "api_calls": result.api_calls, "skipped": skipped}})
            return SearchOutcome(
                search.id, provider.name, False, result.api_calls,
                repo.search_results(search.id), skipped,
            )
