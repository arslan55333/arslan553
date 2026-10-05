"""All reads/writes go through :class:`Repository` so storage rules live in one place."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from leadengine.db.models import (
    ApiUsage,
    Business,
    BusinessSource,
    Email,
    Enrichment,
    GeoCache,
    Search,
    SearchResult,
    utcnow,
)
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.normalize import (
    dedupe_key,
    normalize_domain,
    normalize_keyword,
    normalize_phone,
    parse_us_address,
)

# Simple fields copied from a provider record onto the master row when present.
_MERGE_FIELDS = (
    "name", "phone", "website", "address", "city", "state", "zip_code", "lat", "lng",
    "rating", "review_count", "hours", "google_maps_url", "business_status", "claimed",
    "photo_count", "last_review_at", "recent_review_dates", "owner_response_rate",
)


def _cutoff(ttl_days: float, now: datetime | None = None) -> datetime:
    return (now or utcnow()) - timedelta(days=ttl_days)


class Repository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ── Businesses ────────────────────────────────────────────────────
    def find_business(self, rec: BusinessRecord) -> Business | None:
        """Match by place_id, then Google data_id, then this provider's own id, then phone+domain."""
        s = self.session
        if rec.place_id:
            biz = s.scalar(select(Business).where(Business.place_id == rec.place_id))
            if biz:
                return biz
        if rec.data_id:
            for cand in s.scalars(select(Business).where(Business.data_id == rec.data_id)):
                if not (rec.place_id and cand.place_id and cand.place_id != rec.place_id):
                    return cand
        if rec.provider_id:
            src = s.scalar(
                select(BusinessSource).where(
                    BusinessSource.provider == rec.provider,
                    BusinessSource.provider_id == rec.provider_id,
                )
            )
            if src:
                return src.business
        key = dedupe_key(rec.phone, rec.website)
        if key:
            for cand in s.scalars(select(Business).where(Business.dedupe_key == key)):
                # Two different Google listings are two businesses, even if they share phone+site.
                if rec.place_id and cand.place_id and cand.place_id != rec.place_id:
                    continue
                return cand
        return None

    def upsert_business(self, rec: BusinessRecord, *, fill_only: bool = False) -> Business:
        """Insert or merge a provider record into the master table and store its raw payload.

        Normally newer non-empty values win. With ``fill_only=True`` (gap filling from a
        secondary source) only fields that are still empty are filled.
        """
        now = utcnow()
        if not (rec.city and rec.state and rec.zip_code):
            city, state, zip_code = parse_us_address(rec.address)
            rec.city = rec.city or city
            rec.state = rec.state or state
            rec.zip_code = rec.zip_code or zip_code

        biz = self.find_business(rec)
        if biz is None:
            biz = Business(name=rec.name, categories=[], first_seen=now)
            self.session.add(biz)

        for name in _MERGE_FIELDS:
            value = getattr(rec, name)
            if value in (None, "", [], {}):
                continue
            if fill_only and getattr(biz, name, None) not in (None, "", [], {}):
                continue
            setattr(biz, name, value)
        if rec.place_id and not biz.place_id:
            biz.place_id = rec.place_id
        if rec.data_id and not biz.data_id:
            biz.data_id = rec.data_id
        if rec.categories:
            merged = list(biz.categories or [])
            merged += [c for c in rec.categories if c and c not in merged]
            biz.categories = merged

        biz.phone_norm = normalize_phone(biz.phone)
        biz.domain = normalize_domain(biz.website)
        biz.dedupe_key = dedupe_key(biz.phone, biz.website)
        biz.last_seen = now
        self.session.flush()

        self._save_source(biz, rec, now)
        return biz

    def _save_source(self, biz: Business, rec: BusinessRecord, now: datetime) -> None:
        stmt = select(BusinessSource).where(
            BusinessSource.business_id == biz.id, BusinessSource.provider == rec.provider
        )
        stmt = stmt.where(
            BusinessSource.provider_id == rec.provider_id
            if rec.provider_id
            else BusinessSource.provider_id.is_(None)
        )
        src = self.session.scalar(stmt)
        if src is None:
            src = BusinessSource(business_id=biz.id, provider=rec.provider, provider_id=rec.provider_id)
            self.session.add(src)
        src.raw = rec.raw
        src.fetched_at = now

    def get_business(self, business_id: int) -> Business | None:
        return self.session.get(Business, business_id)

    def list_businesses(
        self,
        *,
        keyword: str | None = None,
        zip_code: str | None = None,
        min_rating: float | None = None,
        min_reviews: int | None = None,
        has_website: bool | None = None,
        limit: int | None = None,
    ) -> list[Business]:
        stmt = select(Business)
        if keyword:
            stmt = stmt.where(
                Business.id.in_(
                    select(SearchResult.business_id)
                    .join(Search, Search.id == SearchResult.search_id)
                    .where(Search.keyword_norm == normalize_keyword(keyword))
                )
            )
        if zip_code:
            stmt = stmt.where(Business.zip_code == zip_code)
        if min_rating is not None:
            stmt = stmt.where(Business.rating >= min_rating)
        if min_reviews is not None:
            stmt = stmt.where(Business.review_count >= min_reviews)
        if has_website is True:
            stmt = stmt.where(Business.website.is_not(None))
        elif has_website is False:
            stmt = stmt.where(Business.website.is_(None))
        stmt = stmt.order_by(Business.review_count.desc().nulls_last(), Business.name)
        if limit:
            stmt = stmt.limit(limit)
        return list(self.session.scalars(stmt))

    # ── Searches (search-level cache) ─────────────────────────────────
    def find_fresh_search(
        self, provider: str, query: SearchQuery, ttl_days: float, *, mode: str = "single"
    ) -> Search | None:
        """A recent identical search (same mode) that already covers ``query.max_results``."""
        stmt = (
            select(Search)
            .where(
                Search.provider == provider,
                Search.keyword_norm == normalize_keyword(query.keyword),
                Search.zip_code.is_(None) if query.zip_code is None else Search.zip_code == query.zip_code,
                Search.location.is_(None) if query.location is None else Search.location == query.location,
                Search.ran_at >= _cutoff(ttl_days),
                Search.mode == mode,
            )
            .order_by(Search.ran_at.desc())
        )
        for search in self.session.scalars(stmt):
            if search.exhausted or search.result_count >= query.max_results:
                return search
        return None

    def record_search(
        self,
        query: SearchQuery,
        provider: str,
        ranked: Iterable[tuple[Business, int]],
        *,
        exhausted: bool,
        api_calls: int,
        mode: str = "single",
        cells: int | None = None,
    ) -> Search:
        best: dict[int, int] = {}
        for biz, rank in ranked:
            best[biz.id] = min(rank, best.get(biz.id, rank))
        search = Search(
            provider=provider,
            keyword=query.keyword,
            keyword_norm=normalize_keyword(query.keyword),
            zip_code=query.zip_code,
            location=query.location,
            lat=query.lat,
            lng=query.lng,
            max_results=query.max_results,
            result_count=len(best),
            exhausted=exhausted,
            api_calls=api_calls,
            mode=mode,
            cells=cells,
        )
        self.session.add(search)
        self.session.flush()
        self.session.add_all(
            SearchResult(search_id=search.id, business_id=bid, rank=rank) for bid, rank in best.items()
        )
        self.session.flush()
        return search

    def search_results(self, search_id: int, limit: int | None = None) -> list[tuple[Business, int]]:
        stmt = (
            select(Business, SearchResult.rank)
            .join(SearchResult, SearchResult.business_id == Business.id)
            .where(SearchResult.search_id == search_id)
            .order_by(SearchResult.rank)
        )
        if limit:
            stmt = stmt.limit(limit)
        return [(biz, rank) for biz, rank in self.session.execute(stmt)]

    # ── Enrichments (per-business cache) ─────────────────────────────
    def set_enrichment(
        self,
        business_id: int,
        kind: str,
        payload: Any,
        *,
        ttl_days: float | None = None,
        source: str | None = None,
    ) -> Enrichment:
        now = utcnow()
        row = Enrichment(
            business_id=business_id,
            kind=kind,
            payload=payload,
            source=source,
            fetched_at=now,
            expires_at=now + timedelta(days=ttl_days) if ttl_days is not None else None,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def latest_enrichment(self, business_id: int, kind: str, *, fresh_only: bool = True) -> Enrichment | None:
        stmt = (
            select(Enrichment)
            .where(Enrichment.business_id == business_id, Enrichment.kind == kind)
            .order_by(Enrichment.fetched_at.desc(), Enrichment.id.desc())
            .limit(1)
        )
        row = self.session.scalar(stmt)
        if row and fresh_only and row.expires_at is not None and row.expires_at <= utcnow():
            return None
        return row

    # ── Geocode cache ────────────────────────────────────────────────
    def get_geo(self, key: str, ttl_days: float) -> GeoCache | None:
        row = self.session.get(GeoCache, key)
        if row and row.fetched_at >= _cutoff(ttl_days):
            return row
        return None

    def set_geo(self, key: str, lat: float, lng: float, payload: Any) -> GeoCache:
        row = self.session.get(GeoCache, key) or GeoCache(key=key, lat=lat, lng=lng)
        row.lat, row.lng, row.payload, row.fetched_at = lat, lng, payload, utcnow()
        self.session.add(row)
        self.session.flush()
        return row

    # ── Stats ────────────────────────────────────────────────────────
    def stats(self) -> dict[str, int]:
        def count(model) -> int:
            return self.session.scalar(select(func.count()).select_from(model)) or 0

        return {
            "businesses": count(Business),
            "with_website": self.session.scalar(
                select(func.count()).select_from(Business).where(Business.website.is_not(None))
            ) or 0,
            "searches": count(Search),
            "enrichments": count(Enrichment),
            "emails": count(Email),
            "api_calls": count(ApiUsage),
        }
