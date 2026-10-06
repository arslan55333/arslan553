"""Database schema. Generic SQLAlchemy types only, so Postgres is a drop-in later."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite has no timezone support, so we store UTC everywhere)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Business(Base):
    """Master record: one row per real-world business."""

    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(primary_key=True)
    place_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    data_id: Mapped[str | None] = mapped_column(String(64), index=True)  # Google "0x..:0x.." feature id
    dedupe_key: Mapped[str | None] = mapped_column(String(255), index=True)

    name: Mapped[str] = mapped_column(String(500))
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    phone: Mapped[str | None] = mapped_column(String(64))
    phone_norm: Mapped[str | None] = mapped_column(String(32), index=True)
    website: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(8))
    zip_code: Mapped[str | None] = mapped_column(String(10), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)

    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    hours: Mapped[Any] = mapped_column(JSON, nullable=True)
    google_maps_url: Mapped[str | None] = mapped_column(Text)
    business_status: Mapped[str | None] = mapped_column(String(64))
    claimed: Mapped[bool | None] = mapped_column(Boolean)
    # Best email (Phase 3) - quick filtering without opening the emails table
    best_email: Mapped[str | None] = mapped_column(String(320))
    email_confidence: Mapped[int | None] = mapped_column(Integer)
    email_status: Mapped[str | None] = mapped_column(String(20))      # valid | catch_all | unknown | invalid | risky
    owner_name: Mapped[str | None] = mapped_column(String(255))

    # Website score (Phase 4)
    website_score: Mapped[int | None] = mapped_column(Integer)
    website_grade: Mapped[str | None] = mapped_column(String(20))
    website_flags: Mapped[Any] = mapped_column(JSON, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(Text)

    # Ads (Phase 5)
    ads_status: Mapped[str | None] = mapped_column(String(10))       # Active | Likely | Past | None
    lsa: Mapped[bool | None] = mapped_column(Boolean)
    ads_confidence: Mapped[int | None] = mapped_column(Integer)
    landing_score: Mapped[int | None] = mapped_column(Integer)      # 0-100, the page their ads send people to
    seo_score: Mapped[int | None] = mapped_column(Integer)          # 0-100 local SEO (on-page + Google profile)
    meta_ads: Mapped[bool | None] = mapped_column(Boolean)

    # Opportunity (Phase 6)
    opportunity_score: Mapped[int | None] = mapped_column(Integer, index=True)
    lead_label: Mapped[str | None] = mapped_column(String(10), index=True)     # Hot | Warm | Cold | Skip
    lead_reason: Mapped[str | None] = mapped_column(Text)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Activity signals (Phase 2 Maps scraping)
    photo_count: Mapped[int | None] = mapped_column(Integer)
    last_review_at: Mapped[datetime | None] = mapped_column(DateTime)
    recent_review_dates: Mapped[Any] = mapped_column(JSON, nullable=True)  # newest reviews, ISO dates
    owner_response_rate: Mapped[float | None] = mapped_column(Float)      # share of sampled reviews answered

    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    sources: Mapped[list["BusinessSource"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Business {self.id} {self.name!r} place_id={self.place_id}>"


class BusinessSource(Base):
    """Latest raw payload per provider for a business (audit trail + re-parsing)."""

    __tablename__ = "business_sources"
    __table_args__ = (Index("ix_source_provider_id", "provider", "provider_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    provider_id: Mapped[str | None] = mapped_column(String(255))
    raw: Mapped[Any] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    business: Mapped[Business] = relationship(back_populates="sources")


class Search(Base):
    """One provider query (keyword + area). Used as the search-level cache."""

    __tablename__ = "searches"
    __table_args__ = (Index("ix_search_lookup", "provider", "keyword_norm", "zip_code", "ran_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    keyword: Mapped[str] = mapped_column(String(255))
    keyword_norm: Mapped[str] = mapped_column(String(255))
    zip_code: Mapped[str | None] = mapped_column(String(10))
    location: Mapped[str | None] = mapped_column(String(255))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    max_results: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str | None] = mapped_column(String(20), default="single")  # single | grid
    cells: Mapped[int | None] = mapped_column(Integer)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    exhausted: Mapped[bool] = mapped_column(Boolean, default=False)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)
    ran_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SearchResult(Base):
    __tablename__ = "search_results"

    search_id: Mapped[int] = mapped_column(ForeignKey("searches.id", ondelete="CASCADE"), primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer)


class Enrichment(Base):
    """Timestamped result of any enrichment (emails, website score, ads ...). History is kept."""

    __tablename__ = "enrichments"
    __table_args__ = (Index("ix_enrichment_lookup", "business_id", "kind", "fetched_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(50))
    payload: Mapped[Any] = mapped_column(JSON, nullable=True)
    source: Mapped[str | None] = mapped_column(String(100))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class Email(Base):
    """Emails found or guessed for a business (filled in Phase 3)."""

    __tablename__ = "emails"
    __table_args__ = (UniqueConstraint("business_id", "email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    source: Mapped[str | None] = mapped_column(String(255))
    method: Mapped[str | None] = mapped_column(String(30))
    source_url: Mapped[str | None] = mapped_column(Text)
    is_guess: Mapped[bool] = mapped_column(Boolean, default=False)
    is_role: Mapped[bool | None] = mapped_column(Boolean)
    verification: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[int | None] = mapped_column(Integer)
    found_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime)


class LeadStatus(Base):
    """Mini-CRM pipeline state: New -> Preview Built -> Emailed -> Replied -> Won / Lost."""

    __tablename__ = "lead_status"

    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="New")
    notes: Mapped[str | None] = mapped_column(Text)
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ApiUsage(Base):
    """One row per external API call, for the credits panel."""

    __tablename__ = "api_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    endpoint: Mapped[str] = mapped_column(String(100))
    units: Mapped[int] = mapped_column(Integer, default=1)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class GeoCache(Base):
    """Cached geocoding results (e.g. ``zip:10001``)."""

    __tablename__ = "geo_cache"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    payload: Mapped[Any] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DomainCheck(Base):
    """Cached per-domain facts (MX records, catch-all ...) shared by all businesses."""

    __tablename__ = "domain_checks"

    domain: Mapped[str] = mapped_column(String(255), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), primary_key=True)
    payload: Mapped[Any] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SerpSnapshot(Base):
    """Cached Google results page (ads) for one keyword + location, shared by all businesses there."""

    __tablename__ = "serp_snapshots"
    __table_args__ = (Index("ix_serp_lookup", "keyword_norm", "location", "provider", "fetched_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    keyword_norm: Mapped[str] = mapped_column(String(255))
    location: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(30))
    payload: Mapped[Any] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LeadEvent(Base):
    """History of status changes and notes (CRM timeline)."""

    __tablename__ = "lead_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    status: Mapped[str | None] = mapped_column(String(30))
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    """Background work (discover runs etc.) with progress log; resumable."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    params: Mapped[Any] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|running|done|failed|cancelled
    progress: Mapped[Any] = mapped_column(JSON, nullable=True)        # list of log lines (last 300)
    done_steps: Mapped[Any] = mapped_column(JSON, nullable=True)      # e.g. ZIPs finished (for resume)
    result: Mapped[Any] = mapped_column(JSON, nullable=True)
    live: Mapped[Any] = mapped_column(JSON, nullable=True)            # rows shown while the job runs
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int | None] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)


