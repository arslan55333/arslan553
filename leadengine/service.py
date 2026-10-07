"""Pipelines.

* ``search``   — one provider query: cache check -> (geocode) -> provider -> merge -> record.
* ``discover`` — hybrid ZIP coverage: free provider over an adaptive grid (+ optional
  town searches) -> merge -> shortlist -> activity signals (free) -> paid gap-filling
  only for shortlisted leads still missing data. Everything is cached.
"""

from __future__ import annotations

import re

import asyncio
from dataclasses import dataclass, field, replace
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from leadengine.cache import cached_enrichment
from leadengine.config import Settings
from leadengine.credits import CreditTracker
from leadengine.db.models import Business, utcnow
from leadengine.db.repo import Repository
from leadengine.discovery import DbCellCache, GridReport, run_adaptive_grid
from leadengine.errors import LeadEngineError, ProviderError, ProviderNotConfigured
from leadengine.geo.geocode import geocode_zip
from leadengine.geo.grid import plan_cells
from leadengine.geo.zipdata import DEFAULT_RADIUS_KM, haversine_km, zip_directory
from leadengine.http import HttpClient
from leadengine.log import get_logger
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.normalize import normalize_domain, normalize_keyword, unwrap_ad_url
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
    websites: list[dict] | None = None
    ads: list[dict] | None = None
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

    def firecrawl(self):
        """A Firecrawl client for one run (None when no key / mode off). See leadengine/enrich/firecrawl.py."""
        from leadengine.enrich.firecrawl import build_firecrawl
        return build_firecrawl(self.settings, self.http, self.credits)

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
                    click_url = None
                    real, was_ad = unwrap_ad_url(rec.website)
                    if was_ad:                    # "Website" of a sponsored listing = Google's ad click link
                        click_url, rec.website, rec.sponsored = rec.website, real, True
                    biz = repo.upsert_business(rec)
                    fill_location(biz)
                    if rec.sponsored:
                        repo.set_enrichment(biz.id, "maps_sponsored",
                                            {"keyword": keyword, "zip": zip_code, "provider": rec.provider,
                                             "click_url": click_url, "landing_url": real if was_ad else None},
                                            ttl_days=self.settings.ttl("ads"), source=rec.provider)
                        if biz.ads_status != "Active":   # a sponsored listing IS proof of ad spend
                            biz.ads_status, biz.ads_confidence = "Active", 90
                            repo.set_enrichment(biz.id, "ads", {
                                "status": "Active", "lsa": bool(biz.lsa), "confidence": 90, "meta_ads": bool(biz.meta_ads),
                                "google_ads_ids": [], "keyword": keyword,
                                "evidence": [f"sponsored (paid) listing on Google Maps for '{keyword}'"]},
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
        website: bool | None = None,
        ads: bool | None = None,
        cell_km: float | None = None,
        max_depth: int | None = None,
        max_cells: int | None = None,
        scope: str | None = None,
        on_progress: Callable[[str], None] | None = None,
        on_found: Callable[[list[dict]], None] | None = None,
    ) -> DiscoverOutcome:
        cfg = self.settings.section("discovery")
        provider = self._ready(provider_name or cfg.get("provider", "playwright"))
        say = on_progress or (lambda _msg: None)
        towns = cfg.get("towns", False) if towns is None else towns
        activity = cfg.get("activity", True) if activity is None else activity
        fill = cfg.get("fill_missing", True) if fill is None else fill
        emails = cfg.get("emails", True) if emails is None else emails
        website = cfg.get("website", True) if website is None else website
        ads = cfg.get("ads", True) if ads is None else ads

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
                    cache=DbCellCache(self._sf, f"{provider.name}|{normalize_keyword(keyword)}",
                                      self.settings.ttl("search"), read=not refresh),
                    on_records=(lambda recs: on_found([live_row_from_record(r) for r in recs])) if on_found else None,
                )
                if report.cells_cached:
                    say(f"  resumed: {report.cells_cached} cell(s) reused from the interrupted run")
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

                if not records:
                    raise ProviderError(provider.name, "Google Maps returned no businesses (blocked, captcha or "
                                                       "a changed page) - nothing was cached, try again later")
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
            area_only = str(scope or cfg.get("check_scope", "all")).lower() == "area"
            shortlist = [b for b, _ in outcome.results if (in_area(b) or not area_only)
                         and (b.review_count or 0) >= min_reviews and (b.rating or 0) >= min_rating]
            shortlist.sort(key=lambda b: -(b.review_count or 0))
            cap = int(cfg.get("max_checks", 60))
            if len(shortlist) > cap:
                say(f"  {len(shortlist)} qualify; deep-checking the {cap} best-reviewed (max_checks in config.toml)")
                shortlist = shortlist[:cap]
            outcome.shortlisted = len(shortlist)
            if shortlist:
                say(f"Shortlist: {len(shortlist)} businesses with >= {min_reviews} reviews and >= {min_rating} stars"
                    + (" inside the ZIP" if area_only else " (whole search area)"))

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
        def push(ids: list[int]) -> None:
            if on_found and ids:
                with self._sf() as s2:
                    r2 = Repository(s2)
                    on_found([live_row_from_business(b) for b in (r2.get_business(i) for i in ids) if b])

        push([b.id for b, _ in outcome.results])
        if emails and shortlist_ids:
            say(f"Finding emails for {len(shortlist_ids)} shortlisted websites...")
            outcome.emails = await self.find_emails(shortlist_ids)
            push(shortlist_ids)
        if website and shortlist_ids:
            say(f"Scoring {len(shortlist_ids)} shortlisted websites...")
            outcome.websites = await self.score_websites(shortlist_ids, on_progress=say)
            push(shortlist_ids)
            if cfg.get("seo", True):
                say("Local SEO checks...")
                await self.seo_audits(shortlist_ids, on_progress=say)
            if str(self.settings.section("firecrawl").get("mode", "smart")) == "full" and self.settings.firecrawl_api_key:
                say("Reading services / owner / years from websites (Firecrawl)...")
                await self.site_info(shortlist_ids, on_progress=say)
        if ads and shortlist_ids:
            say(f"Checking Google Ads for {len(shortlist_ids)} shortlisted businesses...")
            outcome.ads = await self.detect_ads(shortlist_ids, keyword=keyword, on_progress=say)
        scope = str(self.settings.section("reviews").get("scope", "targets"))
        if shortlist_ids and scope in ("targets", "shortlist"):
            from leadengine.scoring.targets import is_target

            self.rescore(shortlist_ids)
            with self._sf() as session:
                rv_ids = [b.id for b in (Repository(session).get_business(i) for i in shortlist_ids)
                          if b and (scope == "shortlist" or is_target(b))]
            if rv_ids:
                say(f"Reviews audit for {len(rv_ids)} business(es)...")
                await self.review_audits(rv_ids, on_progress=say)
        # Opportunity score for everything found in this run (cheap, no network)
        with self._sf() as session:
            ids = [b.id for b, _ in Repository(session).search_results(outcome.search_id)]
        self.rescore(ids)
        push(ids)
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
            finder = build_email_finder(self.settings, self.http, repo, firecrawl=self.firecrawl())
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


    # ── website score (Phase 4) ──────────────────────────────────────
    async def resolve_ad_websites(self, business_ids: list[int]) -> int:
        """Businesses whose website is a Google ad click link: follow it to the real site (cheap, once)."""
        fixed = 0
        with self._sf() as session:
            repo = Repository(session)
            todo = []
            for bid in business_ids:
                biz = repo.get_business(bid)
                if biz is None:
                    continue
                if fix_ad_website(repo, biz):
                    fixed += 1
                spon = repo.latest_enrichment(bid, "maps_sponsored", fresh_only=False)
                click = (spon.payload or {}).get("click_url") if spon else None
                if not biz.website and click:
                    todo.append((bid, click))
            session.commit()
        if self.http is None:
            return fixed
        for bid, click in todo:
            try:
                r = await self.http.request("GET", click, follow_redirects=True, retries=0, timeout=15)
                final = str(r.url)
            except Exception:
                continue
            real, _ = unwrap_ad_url(final)
            if real and "google." not in (normalize_domain(real) or "google."):
                with self._sf() as session:
                    biz = Repository(session).get_business(bid)
                    biz.website, biz.domain = real, normalize_domain(real)
                    session.commit()
                    fixed += 1
        return fixed

    async def score_websites(self, business_ids: list[int], *, refresh: bool = False, render: bool | None = None,
                             vision: bool | None = None, on_progress: Callable[[str], None] | None = None) -> list[dict]:
        """Analyse each business website (cached per business) and store the score."""
        from dataclasses import replace as dc_replace

        from leadengine.enrich.website.analyzer import WebsiteAnalyzer
        from leadengine.enrich.website.render import WebsiteRenderer
        from leadengine.llm import LLMError, build_llm

        say = on_progress or (lambda _m: None)
        cfg = dict(self.settings.section("website"))
        if render is not None:
            cfg["screenshot"] = render
        if vision is not None:
            cfg["vision"] = vision
        settings = dc_replace(self.settings, sections={**self.settings.sections, "website": cfg})
        renderer = None
        if cfg.get("screenshot", True):
            pw = settings.provider("playwright").extra
            renderer = WebsiteRenderer(headless=pw.get("headless", True), executable_path=pw.get("executable_path", ""))
        llm = None
        if cfg.get("vision", False):
            try:
                llm = build_llm(settings, "llm")
            except (LLMError, ImportError) as exc:
                say(f"AI design review disabled: {exc}")
        analyzer = WebsiteAnalyzer(settings, self.http, renderer=renderer, llm=llm, firecrawl=self.firecrawl())
        await self.resolve_ad_websites(business_ids)
        rows: list[dict] = []
        sem = asyncio.Semaphore(int(cfg.get("concurrency", 3)))
        with self._sf() as session:
            repo = Repository(session)
            businesses = [b for b in (repo.get_business(i) for i in business_ids) if b is not None]

            async def one(biz: Business) -> None:
                async with sem:
                    async def compute():
                        say(f"  analysing {biz.website or '(no website)'}")
                        return await analyzer.analyze(biz.website, key=str(biz.id), name=biz.name)
                    try:
                        payload, hit = await cached_enrichment(
                            repo, biz.id, "website", self.settings.ttl("website"), compute, refresh=refresh,
                            source="analyzer", ttl_for=lambda p: 1.0 if "broken" in (p.get("flags") or [])
                            else self.settings.ttl("website"))
                    except Exception as exc:
                        log.exception("website analysis failed", extra={"data": {"name": biz.name}})
                        rows.append({"name": biz.name, "website": biz.website, "error": str(exc)[:150]})
                        return
                biz.website_score = payload.get("score")
                biz.website_grade = payload.get("grade")
                biz.website_flags = payload.get("flags") or []
                if payload.get("screenshot"):
                    biz.screenshot_path = payload["screenshot"]
                session.commit()
                rows.append({"name": biz.name, "website": biz.website, "cached": hit, **payload})

            try:
                await asyncio.gather(*(one(b) for b in businesses))
            finally:
                await analyzer.aclose()
        return rows


    # ── ads detection (Phase 5) ──────────────────────────────────────
    async def live_serp(self, query: str, city: str, state: str, *, add_city: bool = True, source: str | None = None,
                        budget: dict | None = None):
        """One Google results page: free browser first; with serp_provider = "auto" a captcha falls back
        to SerpAPI (if a key is set) while ``budget["paid"]`` allows (credits are precious)."""
        from leadengine.enrich.ads.serp import SerpSnapshot, serp_browser, serp_serpapi

        cfg = self.settings.section("ads")
        source = (source or str(cfg.get("serp_provider", "auto"))).lower()
        paid_ok = bool(self.settings.serpapi_api_key) and (budget is None or budget.get("paid", 0) > 0)

        async def paid():
            if budget is not None:
                budget["paid"] = budget.get("paid", 0) - 1
            return await serp_serpapi(self._ready("serpapi"), query, city, state, add_city=add_city)

        try:
            if source == "serpapi":
                return await paid()
            prov = self._ready("playwright")
            snap = await serp_browser(prov, query, city, state, prov.base_url, add_city=add_city)
            if snap.error and source == "auto" and paid_ok:
                try:
                    alt = await paid()
                    alt.error = None
                    return alt
                except LeadEngineError as exc:
                    snap.error += f"; SerpAPI fallback failed: {exc}"
            return snap
        except LeadEngineError as exc:
            return SerpSnapshot(query, f"{city}, {state}", source, error=str(exc))

    async def detect_ads(self, business_ids: list[int], *, keyword: str | None = None, refresh: bool = False,
                         on_progress: Callable[[str], None] | None = None) -> list[dict]:
        """Live SERP ads + LSA (per keyword/city, cached), website ad tags (per business, cached),
        optional Ads Transparency -> ads_status / lsa on each business."""
        from datetime import date

        from leadengine.enrich.ads.serp import SerpSnapshot, match_ads, serp_browser, serp_serpapi
        from leadengine.enrich.ads.site_tags import SiteAdSignals, scan_gtm, scan_html
        from leadengine.enrich.ads.status import decide, parse_transparency
        from leadengine.enrich.emails.crawl import CrawlResult, SiteCrawler

        say = on_progress or (lambda _m: None)
        cfg = self.settings.section("ads")
        serp_source = str(cfg.get("serp_provider", "auto")).lower()
        budget = {"paid": int(cfg.get("paid_fallback_max", 10))}
        tc_blocked = {"free": False}
        ttl = self.settings.ttl("ads")
        today = date.today()
        crawler = SiteCrawler(self.http, max_pages=1, timeout=float(cfg.get("timeout_seconds", 15)),
                              firecrawl=self.firecrawl())
        rows: list[dict] = []
        try:
            with self._sf() as session:
                repo = Repository(session)
                businesses = [b for b in (repo.get_business(i) for i in business_ids) if b is not None]

                # 1) one results page per keyword + city
                snapshots: dict[tuple[str, str], SerpSnapshot | None] = {}
                plans: dict[int, tuple[str, str] | None] = {}
                for biz in businesses:
                    fill_location(biz)
                    kw = keyword or next(iter(repo.keywords_for(biz.id)), None) or (biz.categories or [None])[0]
                    plans[biz.id] = (kw, f"{biz.city}|{biz.state}") if kw and biz.city and biz.state else None
                if serp_source != "none":
                    for kw, loc in {p for p in plans.values() if p}:
                        city, state = loc.split("|")
                        cached = None if refresh else (repo.get_serp(kw, loc, serp_source, ttl)
                                                       or repo.get_serp(kw, loc, "serpapi", ttl)
                                                       or repo.get_serp(kw, loc, "playwright", ttl))
                        if cached is not None:
                            snapshots[(kw, loc)] = SerpSnapshot.from_dict(cached.payload)
                            continue
                        say(f"  Google search: {kw} {city}, {state} ({serp_source})")
                        snap = await self.live_serp(kw, city, state, source=serp_source, budget=budget)
                        if snap.provider == "serpapi" and serp_source == "auto":
                            say("    (free check got a captcha - used 1 SerpAPI credit)")
                        snapshots[(kw, loc)] = snap
                        if not snap.error:
                            repo.save_serp(kw, loc, serp_source, snap.as_dict())
                            session.commit()

                # 2) per business: site tags, transparency, verdict
                for biz in businesses:
                    async def site_compute(b=biz):
                        if not b.website:
                            return None
                        page = await crawler._home(b.website, CrawlResult(start_url=b.website))
                        if page is None:
                            return None
                        sig = scan_html(page.html)
                        if cfg.get("gtm", True) and sig.gtm_containers:
                            sig = await scan_gtm(self.http, sig)
                        return sig.as_dict()

                    site_payload, _ = await cached_enrichment(repo, biz.id, "ads_site", ttl, site_compute,
                                                              refresh=refresh, source="site")
                    site = SiteAdSignals(**site_payload) if site_payload else None

                    transparency = None
                    if cfg.get("transparency", False) and biz.domain:
                        async def tc_compute(b=biz):
                            prov = self._ready("serpapi")
                            r = await self.http.request("GET", "https://serpapi.com/search.json", params={
                                "engine": "google_ads_transparency_center", "text": b.domain, "region": "2840",
                                "api_key": self.settings.serpapi_api_key})
                            data = prov._json(r)
                            prov._record("ads_transparency", success=not data.get("error"))
                            return parse_transparency(data, b.domain, today) if not data.get("error") else None
                        try:
                            transparency, _ = await cached_enrichment(repo, biz.id, "ads_transparency", ttl,
                                                                      tc_compute, refresh=refresh, source="serpapi")
                        except LeadEngineError as exc:
                            say(f"  transparency check skipped: {exc}")

                    if transparency is None and cfg.get("transparency_free", True) and (biz.domain or biz.name):
                        from leadengine.enrich.ads.transparency import (
                            TransparencyBlocked,
                            advertiser_by_name,
                            domain_free,
                        )

                        async def tcf_compute(b=biz):
                            if b.domain:
                                return await domain_free(self.http, b.domain, today=today)
                            return await advertiser_by_name(self.http, b.name, city=b.city, today=today)
                        if not tc_blocked["free"]:
                            try:
                                transparency, _ = await cached_enrichment(
                                    repo, biz.id, "ads_transparency", ttl, tcf_compute, refresh=refresh,
                                    source="transparency_free")
                            except TransparencyBlocked:
                                tc_blocked["free"] = True
                                say("  Ads Transparency Center blocked this connection (captcha) - skipped this run")
                            except Exception as exc:
                                log.info("transparency lookup failed", extra={"data": {"name": biz.name,
                                                                                     "error": str(exc)[:120]}})

                    if transparency is None and cfg.get("transparency_browser", False) and biz.domain:
                        from leadengine.enrich.ads.transparency import transparency_browser

                        async def tcb_compute(b=biz):
                            return await transparency_browser(self._ready("playwright"), b.domain, today=today)
                        transparency, _ = await cached_enrichment(repo, biz.id, "ads_transparency", ttl, tcb_compute,
                                                                  refresh=refresh, source="browser")

                    plan = plans.get(biz.id)
                    snap = snapshots.get(plan) if plan else None
                    hits = match_ads(snap.ads, name=biz.name, domain=biz.domain, phone=biz.phone) if snap else []
                    verdict = decide(serp_hits=hits, serp_checked=bool(snap and not snap.error), site=site,
                                     maps_sponsored=repo.latest_enrichment(biz.id, "maps_sponsored") is not None,
                                     transparency=transparency, today=today)
                    payload = {**verdict.as_dict(), "keyword": plan[0] if plan else None,
                               "serp_error": snap.error if snap else None,
                               "serp_ads_seen": len(snap.ads) if snap else None}
                    unverified = verdict.status == "None" and (snap is None or bool(snap.error))
                    if unverified:
                        # Google could not be checked (captcha / offline / no city): "unknown", never "no ads"
                        why = (snap.error if snap else "no keyword or city to search") or "search failed"
                        payload.update(status="Unknown", evidence=[f"live Google check failed: {why}"])
                        repo.set_enrichment(biz.id, "ads", payload, ttl_days=1, source="ads")
                        biz.ads_status, biz.lsa = None, verdict.lsa
                        biz.ads_confidence, biz.meta_ads = 0, verdict.meta_ads
                        session.commit()
                        rows.append({"name": biz.name, **payload})
                        continue
                    repo.set_enrichment(biz.id, "ads", payload, ttl_days=ttl, source="ads")
                    biz.ads_status, biz.lsa = verdict.status, verdict.lsa
                    biz.ads_confidence, biz.meta_ads = verdict.confidence, verdict.meta_ads
                    session.commit()
                    rows.append({"name": biz.name, **payload})
        finally:
            await crawler.aclose()
        return rows


    # ── opportunity score (Phase 6) ──────────────────────────────────
    def rescore(self, business_ids: list[int] | None = None) -> list[dict]:
        """Recompute Opportunity Score + label + reason (pure DB work, no network)."""
        from datetime import date

        from sqlalchemy import select

        from leadengine.scoring.opportunity import score_business

        cfg = self.settings.section("opportunity")
        weights = cfg.get("weights", {})
        rules = {k: v for k, v in cfg.items() if k != "weights"}
        today = date.today()
        out = []
        with self._sf() as session:
            repo = Repository(session)
            if business_ids is None:
                businesses = list(session.scalars(select(Business)))
            else:
                businesses = [b for b in (repo.get_business(i) for i in business_ids) if b]
            ids = [b.id for b in businesses]
            webs = repo.latest_enrichments(ids, "website")
            from sqlalchemy import func as sfunc

            from leadengine.scoring.opportunity import chain_name
            domain_cities = dict(session.execute(
                select(Business.domain, sfunc.count(sfunc.distinct(Business.city)))
                .where(Business.domain.is_not(None)).group_by(Business.domain)).all())
            done = {"emails": set(repo.latest_enrichments(ids, "emails")),
                    "ads": {i for i, e in repo.latest_enrichments(ids, "ads").items()
                            if (e.payload or {}).get("status") != "Unknown"}}
            statuses = repo.lead_statuses(ids if len(ids) <= 900 else None)
            for biz in businesses:
                fill_location(biz)
                fix_ad_website(repo, biz)
                web = webs.get(biz.id)
                checked = {k for k, have in (("website", webs), ("emails", done["emails"]), ("ads", done["ads"]))
                           if biz.id in have}
                opp = score_business(biz, today=today, website_reasons=(web.payload or {}).get("reasons") if web else None,
                                     lead_status=statuses.get(biz.id), weights=weights, rules=rules, checked=checked,
                                     chain=chain_name(biz, domain_cities))
                biz.opportunity_score, biz.lead_label, biz.lead_reason = opp.score, opp.label, opp.reason
                biz.scored_at = utcnow()
                out.append({"id": biz.id, "name": biz.name, "score": opp.score, "label": opp.label,
                            "reason": opp.reason, "parts": opp.parts})
            session.commit()
        return out


    # ── preview pages (Phase 8) ──────────────────────────────────────
    async def build_previews(self, business_ids: list[int], *, style: str | None = None, deploy: bool | None = None,
                             use_ai: bool | None = None, screenshots: bool = True,
                             on_progress: Callable[[str], None] | None = None) -> list[dict]:
        from leadengine import crm
        from leadengine.enrich.website.render import WebsiteRenderer
        from leadengine.llm import LLMError, build_llm
        from leadengine.preview.builder import PreviewBuilder

        say = on_progress or (lambda _m: None)
        cfg = self.settings.section("preview")
        llm = None
        if (cfg.get("use_ai", True) if use_ai is None else use_ai):
            try:
                llm = build_llm(self.settings, "preview")
            except (LLMError, ImportError) as exc:
                say(f"AI copy unavailable ({exc}); using template copy")
        renderer = WebsiteRenderer() if screenshots else None
        builder = PreviewBuilder(self.settings, self.http, llm=llm, renderer=renderer)
        rows = []
        try:
            with self._sf() as session:
                repo = Repository(session)
                for bid in business_ids:
                    biz = repo.get_business(bid)
                    if biz is None:
                        continue
                    act = repo.latest_enrichment(bid, "maps_activity", fresh_only=False)
                    extras = {k: (e.payload if (e := repo.latest_enrichment(bid, k, fresh_only=False)) else None)
                              for k in ("site_info", "reviews", "website")}
                    extras["keyword"] = next(iter(repo.keywords_for(bid)), None)
                    say(f"  building preview for {biz.name}")
                    try:
                        payload = await builder.build(biz, act.payload if act else None, style=style, deploy=deploy,
                                                      extras=extras)
                    except Exception as exc:
                        log.exception("preview failed", extra={"data": {"name": biz.name}})
                        rows.append({"id": bid, "name": biz.name, "error": str(exc)[:200]})
                        continue
                    repo.set_enrichment(bid, "preview", payload, source="preview")
                    if crm.current_status(session, bid) == "New":
                        crm.set_status(session, bid, "Preview Built", payload.get("url") or "local preview built")
                    session.commit()
                    rows.append({"id": bid, "name": biz.name, **payload})
        finally:
            if renderer is not None:
                await renderer.aclose()
        return rows


    # ── outreach drafts (Phase 9) ────────────────────────────────────
    async def draft_outreach(self, business_ids: list[int], *, use_ai: bool | None = None, force: bool = False,
                             on_progress: Callable[[str], None] | None = None) -> list[dict]:
        """Write email drafts (3 angles + follow-ups) per lead. Nothing is sent."""
        from leadengine import crm
        from leadengine.llm import LLMError, build_llm
        from leadengine.outreach import compose, facts as ofacts
        from leadengine.outreach.mail import is_suppressed, outreach_cfg

        say = on_progress or (lambda _m: None)
        cfg = outreach_cfg(self.settings)
        attach = True        # without a published link, the preview screenshot rides along as an attachment
        llm = None
        if (cfg.get("use_ai", True) if use_ai is None else use_ai):
            try:
                llm = build_llm(self.settings, "outreach")
            except (LLMError, ImportError) as exc:
                say(f"AI drafts unavailable ({exc}); using templates")
        rows = []
        with self._sf() as session:
            repo = Repository(session)
            for bid in business_ids:
                biz = repo.get_business(bid)
                if biz is None:
                    continue
                status = crm.current_status(session, bid)
                twin = crm.find_contacted_twin(session, biz)
                if not force and (status in crm.CONTACTED or twin is not None):
                    why = f"already contacted ({status})" if status in crm.CONTACTED else f"same business as {twin.name}"
                    rows.append({"id": bid, "name": biz.name, "skipped": why})
                    say(f"  skip {biz.name}: {why}")
                    continue
                if not force and (reason := is_suppressed(session, biz.best_email)):
                    rows.append({"id": bid, "name": biz.name, "skipped": f"do-not-contact ({reason})"})
                    continue
                f = ofacts.gather(session, biz, cfg, self.settings.section("preview"))
                session.commit()                       # no DB lock held while the model writes
                say(f"  drafting emails for {biz.name}")
                drafts = await compose.make_drafts(f, cfg, llm, attach=attach)
                if llm is not None and self.credits is not None:
                    self.credits.record(f"llm:{llm.name}", "outreach_drafts", success=drafts["source"] == "ai")
                repo.set_enrichment(bid, "outreach", drafts, source=drafts["source"])
                session.commit()
                rows.append({"id": bid, "name": biz.name, "to": drafts["to"], "source": drafts["source"],
                             "warnings": drafts["warnings"],
                             "subjects": [v["subject"] for v in drafts["variants"]]})
        return rows


    # ── ads-first discovery ("sweep") ────────────────────────────────
    async def ads_sweep(self, keyword: str, locations: list[str], *, variations: int | None = None,
                        landing: bool | None = None, deep: bool | None = None, refresh: bool = False,
                        watch_id: int | None = None, on_progress: Callable[[str], None] | None = None,
                        sleep: Callable[[float], Any] | None = None,
                        on_found: Callable[[list[dict]], None] | None = None) -> dict:
        """Search many phrases in many places, collect every advertiser, audit their ad landing pages."""
        import asyncio
        import random

        from sqlalchemy import select

        from leadengine.db.models import AdSweep
        from leadengine.enrich.ads import sweep as sw
        from leadengine.enrich.ads.serp import SerpSnapshot, serp_browser, serp_serpapi
        from leadengine.suggest import keyword_suggestions

        say = on_progress or (lambda _m: None)
        pause = sleep or asyncio.sleep
        cfg = self.settings.section("ads")
        source = str(cfg.get("serp_provider", "auto")).lower()
        budget = {"paid": int(cfg.get("paid_fallback_max", 10))}
        if source == "none":
            raise LeadEngineError("[ads] serp_provider is 'none' - set it to playwright (free) or serpapi")
        places = [p for p in (resolve_place(x) for x in locations) if p]
        if not places:
            raise LeadEngineError("no valid places (use ZIPs or 'City, ST')")
        n = int(variations or cfg.get("sweep_variations", 6))
        ideas: list[str] = []
        if cfg.get("sweep_suggest", True) and self.http is not None:
            ideas = [x["text"] for x in await keyword_suggestions(keyword, http=self.http) if x["source"] == "google"]
        queries = sw.variations(keyword, n, extra=ideas)
        ttl = self.settings.ttl("ads")
        say(f"Ads finder: {len(queries)} search phrase(s) x {len(places)} place(s) = "
            f"{len(queries) * (len(places)) + len(places)} Google searches ({source})")

        snaps: list[SerpSnapshot] = []
        searches = failed = live = 0
        blocked = False
        for city, state, label in places:
            for q, add_city in [(q, False) for q in queries] + [(keyword, True)]:
                if blocked:
                    break
                tag = f"{q} {city}" if add_city else q
                loc_key = f"{city}|{state}"
                with self._sf() as session:
                    cached = None if refresh else Repository(session).get_serp("sweep:" + tag, loc_key, source, ttl)
                    snap = SerpSnapshot.from_dict(cached.payload) if cached is not None else None
                if snap is None:
                    if live:
                        await pause(random.uniform(float(cfg.get("sweep_delay_min", 4)), float(cfg.get("sweep_delay_max", 9))))
                    live += 1
                    snap = await self.live_serp(q, city, state, add_city=add_city, source=source, budget=budget)
                    snap.keyword = tag
                    if not snap.error:
                        with self._sf() as session:
                            Repository(session).save_serp("sweep:" + tag, loc_key, source, snap.as_dict())
                            session.commit()
                searches += 1
                snap.location = label
                if snap.error:
                    failed += 1
                    say(f"  {tag} @ {label}: {snap.error}")
                    if "captcha" in snap.error.lower() and (source != "auto" or budget["paid"] <= 0
                                                            or not self.settings.serpapi_api_key):
                        blocked = True
                        say("  Google is asking for a captcha - stopping here (results so far are kept). "
                            "Try later or add proxies.")
                    continue
                say(f"  {tag} @ {label}: {len(snap.ads)} ad(s)")
                snaps.append(snap)
                if on_found and snap.ads:
                    on_found([{"key": "adv:" + a.key, "name": a.name, "site": a.domain, "phone": a.phone,
                               "kinds": ", ".join(sorted(a.kinds)), "hits": a.hits, "ad": True}
                              for a in sw.aggregate(snaps)])

        advertisers = sw.aggregate(snaps)
        say(f"Found {len(advertisers)} advertiser(s) in {searches - failed} successful searches")
        rows: list[dict] = []
        ids: list[int] = []
        with self._sf() as session:
            repo = Repository(session)
            prev_q = select(AdSweep).where(AdSweep.watch_id == watch_id) if watch_id is not None else \
                select(AdSweep).where(AdSweep.keyword == keyword.strip().lower())
            prev = session.scalar(prev_q.order_by(AdSweep.id.desc()).limit(1))
            seen_before = {a.get("business_id") for a in (prev.advertisers or [])} if prev else set()
            for adv in advertisers:
                biz = match_advertiser(session, adv)
                if biz is None:
                    first_city, _, first_state = (sorted(adv.locations)[0] if adv.locations else ", ").partition(", ")
                    site = f"https://{adv.domain}" if adv.domain else None
                    biz = repo.upsert_business(BusinessRecord(
                        name=adv.name, provider="google_ads", provider_id=adv.key, website=site, phone=adv.phone,
                        city=first_city or None, state=first_state or None))
                    session.flush()
                evidence = sw.evidence_lines(adv, max(1, searches - failed))
                old = repo.latest_enrichment(biz.id, "ads", fresh_only=False)
                old_ev = [e for e in ((old.payload or {}).get("evidence") or [])] if old else []
                payload = {"status": "Active", "lsa": "lsa" in adv.kinds or bool(biz.lsa), "confidence": 95,
                           "evidence": evidence + [e for e in old_ev if e not in evidence][:4],
                           "meta_ads": bool(biz.meta_ads), "google_ads_ids": [], "keyword": keyword,
                           "sweep": adv.as_dict()}
                repo.set_enrichment(biz.id, "ads", payload, ttl_days=ttl, source="sweep")
                biz.ads_status, biz.ads_confidence = "Active", 95
                biz.lsa = payload["lsa"]
                ids.append(biz.id)
                rows.append({"business_id": biz.id, "name": biz.name, "domain": adv.domain, "kinds": sorted(adv.kinds),
                             "hits": adv.hits, "best_position": adv.best_position, "queries": sorted(adv.queries)[:8],
                             "landing_url": (adv.landing_urls or [None])[0], "ad_title": (adv.titles or [None])[0],
                             "new": bool(prev) and biz.id not in seen_before})
            if on_found:
                on_found([{"key": "adv:" + adv.key, "id": r["business_id"], "name": r["name"],
                           "new": r["new"] or None} for adv, r in zip(advertisers, rows)])
            record = AdSweep(keyword=keyword.strip().lower(), locations=[p[2] for p in places], queries=queries,
                             searches=searches, failed=failed, advertisers=rows, watch_id=watch_id)
            session.add(record)
            session.commit()
            sweep_id = record.id

        if (cfg.get("sweep_landing", True) if landing is None else landing) and rows:
            await self.audit_landings([r for r in rows if r.get("landing_url") or r.get("domain")], on_progress=say)
        if (cfg.get("sweep_deep", True) if deep is None else deep) and ids:
            say(f"Scoring {len(ids)} advertiser websites and finding emails...")
            await self.score_websites(ids, on_progress=say)
            await self.seo_audits(ids, on_progress=say)
            await self.find_emails(ids)
        labels = {r["id"]: r["label"] for r in self.rescore(ids)} if ids else {}
        if on_found and ids:
            with self._sf() as s2:
                r2 = Repository(s2)
                on_found([{**live_row_from_business(b), "key": "adv:" + adv.key}
                          for adv, b in zip(advertisers, (r2.get_business(i) for i in ids)) if b])
        new = sum(1 for r in rows if r["new"])
        say(f"Done: {len(rows)} advertisers, {sum(1 for v in labels.values() if v == 'Hot')} Hot"
            + (f", {new} new since the last sweep" if new else ""))
        return {"sweep_id": sweep_id, "searches": searches, "failed": failed, "advertisers": len(rows), "new": new,
                "hot": sum(1 for v in labels.values() if v == "Hot"), "blocked": blocked, "link": f"/ads/{sweep_id}"}

    async def audit_landings(self, rows: list[dict], *, on_progress: Callable[[str], None] | None = None) -> int:
        """Audit the page each advertiser's ads point to; stores enrichment 'landing' + business.landing_score."""
        from leadengine.enrich.ads.landing import audit_landing
        from leadengine.enrich.website.render import WebsiteRenderer

        say = on_progress or (lambda _m: None)
        renderer = WebsiteRenderer()
        shots = self.settings.root / "data" / "screenshots"
        fc = self.firecrawl()
        done = 0
        try:
            for r in rows:
                url = r.get("landing_url") or (f"https://{r['domain']}" if r.get("domain") else None)
                if not url:
                    continue
                say(f"  landing page: {url}")
                out = await audit_landing(self.http, url, ad_title=r.get("ad_title"), renderer=renderer,
                                          pagespeed_key=self.settings.pagespeed_api_key,
                                          shot_path=shots / f"landing-{r['business_id']}.jpg", firecrawl=fc)
                with self._sf() as session:
                    repo = Repository(session)
                    biz = repo.get_business(r["business_id"])
                    if biz is None:
                        continue
                    repo.set_enrichment(biz.id, "landing", out, ttl_days=self.settings.ttl("website"), source="landing")
                    biz.landing_score = out.get("score")
                    title = ((out.get("facts") or {}).get("title") or "").strip()
                    if biz.name == r.get("domain") and title:          # search-ad-only advertiser: use the site name
                        biz.name = re.split(r"\s[|\-–—:]\s", title)[0][:120] or biz.name
                    session.commit()
                done += 1
        finally:
            await renderer.aclose()
        return done

    # ── local SEO score ──────────────────────────────────────────────
    async def seo_audits(self, business_ids: list[int], *, refresh: bool = False,
                         on_progress: Callable[[str], None] | None = None) -> list[dict]:
        """Local SEO score (on-page + Google profile + optional authority) per business, cached."""
        from sqlalchemy import select

        from leadengine.db.models import SearchResult
        from leadengine.enrich.seo import seo_audit
        from leadengine.scoring.opportunity import chain_name

        say = on_progress or (lambda _m: None)
        key = self.settings.openpagerank_api_key
        fc = self.firecrawl()
        rows = []
        for bid in business_ids:
            with self._sf() as session:
                repo = Repository(session)
                biz = repo.get_business(bid)
                if biz is None:
                    continue
                cached = None if refresh else repo.latest_enrichment(bid, "seo")
                if cached is not None:
                    rows.append({"id": bid, "name": biz.name, **cached.payload})
                    continue
                # competitors: businesses ranked above it in the same searches
                comps = []
                for sid, rank in session.execute(select(SearchResult.search_id, SearchResult.rank)
                                                 .where(SearchResult.business_id == bid)):
                    for other, orank in repo.search_results(sid, limit=10):
                        if other.id != bid and (rank is None or orank < rank):
                            comps.append({"name": other.name, "review_count": other.review_count,
                                          "website": other.website, "chain": bool(chain_name(other, {}))})
                kw = next(iter(repo.keywords_for(bid)), None) or (biz.categories or [None])[0]
                towns = [t.split(",")[0] for t in zip_directory().towns(biz.zip_code)] if biz.zip_code else []
                session.expunge(biz)
            say(f"  local SEO: {biz.name}")
            out = await seo_audit(self.http, biz, service=kw, competitors=comps[:5], towns=towns, opr_key=key,
                                  firecrawl=fc)
            with self._sf() as session:
                repo = Repository(session)
                repo.set_enrichment(bid, "seo", out, ttl_days=self.settings.ttl("website"), source="seo")
                b = repo.get_business(bid)
                b.seo_score = out["score"]
                session.commit()
            rows.append({"id": bid, "name": biz.name, **out})
        return rows

    async def review_audits(self, business_ids: list[int], *, refresh: bool = False,
                            on_progress: Callable[[str], None] | None = None) -> list[dict]:
        """Google reviews audit per business (cached): negatives, unanswered ones with suggested replies,
        reply rate, review speed, complaint themes. Free browser first; SerpAPI (2 credits) only as fallback."""
        from leadengine.enrich.reviews import ai_summary, analyze, serpapi_reviews
        from leadengine.llm import LLMError, build_llm

        say = on_progress or (lambda _m: None)
        cfg = self.settings.section("reviews")
        source = str(cfg.get("source", "auto")).lower()
        budget = int(cfg.get("serpapi_max", 10))
        llm = None
        if cfg.get("ai_summary", True):
            try:
                llm = build_llm(self.settings, "llm")
            except (LLMError, ImportError, Exception):
                llm = None
        rows: list[dict] = []
        for bid in business_ids:
            with self._sf() as session:
                repo = Repository(session)
                biz = repo.get_business(bid)
                if biz is None:
                    continue
                cached = None if refresh else repo.latest_enrichment(bid, "reviews")
                if cached is not None:
                    rows.append({"id": bid, "name": biz.name, "cached": True, **cached.payload})
                    continue
                session.expunge(biz)
            data, src = None, None
            if source in ("auto", "browser") and biz.google_maps_url:
                try:
                    say(f"  reviews: {biz.name} (Google Maps)")
                    data = await self._ready("playwright").review_audit_data(biz.google_maps_url)
                    src = "browser"
                    if not (data.get("lowest") or data.get("newest")):
                        data = None
                except Exception as exc:
                    log.info("browser review read failed", extra={"data": {"name": biz.name, "error": str(exc)[:120]}})
            if data is None and source in ("auto", "serpapi") and self.settings.serpapi_api_key and budget >= 2 \
                    and (biz.data_id or biz.place_id):
                say(f"  reviews: {biz.name} (SerpAPI, 2 credits)")
                try:
                    lowest, d1 = await serpapi_reviews(self.http, self.settings.serpapi_api_key, data_id=biz.data_id,
                                                       place_id=biz.place_id, sort_by="ratingLow")
                    newest, d2 = await serpapi_reviews(self.http, self.settings.serpapi_api_key, data_id=biz.data_id,
                                                       place_id=biz.place_id, sort_by="newestFirst")
                    budget -= 2
                    if self.credits is not None:
                        self.credits.record("serpapi", "google_maps_reviews", units=2)
                    info = d1.get("place_info") or d2.get("place_info") or {}
                    data = {"lowest": lowest, "newest": newest, "histogram": None,
                            "topics": d1.get("topics") or d2.get("topics") or [],
                            "rating": info.get("rating"), "total": info.get("reviews")}
                    src = "serpapi"
                except Exception as exc:
                    say(f"    SerpAPI reviews failed: {str(exc)[:100]}")
            if data is None:
                rows.append({"id": bid, "name": biz.name, "error": "reviews could not be read"})
                continue
            audit = analyze(newest=data.get("newest") or [], lowest=data.get("lowest") or [],
                            histogram=data.get("histogram"), rating=biz.rating or data.get("rating"),
                            total=biz.review_count or data.get("total"), topics=data.get("topics"),
                            phone=biz.phone, source=src)
            audit["ai_summary"] = await ai_summary(llm, biz.name, audit)
            with self._sf() as session:
                repo = Repository(session)
                repo.set_enrichment(bid, "reviews", audit, ttl_days=self.settings.ttl("activity"), source=src)
                b = repo.get_business(bid)
                b.reputation_score = audit["score"]
                if b.owner_response_rate is None and audit["reply_rate"] is not None:
                    b.owner_response_rate = audit["reply_rate"] / 100
                session.commit()
            rows.append({"id": bid, "name": biz.name, **audit})
        return rows

    async def citation_audits(self, business_ids: list[int], *, refresh: bool = False,
                              on_progress: Callable[[str], None] | None = None) -> list[dict]:
        """Citations / NAP check per business (cached 30 days): where it is listed, which important directories
        are missing, and which listings show a different phone / address. Firecrawl search (~4 credits) or
        SerpAPI (2 credits)."""
        from leadengine.enrich.citations import audit_citations

        say = on_progress or (lambda _m: None)
        fc = self.firecrawl()
        rows = []
        for bid in business_ids:
            with self._sf() as session:
                repo = Repository(session)
                biz = repo.get_business(bid)
                if biz is None:
                    continue
                cached = None if refresh else repo.latest_enrichment(bid, "citations")
                if cached is not None:
                    rows.append({"id": bid, "name": biz.name, "cached": True, **cached.payload})
                    continue
                session.expunge(biz)
            say(f"  citations: {biz.name}")
            try:
                out = await audit_citations(biz, firecrawl=fc, http=self.http, serp_key=self.settings.serpapi_api_key,
                                            credits=self.credits)
            except Exception as exc:
                say(f"    citations check failed: {str(exc)[:100]}")
                out = None
            if out is None:
                rows.append({"id": bid, "name": biz.name, "error": "add a Firecrawl or SerpAPI key for citations"})
                continue
            with self._sf() as session:
                repo = Repository(session)
                repo.set_enrichment(bid, "citations", out, ttl_days=30, source=out["source"])
                repo.get_business(bid).citation_score = out["score"]
                session.commit()
            rows.append({"id": bid, "name": biz.name, **out})
        return rows

    async def site_info(self, business_ids: list[int], *, refresh: bool = False, explicit: bool = False,
                        on_progress: Callable[[str], None] | None = None) -> int:
        """Firecrawl 'full' mode: services, service areas, owner, years in business from each website (cached).
        ``explicit`` (the lead page button) runs it whatever the mode, as long as a key is saved."""
        fc = self.firecrawl()
        if fc is not None and explicit:
            fc.mode = "full"
        if fc is None or not fc.can_extract:
            return 0
        say = on_progress or (lambda _m: None)
        done = 0
        for bid in business_ids:
            with self._sf() as session:
                repo = Repository(session)
                biz = repo.get_business(bid)
                if biz is None or not biz.website or set(biz.website_flags or []) & {
                        "broken", "parked", "facebook_only", "social_or_directory_only"}:
                    continue
                if not refresh and repo.latest_enrichment(bid, "site_info") is not None:
                    continue
                url = biz.website
            say(f"  reading site facts: {url}")
            info = await fc.extract_info(url)
            if info is None:
                continue
            with self._sf() as session:
                repo = Repository(session)
                repo.set_enrichment(bid, "site_info", info, ttl_days=self.settings.ttl("website"), source="firecrawl")
                biz = repo.get_business(bid)
                if info.get("owner_name") and not biz.owner_name:
                    biz.owner_name = str(info["owner_name"])[:120]
                session.commit()
            done += 1
        return done

    # ── audit report ─────────────────────────────────────────────────
    async def build_reports(self, business_ids: list[int], *, deploy: bool | None = None,
                            on_progress: Callable[[str], None] | None = None) -> list[dict]:
        """One shareable audit page per lead (data/reports/<slug>/), optionally published like previews."""
        from pathlib import Path

        from leadengine.preview.builder import PreviewBuilder
        from leadengine.report.builder import ReportBuilder

        say = on_progress or (lambda _m: None)
        builder = ReportBuilder(self.settings)
        cfg = self.settings.section("preview")
        deploy = cfg.get("deploy", "none") != "none" if deploy is None else deploy
        rows = []
        for bid in business_ids:
            with self._sf() as session:
                biz = Repository(session).get_business(bid)
                if biz is None:
                    continue
                say(f"  audit report: {biz.name}")
                out = builder.build(session, biz)
                name = biz.name
            if deploy:
                try:
                    out.update(await PreviewBuilder(self.settings, self.http).publish(Path(out["dir"]), out["slug"] + "-audit"))
                except Exception as exc:
                    out["error"] = f"publish failed: {exc}"[:200]
            with self._sf() as session:
                Repository(session).set_enrichment(bid, "audit", out, source="report")
                session.commit()
            rows.append({"id": bid, "name": name, **out})
        return rows

    # ── weekly watch / alerts ────────────────────────────────────────
    def due_watches(self, now=None) -> list[int]:
        from datetime import timedelta

        from sqlalchemy import select

        from leadengine.db.models import Watch

        now = now or utcnow()
        with self._sf() as session:
            return [w.id for w in session.scalars(select(Watch).where(Watch.active.is_(True)))
                    if w.last_run_at is None or w.last_run_at <= now - timedelta(days=w.every_days or 7)]

    async def run_watches(self, watch_ids: list[int] | None = None, *,
                          on_progress: Callable[[str], None] | None = None) -> dict:
        """Run due (or given) watches; every business that newly appears as an advertiser becomes an Alert."""
        from leadengine.db.models import AdSweep, Alert, Watch

        say = on_progress or (lambda _m: None)
        ids = watch_ids if watch_ids is not None else self.due_watches()
        created: list[dict] = []
        for wid in ids:
            with self._sf() as session:
                w = session.get(Watch, wid)
                if w is None:
                    continue
                keyword, locations, variations = w.keyword, list(w.locations or []), w.variations
            say(f"Watch #{wid}: '{keyword}' in {', '.join(locations)}")
            try:
                out = await self.ads_sweep(keyword, locations, variations=variations, watch_id=wid, on_progress=say)
            except LeadEngineError as exc:
                say(f"  watch #{wid} failed: {exc}")
                continue
            with self._sf() as session:
                w = session.get(Watch, wid)
                w.last_run_at = utcnow()
                sweep = session.get(AdSweep, out["sweep_id"])
                for a in sweep.advertisers or []:
                    if not a.get("new"):
                        continue
                    kinds = ", ".join({"search": "search ads", "lsa": "Local Services Ads", "places": "map ads"}.get(k, k)
                                      for k in a.get("kinds") or [])
                    msg = (f"{a['name']} started advertising for '{keyword}' ({kinds}; seen in {a['hits']} searches)")
                    session.add(Alert(kind="new_advertiser", business_id=a["business_id"], watch_id=wid, message=msg))
                    created.append({"business_id": a["business_id"], "message": msg})
                session.commit()
            say(f"  {sum(1 for c in created)} new advertiser alert(s) so far")
        if created and self.settings.alert_webhook_url and self.http is not None:
            try:
                await self.http.request("POST", self.settings.alert_webhook_url, retries=1,
                                        json={"type": "leadengine.alerts", "alerts": created})
            except Exception as exc:
                say(f"  alert webhook failed: {exc}")
        return {"watches": len(ids), "alerts": len(created), "link": "/alerts"}

    # ── geo-grid rank heatmap ────────────────────────────────────────
    async def rank_grid(self, keyword: str, *, zip_code: str | None = None, business_id: int | None = None,
                        lat: float | None = None, lng: float | None = None, size: int | None = None,
                        spacing_km: float | None = None, on_progress: Callable[[str], None] | None = None) -> dict:
        """Search Google Maps from every point of an N×N grid; store where each business ranks."""
        import asyncio

        from leadengine.db.models import RankGrid
        from leadengine.geo.grid import zoom_for_span
        from leadengine.geo.rankgrid import grid_points, summarize
        from leadengine.normalize import normalize_zip

        say = on_progress or (lambda _m: None)
        cfg = self.settings.section("rank")
        size = int(size or cfg.get("size", 7))
        label = None
        if business_id is not None:
            with self._sf() as session:
                biz = Repository(session).get_business(business_id)
                if biz is None or biz.lat is None:
                    raise LeadEngineError("that business has no map location yet")
                lat, lng, label = biz.lat, biz.lng, biz.name
        elif zip_code:
            z = zip_directory().get(normalize_zip(zip_code))
            if z is None:
                raise LeadEngineError(f"unknown ZIP {zip_code}")
            lat, lng, label = z.lat, z.lng, f"{z.zip} {z.label}"
            spacing_km = spacing_km or max(0.5, round(z.radius_km * 2 / max(1, size - 1), 2))
        if lat is None or lng is None:
            raise LeadEngineError("give a ZIP, a business or coordinates")
        spacing = float(spacing_km or cfg.get("spacing_km", 1.0))
        zoom = int(cfg.get("zoom") or zoom_for_span(lat, spacing * 2.5))
        pts = grid_points(lat, lng, size, spacing)
        provider = self._ready(str(cfg.get("provider", "playwright")))
        say(f"Rank grid: '{keyword}' {size}x{size} = {len(pts)} map searches, {spacing} km apart (zoom {zoom})")
        sem = asyncio.Semaphore(int(cfg.get("concurrency", 2)))
        done = 0

        async def one(p: dict) -> None:
            nonlocal done
            async with sem:
                try:
                    res = await provider.search(SearchQuery(keyword=keyword, lat=p["lat"], lng=p["lng"], zoom=zoom,
                                                            max_results=int(cfg.get("depth", 20))))
                    p["records"] = res.records
                except Exception as exc:
                    p["error"] = f"{type(exc).__name__}: {str(exc)[:80]}"
            done += 1
            if done % max(1, len(pts) // 10) == 0 or done == len(pts):
                say(f"  {done}/{len(pts)} points searched")

        await asyncio.gather(*(one(p) for p in pts))
        names: dict[int, str] = {}
        with self._sf() as session:
            repo = Repository(session)
            for p in pts:
                recs = p.pop("records", None) or []
                ranked, _ = self._store(session, repo, recs, keyword, None)
                p["ranks"] = [b.id for b, _ in sorted(ranked, key=lambda t: t[1])]
                names.update({b.id: b.name for b, _ in ranked})
            summary = summarize(pts, names)
            grid = RankGrid(keyword=keyword, label=label, center_lat=lat, center_lng=lng, size=size,
                            spacing_km=spacing, zoom=zoom, focus_business_id=business_id, points=pts, summary=summary)
            session.add(grid)
            session.flush()
            for row in summary:
                repo.set_enrichment(row["business_id"], "rank", {**row, "grid_id": grid.id, "keyword": keyword,
                                                                 "size": size, "spacing_km": spacing},
                                    ttl_days=self.settings.ttl("search"), source="rankgrid")
            session.commit()
            gid = grid.id
        failed = sum(1 for p in pts if p.get("error"))
        say(f"Done: {len(summary)} businesses ranked" + (f", {failed} point(s) failed" if failed else ""))
        return {"grid_id": gid, "points": len(pts), "failed": failed, "businesses": len(summary),
                "leader": summary[0]["name"] if summary else None, "link": f"/rank/{gid}"}

def _live_key(place_id: str | None, name: str | None, phone: str | None) -> str:
    from leadengine.normalize import normalize_phone
    return place_id or f"{(name or '').lower()}|{normalize_phone(phone) or ''}"


def live_row_from_record(r: BusinessRecord) -> dict:
    real, was_ad = unwrap_ad_url(r.website)
    return {"key": _live_key(r.place_id, r.name, r.phone), "name": r.name, "rating": r.rating, "reviews": r.review_count,
            "phone": r.phone, "site": normalize_domain(real) if real else None, "category": (r.categories or [None])[0],
            "ad": True if (r.sponsored or was_ad) else None, "zip": r.zip_code}


def live_row_from_business(b: Business) -> dict:
    return {"key": _live_key(b.place_id, b.name, b.phone), "id": b.id, "name": b.name, "rating": b.rating,
            "reviews": b.review_count, "phone": b.phone, "site": b.domain, "zip": b.zip_code,
            "ads": b.ads_status, "ad": True if b.ads_status == "Active" else None, "site_score": b.website_score,
            "landing": b.landing_score, "email": b.best_email, "label": b.lead_label, "score": b.opportunity_score}

def resolve_place(text: str) -> tuple[str, str, str] | None:
    """'10001' / 'New York, NY' / 'Astoria' -> (city, state, label) using the bundled ZIP data."""
    from leadengine.normalize import normalize_zip

    t = (text or "").strip()
    if not t:
        return None
    try:
        z = zip_directory().get(normalize_zip(t))
        return (z.city, z.state, z.label) if z else None
    except ValueError:
        pass
    city, _, state = t.partition(",")
    city, state = city.strip(), state.strip().upper()[:2]
    if state:
        return city, state, f"{city}, {state}"
    best = zip_directory().by_city(city)
    return (best[0].city, best[0].state, best[0].label) if best else None


def match_advertiser(session: Session, adv) -> Business | None:
    """Same business already in the database? Domain first, then phone, then a very similar name."""
    from sqlalchemy import select

    from leadengine.enrich.ads.serp import name_similarity
    from leadengine.normalize import normalize_phone

    if adv.domain:
        b = session.scalar(select(Business).where(Business.domain == adv.domain)
                           .order_by(Business.review_count.desc().nulls_last()).limit(1))
        if b is not None:
            return b
    phone = normalize_phone(adv.phone)
    if phone:
        b = session.scalar(select(Business).where(Business.phone_norm == phone).limit(1))
        if b is not None:
            return b
    if adv.name and not (adv.domain and adv.name == adv.domain):
        states = {loc.rsplit(", ", 1)[-1] for loc in adv.locations}
        for b in session.scalars(select(Business).where(Business.state.in_(states)) if states else select(Business)):
            if name_similarity(b.name, adv.name) >= 0.8 and len(adv.name.split()) >= 2:
                return b
    return None

def fix_ad_website(repo: Repository, biz: Business) -> bool:
    """Older rows saved a Google ad click link as the website: unwrap it and record the ad (no network)."""
    real, was_ad = unwrap_ad_url(biz.website)
    if not was_ad:
        return False
    from sqlalchemy import delete

    from leadengine.db.models import Enrichment

    click = biz.website
    biz.website, biz.domain = real, normalize_domain(real) if real else None
    biz.website_score = biz.website_grade = None          # the old score was for Google's link: redo it
    biz.website_flags = []
    repo.session.execute(delete(Enrichment).where(Enrichment.business_id == biz.id,
                                                  Enrichment.kind.in_(("website", "emails", "seo", "ads_site"))))
    repo.set_enrichment(biz.id, "maps_sponsored", {"click_url": click, "landing_url": real, "provider": "fix"},
                        ttl_days=30, source="fix")
    if biz.ads_status != "Active":
        biz.ads_status, biz.ads_confidence = "Active", 90
        repo.set_enrichment(biz.id, "ads", {"status": "Active", "lsa": bool(biz.lsa), "confidence": 90,
                                            "meta_ads": bool(biz.meta_ads), "google_ads_ids": [],
                                            "evidence": ["sponsored (paid) listing on Google Maps"]}, ttl_days=30, source="fix")
    return True

def fill_location(biz: Business) -> bool:
    """Maps cards often lack city/ZIP ("91-01 120th St"); take them from the nearest ZIP centre."""
    if (biz.city and biz.zip_code) or biz.lat is None or biz.lng is None:
        return False
    z = zip_directory().nearest(biz.lat, biz.lng)
    if z is None:
        return False
    biz.zip_code = biz.zip_code or z.zip
    biz.city = biz.city or z.city
    biz.state = biz.state or z.state
    return True


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
