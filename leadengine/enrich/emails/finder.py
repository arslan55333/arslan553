"""Email finder pipeline for one website:
crawl -> extract -> filter -> people -> (Facebook) -> guesses -> verify -> confidence score."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

from leadengine.enrich.emails.crawl import CrawlResult, Page, SiteCrawler
from leadengine.enrich.emails.extract import Candidate, extract_candidates
from leadengine.enrich.emails.filters import (
    domain_of,
    is_free_mail,
    is_role,
    local_of,
    reject_reason,
    same_site,
)
from leadengine.enrich.emails.guess import guess_emails, render
from leadengine.enrich.emails.people import Person, people_from_html, rank_people
from leadengine.enrich.emails.verify import EmailVerifier, Verification
from leadengine.log import get_logger

log = get_logger("emails")

METHOD_BASE = {"mailto": 70, "jsonld": 70, "cfemail": 68, "text": 62, "attr": 55, "obfuscated": 58,
               "script": 40, "comment": 30, "facebook": 52}
PAGE_BONUS = {"contact": 15, "about": 8, "team": 8, "home": 6, "legal": 0, "other": 3, "facebook": 0}


@dataclass
class FoundEmail:
    email: str
    confidence: int
    source: str                 # human-readable: "mailto on contact page", "guessed first.last@ ..."
    method: str
    source_url: str | None
    is_guess: bool
    is_role: bool
    own_domain: bool
    verification: dict[str, Any] | None = None
    pages: int = 1


@dataclass
class EmailReport:
    website: str
    emails: list[FoundEmail] = field(default_factory=list)
    people: list[dict[str, Any]] = field(default_factory=list)
    pages_crawled: list[str] = field(default_factory=list)
    facebook_urls: list[str] = field(default_factory=list)
    reachable: bool = False
    ssl_error: bool = False
    redirected_to: str | None = None
    rejected: dict[str, str] = field(default_factory=dict)   # email -> reason (debugging)
    error: str | None = None

    @property
    def best(self) -> FoundEmail | None:
        real = [e for e in self.emails if not e.is_guess and e.confidence >= 30]
        return real[0] if real else None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["best"] = asdict(self.best) if self.best else None
        return data


def _page_label(page: Page) -> str:
    path = urlsplit(page.final_url).path or "/"
    return "homepage" if page.kind == "home" else f"{page.kind} page ({path})"


class EmailFinder:
    def __init__(self, crawler: SiteCrawler, verifier: EmailVerifier, cfg: dict[str, Any]) -> None:
        self.crawler = crawler
        self.verifier = verifier
        self.facebook = bool(cfg.get("facebook", True))
        self.guess = bool(cfg.get("guess_patterns", True))
        self.verify_guesses = bool(cfg.get("verify_guesses", True))

    async def find(self, website: str, owner_hint: str | None = None) -> EmailReport:
        crawl = await self.crawler.crawl(website)
        report = EmailReport(website=website, reachable=crawl.reachable, ssl_error=crawl.ssl_error,
                             redirected_to=crawl.redirected_to, facebook_urls=crawl.facebook_urls,
                             error=crawl.error)
        if not crawl.reachable:
            return report
        if self.facebook and crawl.facebook_urls:
            await self._facebook(crawl)
        report.pages_crawled = [p.final_url for p in crawl.pages]

        # ── candidates -> grouped by address
        grouped: dict[str, list[tuple[Candidate, Page]]] = {}
        for page in crawl.pages:
            for cand in extract_candidates(page.html, page.final_url):
                if page.kind == "facebook":
                    cand.method = "facebook"
                reason = reject_reason(cand.email)
                if reason:
                    report.rejected.setdefault(cand.email, reason)
                    continue
                grouped.setdefault(cand.email, []).append((cand, page))

        # ── people
        people: list[Person] = []
        for page in crawl.pages:
            if page.kind in ("home", "about", "team", "contact"):
                for p in people_from_html(page.html, page.final_url):
                    if not any((p.first, p.last) == (q.first, q.last) for q in people):
                        people.append(p)
        people = rank_people(people)
        report.people = [{"name": p.full, "title": p.title, "source": p.source, "url": p.url} for p in people[:5]]

        own_found = any(same_site(e, crawl.site_domains) for e in grouped)
        found = [self._score(email, hits, crawl, own_found, people) for email, hits in grouped.items()]

        # ── guesses (only on the business's own domain)
        if self.guess and crawl.site_domains:
            domain = sorted(crawl.site_domains, key=len)[0]
            names = [(p.first, p.last) for p in people[:2]]
            if owner_hint and not names:
                parts = owner_hint.split()
                if len(parts) >= 2:
                    names = [(parts[0], parts[-1])]
            for email, why in guess_emails(domain, names, list(grouped)):
                if reject_reason(email):
                    continue
                learned = "pattern seen on site" in why
                found.append(FoundEmail(email, 32 if learned else 22, f"guessed: {why}", "guess", None,
                                        True, is_role(email), True))

        await self._verify_all(found)
        found.sort(key=lambda e: (-e.confidence, e.is_guess, e.email))
        report.emails = found
        return report

    def _score(self, email: str, hits: list[tuple[Candidate, Page]], crawl: CrawlResult,
               own_found: bool, people: list[Person]) -> FoundEmail:
        best_cand, best_page = max(hits, key=lambda h: METHOD_BASE.get(h[0].method, 40) + PAGE_BONUS.get(h[1].kind, 0))
        score = METHOD_BASE.get(best_cand.method, 40) + PAGE_BONUS.get(best_page.kind, 0)
        pages = len({p.final_url for _, p in hits})
        score += min(10, 5 * (pages - 1))
        own = same_site(email, crawl.site_domains)
        if own:
            score += 15
        elif is_free_mail(email):
            score += -12 if own_found else 4      # many small businesses really use gmail
        else:
            # another company's domain: usually the web designer / platform in the footer
            on_contact = best_page.kind == "contact" and best_cand.method in ("mailto", "text")
            score -= 18 if on_contact else 40
        local = local_of(email)
        for p in people[:3]:
            if local in {render(x, p.first, p.last) for x in ("first", "first.last", "firstlast", "flast", "f.last")}:
                score += 8
                break
        label = ("on Facebook page" if best_cand.method == "facebook"
                 else f"{best_cand.method} on {_page_label(best_page)}")
        return FoundEmail(email, max(0, min(100, score)), label, best_cand.method, best_page.final_url,
                          False, is_role(email), own, pages=pages)

    async def _verify_all(self, found: list[FoundEmail]) -> None:
        targets = [f for f in found if not f.is_guess or self.verify_guesses]
        results = await asyncio.gather(*(self.verifier.verify(f.email) for f in targets), return_exceptions=True)
        for f, v in zip(targets, results):
            if isinstance(v, BaseException):
                v = Verification("unknown", f"verification error: {type(v).__name__}", "mx")
            f.verification = v.as_dict()
            if v.status == "invalid":
                f.confidence = 0 if f.is_guess else min(f.confidence, 10)
            elif v.status == "valid":
                f.confidence = min(100, f.confidence + (25 if f.is_guess else 12))
            elif v.status == "catch_all":
                f.confidence = min(f.confidence, 35 if f.is_guess else 75)
            elif v.status == "risky":
                f.confidence = min(f.confidence, 55)
            if f.is_guess and v.status != "valid":
                f.confidence = min(f.confidence, 35)   # never let an unproven guess look certain

    async def _facebook(self, crawl: CrawlResult) -> None:
        """Logged-out Facebook pages sometimes show the email in the intro/about JSON. Best effort."""
        for url in crawl.facebook_urls[:1]:
            page = await self.crawler.fetch(url, crawl)
            if page is not None:
                page.kind = "facebook"
                # Facebook HTML escapes '@' as @ inside JSON
                page.html = page.html.replace("\\u0040", "@").replace("\\u0025", "%")
                crawl.pages.append(page)
