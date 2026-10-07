"""Website analysis pipeline for one business: fetch -> HTML signals -> tech -> remote checks
(certificate, PageSpeed, Wayback, sitemap) -> browser render (screenshots, mobile) -> AI design
review (optional) -> score."""

from __future__ import annotations

import asyncio
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from leadengine.config import Settings
from leadengine.enrich.emails.crawl import CrawlResult, SiteCrawler
from leadengine.enrich.website import remote
from leadengine.enrich.website.render import WebsiteRenderer
from leadengine.enrich.website.score import compute_score
from leadengine.enrich.website.signals import analyze_html
from leadengine.enrich.website.tech import load_fingerprints
from leadengine.http import HttpClient
from leadengine.llm import LLM, LLMError
from leadengine.log import get_logger
from leadengine.normalize import is_shared_domain, normalize_domain

log = get_logger("website")

BUILDER_SUBDOMAINS = ("wixsite.com", "weebly.com", "godaddysites.com", "business.site", "square.site",
                      "squarespace.com", "wordpress.com", "blogspot.com", "jimdosite.com", "site123.me")
JS_PROPS = ["jQuery.fn.jquery", "$.fn.jquery", "jQuery.prototype.jquery"]

VISION_PROMPT = """You are reviewing the homepage screenshot of a small local service business ({name}).
Rate how OUTDATED the design looks on a 1-10 scale (1 = modern, current best practice; 10 = looks like the 2000s).
Consider layout, typography, imagery, colours, spacing, and whether it would convert visitors into calls.
Return JSON: {{"outdated_1_10": <int>, "era": "<e.g. 2008-2012 style>", "why": "<one sentence, concrete>",
"top_fixes": ["<fix>", "<fix>", "<fix>"]}}"""


