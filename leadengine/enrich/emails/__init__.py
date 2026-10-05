"""Email discovery, verification and scoring (Phase 3)."""

from __future__ import annotations

import httpx

from leadengine.config import Settings
from leadengine.enrich.emails.crawl import SiteCrawler
from leadengine.enrich.emails.finder import EmailFinder, EmailReport, FoundEmail
from leadengine.enrich.emails.verify import EmailVerifier
from leadengine.http import HttpClient


def build_email_finder(
    settings: Settings,
    http: HttpClient,
    repo=None,
    *,
    insecure_transport: httpx.AsyncBaseTransport | None = None,
    verifier: EmailVerifier | None = None,
) -> EmailFinder:
    cfg = settings.section("emails")
    crawler = SiteCrawler(http, max_pages=int(cfg.get("max_pages", 8)),
                          timeout=float(cfg.get("timeout_seconds", 12)), insecure_transport=insecure_transport)
    verifier = verifier or EmailVerifier(cfg, http, repo, reacher_secret=settings.reacher_secret,
                                         domain_ttl_days=settings.ttl("domain"))
    return EmailFinder(crawler, verifier, cfg)


__all__ = ["EmailFinder", "EmailReport", "EmailVerifier", "FoundEmail", "SiteCrawler", "build_email_finder"]
