"""Pipelines.

* ``search``   — one provider query: cache check -> (geocode) -> provider -> merge -> record.
* ``discover`` — hybrid ZIP coverage: free provider over an adaptive grid (+ optional
  town searches) -> merge -> shortlist -> activity signals (free) -> paid gap-filling
  only for shortlisted leads still missing data. Everything is cached.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from leadengine.cache import cached_enrichment
from leadengine.config import Settings
from leadengine.credits import CreditTracker
from leadengine.db.models import Business
from leadengine.db.repo import Repository
from leadengine.discovery import GridReport, run_adaptive_grid
from leadengine.errors import LeadEngineError, ProviderError, ProviderNotConfigured
from leadengine.geo.geocode import geocode_zip
from leadengine.geo.grid import plan_cells
from leadengine.geo.zipdata import DEFAULT_RADIUS_KM, haversine_km, zip_directory
from leadengine.http import HttpClient
from leadengine.log import get_logger
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.providers import Provider, build_provider
from leadengine.providers.playwright_maps import PlaywrightMapsProvider, activity_payload
from leadengine.providers.serpapi import SerpApiProvider
from leadengine.proxy import ProxyPool

log = get_logger("service")


@dataclass
class SearchOutcome:
    search_id: int
    provider: str
    from_cache: bool
    api_calls: int
    results: list[tuple[Business, int]]
    skipped: int = 0


@dataclass
class EmailRunSummary:
    checked: int = 0          # websites crawled now
    cached: int = 0           # results reused from cache
    with_email: int = 0       # businesses with at least one found (not guessed) email
    valid: int = 0            # best email verified deliverable (smtp/reacher)
    guesses_only: int = 0     # nothing found, only pattern guesses
    no_website: int = 0
    unreachable: int = 0
    rows: list[dict] = field(default_factory=list)   # per business, for display


@dataclass
class DiscoverOutcome:
    search_id: int
    provider: str
    zip_code: str
    from_cache: bool
    results: list[tuple[Business, int]]
    in_area: int = 0
    sponsored: int = 0
    grid: GridReport | None = None
    towns: list[str] = field(default_factory=list)
    shortlisted: int = 0
    activity_checked: int = 0
    activity_cached: int = 0
    filled: int = 0
    fill_cached: int = 0
    paid_calls: int = 0
    emails: EmailRunSummary | None = None
    skipped: int = 0


class LeadService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        http: HttpClient,
        credits: CreditTracker | None = None,
        providers: dict[str, Provider] | None = None,
        proxies: ProxyPool | None = None,
    ) -> None:
        self.settings = settings
        self._sf = session_factory
        self.http = http
        self.credits = credits
        self._providers = dict(providers or {})
        proxy_cfg = settings.section("proxy")
        self.proxies = proxies or ProxyPool.from_env(
            settings.proxy_list, settings.proxy_file,
            max_failures=int(proxy_cfg.get("max_failures", 3)),
            ban_seconds=float(proxy_cfg.get("ban_seconds", 600)),
        )

    def provider(self, name: str) -> Provider:
        if name not in self._providers:
            self._providers[name] = build_provider(name, self.settings, self.http, self.credits, self.proxies)
        return self._providers[name]

    def _ready(self, name: str) -> Provider:
        provider = self.provider(name)
        ok, reason = provider.configured()
        if not ok:
            raise ProviderNotConfigured(provider.name, reason)
        return provider

    async def aclose(self) -> None:
        for provider in self._providers.values():
            try:
                await provider.aclose()
            except Exception:  # pragma: no cover - best effort cleanup
                log.exception("provider close failed")

    # ── storage helper ───────────────────────────────────────────────
    def _store(self, session: Session, repo: Repository, records: list[BusinessRecord],
               keyword: str, zip_code: str | None) -> tuple[list[tuple[Business, int]], int]:
        ranked: list[tuple[Business, int]] = []
        skipped = 0
        for i, rec in enumerate(records, 1):
            try:
                with session.begin_nested():  # one bad record must not break the run
                    biz = repo.upsert_business(rec)
                    if rec.sponsored:
                        repo.set_enrichment(biz.id, "maps_sponsored",
                                            {"keyword": keyword, "zip": zip_code, "provider": rec.provider},
                                            ttl_days=self.settings.ttl("ads"), source=rec.provider)
                ranked.append((biz, rec.rank or i))
            except Exception:
                skipped += 1
                log.exception("could not store record", extra={"data": {"name": rec.name, "provider": rec.provider}})
        return ranked, skipped

    # ── single search ────────────────────────────────────────────────
    async def search(self, query: SearchQuery, provider_name: str | None = None, *, refresh: bool = False) -> SearchOutcome:
        """Run a search, reusing a fresh cached one when possible (0 API calls)."""
        provider = self._ready(provider_name or self.settings.default_provider)

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
            ranked, skipped = self._store(session, repo, result.records, query.keyword, query.zip_code)
            search = repo.record_search(
                query, provider.name, ranked, exhausted=result.exhausted, api_calls=result.api_calls
            )
            session.commit()
            log.info("search stored", extra={"data": {"search_id": search.id, "results": len(ranked),
                                                       "api_calls": result.api_calls, "skipped": skipped}})
            return SearchOutcome(search.id, provider.name, False, result.api_calls, repo.search_results(search.id), skipped)

    # ── hybrid ZIP discovery ─────────────────────────────────────────
    async def discover(
        self,
        keyword: str,
        zip_code: str,
        *,
        provider_name: str | None = None,
        refresh: bool = False,
        towns: bool | None = None,
        activity: bool | None = None,
        fill: bool | None = None,
        emails: bool | None = None,
        cell_km: float | None = None,
        max_depth: int | None = None,
        max_cells: int | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> DiscoverOutcome:
        cfg = self.settings.section("discovery")
        provider = self._ready(provider_name or cfg.get("provider", "playwright"))
        say = on_progress or (lambda _msg: None)
        towns = cfg.get("towns", False) if towns is None else towns
        activity = cfg.get("activity", True) if activity is None else activity
        fill = cfg.get("fill_missing", True) if fill is None else fill
        emails = cfg.get("emails", True) if emails is None else emails

        info = zip_directory().get(zip_code)
        with self._sf() as session:
            repo = Repository(session)
            if info is not None:
                lat, lng, radius_km = info.lat, info.lng, info.radius_km
            else:
                coords = await geocode_zip(zip_code, http=self.http, repo=repo, settings=self.settings, credits=self.credits)
                session.commit()
                if coords is None:
                    raise LeadEngineError(f"Unknown ZIP code {zip_code}")
                (lat, lng), radius_km = coords, DEFAULT_RADIUS_KM
            area_km = radius_km * (1 + float(cfg.get("area_margin", 0.15)))
            base = SearchQuery(keyword=keyword, zip_code=zip_code, lat=lat, lng=lng, max_results=provider.max_per_query)

            outcome: DiscoverOutcome
            hit = None if refresh else repo.find_fresh_search(provider.name, base, self.settings.ttl("search"), mode="grid")
            if hit is not None:
                say(f"Using cached discovery from {hit.ran_at:%Y-%m-%d} (0 calls). Use --refresh to re-scan.")
                outcome = DiscoverOutcome(hit.id, provider.name, zip_code, True, repo.search_results(hit.id))
            else:
                cells = plan_cells(lat, lng, area_km, float(cell_km or cfg.get("initial_cell_km", 6)))
                say(f"Scanning {info.label if info else zip_code} ({area_km:.1f} km radius) with "
                    f"{len(cells)} starting cell(s) via {provider.name}...")
                report = await run_adaptive_grid(
                    provider, base, cells,
                    max_depth=int(max_depth if max_depth is not None else cfg.get("max_depth", 2)),
                    max_cells=int(max_cells or cfg.get("max_cells", 40)),
                    concurrency=int(cfg.get("concurrency", 3)),
                    on_cell=lambda c, n, sat: say(f"  cell z{c.zoom} depth {c.depth}: {n} results"
                                                  + (" (saturated -> splitting)" if sat else "")),
                )
                records = list(report.records)
                town_names: list[str] = []
                if towns:
                    town_names = zip_directory().towns(zip_code) if info else []
                    for town in town_names:
                        say(f"  town search: {keyword} in {town}")
                        try:
                            res = await provider.search(SearchQuery(keyword=keyword, location=town,
                                                                    max_results=provider.max_per_query))
                            records += res.records
                            report.api_calls += res.api_calls
                        except LeadEngineError as exc:
                            report.errors.append(f"town {town}: {exc}")
                if report.cells_run and report.cells_failed == report.cells_run:
                    raise ProviderError(provider.name, "every grid cell failed: " + "; ".join(report.errors[:3]))

                ranked, skipped = self._store(session, repo, records, keyword, zip_code)
                search = repo.record_search(
                    base, provider.name, ranked, exhausted=report.saturated_leaves == 0,
                    api_calls=report.api_calls, mode="grid", cells=report.cells_run,
                )
                session.commit()
                outcome = DiscoverOutcome(search.id, provider.name, zip_code, False, repo.search_results(search.id),
                                          grid=report, towns=town_names, skipped=skipped)

            def in_area(b: Business) -> bool:
                if b.zip_code == zip_code:
                    return True
                return b.lat is not None and b.lng is not None and haversine_km(lat, lng, b.lat, b.lng) <= area_km

            outcome.in_area = sum(1 for b, _ in outcome.results if in_area(b))
            sponsored_ids = {e.business_id for b, _ in outcome.results
                             if (e := repo.latest_enrichment(b.id, "maps_sponsored")) is not None}
            outcome.sponsored = len(sponsored_ids)

            min_reviews = int(cfg.get("shortlist_min_reviews", 20))
            min_rating = float(cfg.get("shortlist_min_rating", 4.0))
            shortlist = [b for b, _ in outcome.results if in_area(b)
                         and (b.review_count or 0) >= min_reviews and (b.rating or 0) >= min_rating]
            outcome.shortlisted = len(shortlist)
            if shortlist:
                say(f"Shortlist: {len(shortlist)} businesses with >= {min_reviews} reviews and >= {min_rating} stars")

            if activity and shortlist and isinstance(provider, PlaywrightMapsProvider):
                checked, cached = await self.enrich_activity(repo, provider, shortlist)
                outcome.activity_checked, outcome.activity_cached = checked, cached
                session.commit()
            if fill and shortlist:
                filled, cached, calls = await self.fill_missing(repo, shortlist, cfg)
                outcome.filled, outcome.fill_cached, outcome.paid_calls = filled, cached, calls
                session.commit()
            shortlist_ids = [b.id for b in shortlist]
            outcome.results = repo.search_results(outcome.search_id)
        if emails and shortlist_ids:
            say(f"Finding emails for {len(shortlist_ids)} shortlisted websites...")
            outcome.emails = await self.find_emails(shortlist_ids)
            with self._sf() as session:
                outcome.results = Repository(session).search_results(outcome.search_id)
        return outcome

    async def enrich_activity(self, repo: Repository, provider: PlaywrightMapsProvider,
                              businesses: list[Business]) -> tuple[int, int]:
        """Review recency, owner replies, claimed and photo count from each place page (cached)."""
        ttl = self.settings.ttl("activity")
        cached_hits = 0
        opened = 0

        async def one(biz: Business) -> None:
            nonlocal cached_hits, opened
            url = biz.google_maps_url or (
                f"{provider.base_url}/maps/place/data=!4m2!3m1!1s{biz.data_id}" if biz.data_id else None)
            if not url:
                return

            async def compute():
                nonlocal opened
                opened += 1
                return activity_payload(await provider.place_details(url, reviews=True, proxy=provider.proxies.next()))

            try:
                payload, hit = await cached_enrichment(repo, biz.id, "maps_activity", ttl, compute, source=provider.name)
            except Exception as exc:
                log.warning("activity check failed", extra={"data": {"name": biz.name, "error": str(exc)[:150]}})
                return
            cached_hits += hit
            apply_activity(biz, payload)
            repo.session.commit()

        sem = asyncio.Semaphore(int(provider.opt("contexts", 3)))

        async def guarded(b: Business) -> None:
            async with sem:
                await one(b)

        await asyncio.gather(*(guarded(b) for b in businesses))
        return opened, cached_hits

    async def fill_missing(self, repo: Repository, businesses: list[Business], cfg: dict) -> tuple[int, int, int]:
        """Paid lookups only for shortlisted leads that still miss phone or website (cached, capped)."""
        name = cfg.get("fill_provider", "serpapi")
        provider = self.provider(name)
        if not isinstance(provider, SerpApiProvider) or not provider.configured()[0]:
            log.info("gap filling skipped", extra={"data": {"provider": name, "reason": provider.configured()[1]}})
            return 0, 0, 0
        need = [b for b in businesses if not (b.phone and b.website) and (b.place_id or b.data_id)]
        budget = int(cfg.get("max_fill", 25))
        filled = cached = calls = 0
        for biz in need:
            if calls >= budget:
                break

            async def compute(b=biz):
                nonlocal calls
                calls += 1
                rec = await provider.place(place_id=b.place_id, data_id=b.data_id)
                return {"found": rec is not None, "record": _record_json(rec) if rec else None}

            try:
                payload, hit = await cached_enrichment(repo, biz.id, "paid_place", self.settings.ttl("paid_place"),
                                                       compute, source=name)
            except LeadEngineError as exc:
                log.warning("gap fill failed", extra={"data": {"name": biz.name, "error": str(exc)[:150]}})
                continue
            cached += hit
            if payload.get("record"):
                rec = BusinessRecord(**{**payload["record"], "provider": name})
                repo.upsert_business(rec, fill_only=True)
                repo.session.commit()
                filled += 1
        return filled, cached, calls


    # ── emails (Phase 3) ─────────────────────────────────────────────
    async def find_emails(self, business_ids: list[int], *, refresh: bool = False,
                          on_progress: Callable[[str], None] | None = None) -> EmailRunSummary:
        """Crawl each business website for emails (cached per business), store and score them."""
        from leadengine.enrich.emails import build_email_finder

        say = on_progress or (lambda _m: None)
        cfg = self.settings.section("emails")
        summary = EmailRunSummary()
        with self._sf() as session:
            repo = Repository(session)
            finder = build_email_finder(self.settings, self.http, repo)
            sem = asyncio.Semaphore(int(cfg.get("concurrency", 5)))
            businesses = [b for b in (repo.get_business(i) for i in business_ids) if b is not None]

            async def one(biz: Business) -> None:
                if not biz.website:
                    summary.no_website += 1
                    return
                async with sem:
                    async def compute():
                        say(f"  crawling {biz.website}")
                        return (await finder.find(biz.website, owner_hint=biz.owner_name)).as_dict()
                    try:
                        payload, hit = await cached_enrichment(
                            repo, biz.id, "emails", self.settings.ttl("emails"), compute, refresh=refresh,
                            source="crawler",
                            ttl_for=lambda p: self.settings.ttl("emails") if p.get("reachable") else 1.0)
                    except Exception as exc:
                        log.warning("email search failed", extra={"data": {"name": biz.name, "error": str(exc)[:150]}})
                        summary.unreachable += 1
                        return
                summary.cached += hit
                summary.checked += not hit
                self._store_emails(repo, biz, payload, summary)
                session.commit()

            try:
                await asyncio.gather(*(one(b) for b in businesses))
            finally:
                await finder.crawler.aclose()
            session.commit()
        return summary

    def _store_emails(self, repo: Repository, biz: Business, payload: dict, summary: EmailRunSummary) -> None:
        if not payload.get("reachable"):
            summary.unreachable += 1
        emails = payload.get("emails") or []
        repo.save_emails(biz.id, [{
            "email": e["email"], "source": e["source"][:255], "method": e["method"], "source_url": e["source_url"],
            "is_guess": e["is_guess"], "is_role": e["is_role"], "confidence": e["confidence"],
            "verification": (e.get("verification") or {}).get("status"),
        } for e in emails])
        repo.set_enrichment(biz.id, "site_fetch", {
            "reachable": payload.get("reachable"), "ssl_error": payload.get("ssl_error"),
            "redirected_to": payload.get("redirected_to"), "pages": payload.get("pages_crawled"),
            "facebook": payload.get("facebook_urls")}, ttl_days=self.settings.ttl("website"), source="crawler")
        best = payload.get("best")
        people = payload.get("people") or []
        if people and not biz.owner_name:
            biz.owner_name = people[0]["name"]
        if best:
            biz.best_email = best["email"]
            biz.email_confidence = best["confidence"]
            biz.email_status = (best.get("verification") or {}).get("status")
            summary.with_email += 1
            summary.valid += biz.email_status == "valid"
        elif any(e["is_guess"] for e in emails):
            summary.guesses_only += 1
        summary.rows.append({"name": biz.name, "website": biz.website, "best": best, "owner": biz.owner_name,
                             "others": [e for e in emails if not best or e["email"] != best["email"]][:3],
                             "reachable": payload.get("reachable")})


def apply_activity(biz: Business, payload: dict) -> None:
    from datetime import datetime

    if payload.get("claimed") is not None:
        biz.claimed = payload["claimed"]
    if payload.get("photo_count") is not None:
        biz.photo_count = payload["photo_count"]
    if payload.get("recent_review_dates"):
        biz.recent_review_dates = payload["recent_review_dates"]
    if payload.get("last_review_at"):
        biz.last_review_at = datetime.fromisoformat(payload["last_review_at"])
    if payload.get("owner_response_rate") is not None:
        biz.owner_response_rate = payload["owner_response_rate"]


def _record_json(rec: BusinessRecord) -> dict:
    """JSON-safe copy of a record for the enrichment cache (raw payload dropped)."""
    keep = ("name", "provider_id", "place_id", "data_id", "phone", "website", "address", "lat", "lng",
            "rating", "review_count", "categories", "hours", "google_maps_url", "business_status", "claimed")
    return {k: getattr(rec, k) for k in keep}