class WebsiteAnalyzer:
    def __init__(self, settings: Settings, http: HttpClient, *, renderer: WebsiteRenderer | None = None,
                 llm: LLM | None = None, crawler: SiteCrawler | None = None,
                 screenshot_dir: Path | None = None, today: date | None = None, firecrawl=None) -> None:
        self.settings = settings
        self.cfg = settings.section("website")
        self.http = http
        self.renderer = renderer
        self.llm = llm
        self.crawler = crawler or SiteCrawler(http, max_pages=1, timeout=float(self.cfg.get("timeout_seconds", 15)),
                                              firecrawl=firecrawl)
        self.screenshot_dir = screenshot_dir or settings.root / "data" / "screenshots"
        self.today = today or date.today()

    async def aclose(self) -> None:
        await self.crawler.aclose()
        if self.renderer is not None:
            await self.renderer.aclose()

    async def analyze(self, website: str | None, *, key: str, name: str = "") -> dict[str, Any]:
        """Full analysis. ``key`` names the screenshot files (e.g. the business id)."""
        if not website:
            return {"score": None, "grade": "n/a", "flags": ["no_website"], "reasons": ["no website"]}
        domain = normalize_domain(website)
        flags: list[str] = []
        if is_shared_domain(domain):
            flag = "facebook_only" if domain and "facebook" in domain else "social_or_directory_only"
            return {"score": None, "grade": "n/a", "flags": [flag], "reasons": [f"no real website ({domain})"]}
        if domain and domain.endswith(BUILDER_SUBDOMAINS):
            flags.append("builder_subdomain")

        crawl = CrawlResult(start_url=website)
        page = await self.crawler._home(website, crawl)
        if page is None:
            if crawl.last_status in (401, 403, 429, 503):        # it's up, it just refuses robots: not a lead signal
                return {"score": None, "grade": "n/a", "flags": flags + ["blocks_bots"], "domain": domain,
                        "reasons": [f"the website blocks automatic checks (HTTP {crawl.last_status}) — check it by hand"
                                    + ("" if self.crawler.firecrawl else ", or add a Firecrawl key in Settings")]}
            return {"score": None, "grade": "n/a", "flags": flags + ["broken"],
                    "reasons": ["website does not load"], "domain": domain}
        if crawl.via_firecrawl:
            flags.append("read_via_firecrawl")
        if crawl.ssl_error:
            flags.append("ssl_invalid")
        final_domain = normalize_domain(page.final_url)
        if final_domain and domain and final_domain != domain and not final_domain.endswith("." + domain):
            flags.append("redirects_elsewhere")

        html = analyze_html(page.html, self.today.year)
        from leadengine.enrich.website.agency import detect_agency
        agency = detect_agency(page.html)
        if html.parked:
            flags.append("parked")
        if html.default_page:
            flags.append("server_default_page")

        host = urlsplit(page.final_url).hostname or domain
        base = f"{urlsplit(page.final_url).scheme}://{urlsplit(page.final_url).netloc}"
        tasks: dict[str, Any] = {"cert": remote.check_certificate(host)}
        if self.cfg.get("pagespeed", True):
            tasks["pagespeed"] = remote.pagespeed(self.http, page.final_url, self.settings.pagespeed_api_key)
        if self.cfg.get("wayback", True):
            tasks["wayback"] = remote.wayback_span(self.http, domain)
        tasks["sitemap"] = remote.sitemap_lastmod(self.http, base)
        if self.renderer is not None and self.cfg.get("screenshot", True):
            shot = self.screenshot_dir / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', key)}.jpg"
            tasks["render"] = self.renderer.render(page.final_url, shot, JS_PROPS)
        results = dict(zip(tasks, await asyncio.gather(*tasks.values(), return_exceptions=True)))
        for k, v in list(results.items()):
            if isinstance(v, BaseException):
                log.warning("website check failed", extra={"data": {"check": k, "site": domain, "error": str(v)[:150]}})
                results[k] = None
        cert, render = results.get("cert"), results.get("render")

        fp = load_fingerprints()
        techs = fp.detect(html=page.html, script_srcs=html.script_srcs, inline_scripts=html.inline_scripts,
                          meta=html.meta, headers=page.headers, cookies=page.cookies,
                          js=(render or {}).get("js"))
        if any("Domain parking" == t.name for t in techs) and "parked" not in flags and html.word_count < 400:
            flags.append("parked")

        vision = None
        if self.llm is not None and self.cfg.get("vision", False) and (render or {}).get("screenshot"):
            try:
                img = Path(render["screenshot"]).read_bytes()
                vision = await self.llm.complete_json(VISION_PROMPT.format(name=name or domain), images=[img],
                                                      max_tokens=800)
            except (LLMError, OSError, ValueError) as exc:
                log.warning("AI design review failed", extra={"data": {"site": domain, "error": str(exc)[:150]}})

        https_ok = page.final_url.startswith("https://") and bool((cert or {}).get("valid")) and not crawl.ssl_error
        score = compute_score(
            html, techs, today=self.today, https_ok=https_ok, cert=cert, render=render,
            pagespeed=results.get("pagespeed") if (results.get("pagespeed") or {}).get("performance") is not None else None,
            sitemap_lastmod=results.get("sitemap"), last_modified_header=page.headers.get("last-modified"),
            wayback=results.get("wayback"), vision=vision, flags=flags,
        )
        if "parked" in flags or "server_default_page" in flags:
            score.score = min(score.score or 0, 10)
            score.grade = "Outdated"
            score.reasons.insert(0, "parked / placeholder page, not a real website")
        payload = score.as_dict()
        payload.update({
            "domain": domain, "final_url": page.final_url,
            "tech": [{"name": t.name, "version": t.version, "categories": t.categories} for t in techs],
            "html": html.summary(), "certificate": cert, "pagespeed": results.get("pagespeed"),
            "wayback": results.get("wayback"), "sitemap_lastmod": results.get("sitemap"),
            "screenshot": (render or {}).get("screenshot"), "mobile_screenshot": (render or {}).get("mobile_screenshot"),
            "mobile": (render or {}).get("mobile"), "vision": vision, "checked_on": self.today.isoformat(),
            "agency": agency,
        })
        return payload