class Suppression(Base):
    """Do-not-contact list: unsubscribes, bounces, manual blocks. ``value`` is an email or a bare domain."""

    __tablename__ = "suppressions"

    id: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OutboundEmail(Base):
    """One approved email (step 0 = first email, 1.. = follow-ups). Only rows you approved ever get sent."""

    __tablename__ = "outbound_emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    to_email: Mapped[str] = mapped_column(String(255), index=True)
    step: Mapped[int] = mapped_column(Integer, default=0)
    angle: Mapped[str | None] = mapped_column(String(30))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    delay_days: Mapped[int | None] = mapped_column(Integer, default=0)
    # approved -> sent | failed | cancelled ; follow-ups wait as "scheduled" until step 0 is sent
    status: Mapped[str] = mapped_column(String(20), default="approved", index=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    message_id: Mapped[str | None] = mapped_column(String(255))
    attachment: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class GridCellCache(Base):
    """Results of one grid cell, so an interrupted ZIP scan resumes without re-running finished cells."""

    __tablename__ = "grid_cell_cache"

    key: Mapped[str] = mapped_column(String(300), primary_key=True)   # provider|keyword|cell
    records: Mapped[Any] = mapped_column(JSON, nullable=True)
    exhausted: Mapped[bool] = mapped_column(Boolean, default=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AdSweep(Base):
    """One ads-first run: which businesses advertised for which searches, where (for 'new advertiser' alerts)."""

    __tablename__ = "ad_sweeps"

    id: Mapped[int] = mapped_column(primary_key=True)
    keyword: Mapped[str] = mapped_column(String(200), index=True)
    locations: Mapped[Any] = mapped_column(JSON, nullable=True)      # ["New York, NY", ...]
    queries: Mapped[Any] = mapped_column(JSON, nullable=True)        # search phrases used
    searches: Mapped[int | None] = mapped_column(Integer, default=0)
    failed: Mapped[int | None] = mapped_column(Integer, default=0)
    advertisers: Mapped[Any] = mapped_column(JSON, nullable=True)    # [{business_id, name, domain, kinds, hits, new}]
    watch_id: Mapped[int | None] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Watch(Base):
    """A saved keyword + places to re-check every week for new advertisers."""

    __tablename__ = "watches"

    id: Mapped[int] = mapped_column(primary_key=True)
    keyword: Mapped[str] = mapped_column(String(200))
    locations: Mapped[Any] = mapped_column(JSON, nullable=True)
    variations: Mapped[int | None] = mapped_column(Integer, default=4)
    every_days: Mapped[int | None] = mapped_column(Integer, default=7)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Alert(Base):
    """Something worth your attention, e.g. a business that started advertising this week."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), default="new_advertiser")
    business_id: Mapped[int | None] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    watch_id: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
    seen: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class RankGrid(Base):
    """One geo-grid rank run: Google Maps results searched from every point of an N×N grid."""

    __tablename__ = "rank_grids"

    id: Mapped[int] = mapped_column(primary_key=True)
    keyword: Mapped[str] = mapped_column(String(200), index=True)
    label: Mapped[str | None] = mapped_column(String(200))
    center_lat: Mapped[float] = mapped_column(Float)
    center_lng: Mapped[float] = mapped_column(Float)
    size: Mapped[int] = mapped_column(Integer, default=7)
    spacing_km: Mapped[float] = mapped_column(Float, default=1.0)
    zoom: Mapped[int | None] = mapped_column(Integer)
    focus_business_id: Mapped[int | None] = mapped_column(Integer)
    points: Mapped[Any] = mapped_column(JSON, nullable=True)     # [{r, c, lat, lng, ranks: [business ids], error}]
    summary: Mapped[Any] = mapped_column(JSON, nullable=True)    # leaderboard (see geo.rankgrid.summarize)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
