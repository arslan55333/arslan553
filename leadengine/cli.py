"""Command line interface. Run ``python -m leadengine --help``."""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from leadengine.config import Settings
from leadengine.credits import CreditTracker
from leadengine.db import Repository, init_db, make_engine, make_session_factory
from leadengine.errors import LeadEngineError, ProviderAuthError, ProviderNotConfigured
from leadengine.http import HttpClient
from leadengine.log import setup_logging
from leadengine.models import SearchQuery
from leadengine.geo.grid import plan_cells
from leadengine.geo.zipdata import zip_directory
from leadengine.normalize import normalize_zip
from leadengine.proxy import ProxyPool
from leadengine.providers import PROVIDERS, build_provider
from leadengine.providers.serpapi import SerpApiProvider
from leadengine.service import LeadService

app = typer.Typer(
    help="LeadEngine - Google Maps lead intelligence (Phase 1: foundation).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _bootstrap(verbose: bool = False):
    settings = Settings.load()
    setup_logging("DEBUG" if verbose else settings.log_level, settings.log_dir)
    engine = make_engine(settings.database_url)
    init_db(engine)
    return settings, make_session_factory(engine)


def _fail(message: str) -> None:
    console.print(f"[bold red]Error:[/] {message}")
    raise typer.Exit(1)


def _short_url(url: str | None, width: int = 32) -> str:
    if not url:
        return "-"
    text = url.split("://", 1)[-1].removeprefix("www.").rstrip("/")
    return text if len(text) <= width else text[: width - 1] + "~"


@app.command()
def init() -> None:
    """Create the database and show where everything lives."""
    settings, _ = _bootstrap()
    console.print(f"[green]Database ready:[/] {settings.database_url}")
    console.print(f"Logs: {settings.log_dir / 'leadengine.jsonl'}")
    if not (settings.root / ".env").exists():
        console.print("[yellow]No .env file yet.[/] Copy .env.example to .env and add your API keys.")


@app.command()
def providers() -> None:
    """List data sources and whether each one is ready to use."""
    settings, sf = _bootstrap()

    async def _run():
        async with HttpClient(settings.http) as http:
            return [build_provider(name, settings, http) for name in PROVIDERS]

    table = Table(title="Providers")
    for col in ("name", "description", "paid", "est. $/call", "free/month", "status"):
        table.add_column(col)
    for p in asyncio.run(_run()):
        ready, reason = p.configured()
        cfg = settings.provider(p.name)
        table.add_row(
            p.name + (" (default)" if p.name == settings.default_provider else ""),
            p.label,
            "yes" if p.paid else "no",
            f"{cfg.cost_per_call_usd:.3f}",
            str(cfg.monthly_free_calls or "-"),
            f"[green]{reason}[/]" if ready else f"[red]{reason}[/]",
        )
    console.print(table)


@app.command()
def search(
    keyword: str = typer.Argument(..., help='What to search, e.g. "dumpster rental"'),
    zip_code: Optional[str] = typer.Option(None, "--zip", "-z", help="US ZIP code, e.g. 75201"),
    location: Optional[str] = typer.Option(None, "--location", "-l", help='Free text, e.g. "Dallas, TX"'),
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help=f"One of: {', '.join(PROVIDERS)}"),
    max_results: int = typer.Option(20, "--max", "-m", min=1, max=120, help="How many results (Google caps ~120)"),
    refresh: bool = typer.Option(False, "--refresh", help="Ignore the cache and pay for fresh data"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Find businesses for KEYWORD in a ZIP. Re-running the same search uses the cache (0 credits)."""
    if not zip_code and not location:
        _fail("give --zip or --location")
    if zip_code:
        try:
            zip_code = normalize_zip(zip_code)
        except ValueError as exc:
            _fail(str(exc))
    settings, sf = _bootstrap(verbose)
    credits = CreditTracker(sf, settings)
    query = SearchQuery(keyword=keyword, zip_code=zip_code, location=location, max_results=max_results)

    async def _run():
        async with HttpClient(settings.http, user_agent=settings.user_agent) as http:
            service = LeadService(settings, sf, http, credits)
            try:
                return await service.search(query, provider, refresh=refresh)
            finally:
                await service.aclose()

    try:
        outcome = asyncio.run(_run())
    except ProviderNotConfigured as exc:
        _fail(f"{exc}. Run `python -m leadengine providers` to see what is set up.")
    except ProviderAuthError as exc:
        _fail(f"API key rejected - {exc}")
    except LeadEngineError as exc:
        _fail(str(exc))

    table = Table(title=f"{keyword} - {query.location_text()}  [{outcome.provider}]")
    for col, kw in (("#", {"justify": "right"}), ("Name", {}), ("Rating", {"justify": "right"}),
                    ("Reviews", {"justify": "right"}), ("Phone", {}), ("Website", {}), ("ZIP", {})):
        table.add_column(col, **kw)
    for biz, rank in outcome.results:
        table.add_row(
            str(rank), biz.name[:45],
            f"{biz.rating:.1f}" if biz.rating is not None else "-",
            str(biz.review_count) if biz.review_count is not None else "-",
            biz.phone or "-", _short_url(biz.website), biz.zip_code or "-",
        )
    console.print(table)
    if outcome.from_cache:
        console.print(f"[bold green]From CACHE[/] - 0 API calls (search #{outcome.search_id}). Use --refresh to re-fetch.")
    else:
        console.print(f"[bold cyan]LIVE[/] - {outcome.api_calls} API call(s), {len(outcome.results)} businesses saved (search #{outcome.search_id}).")
    if outcome.skipped:
        console.print(f"[yellow]{outcome.skipped} record(s) skipped - see logs.[/]")


@app.command()
def discover(
    keyword: str = typer.Argument(..., help='What to search, e.g. "dumpster rental"'),
    zip_code: str = typer.Option(..., "--zip", "-z", help="US ZIP code"),
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help="Discovery source (default from config: playwright)"),
    towns: Optional[bool] = typer.Option(None, "--towns/--no-towns", help="Also search each town in/around the ZIP"),
    activity: Optional[bool] = typer.Option(None, "--activity/--no-activity", help="Open shortlisted place pages for review dates, owner replies, claimed, photos"),
    fill: Optional[bool] = typer.Option(None, "--fill/--no-fill", help="Use the paid API to fill missing phone/website for shortlisted leads"),
    emails: Optional[bool] = typer.Option(None, "--emails/--no-emails", help="Find emails on shortlisted websites"),
    website: Optional[bool] = typer.Option(None, "--website/--no-website", help="Score shortlisted websites 0-100"),
    ads: Optional[bool] = typer.Option(None, "--ads/--no-ads", help="Detect Google Ads / Local Services Ads"),
    cell_km: Optional[float] = typer.Option(None, "--cell-km", help="Starting grid cell size in km"),
    max_depth: Optional[int] = typer.Option(None, "--max-depth", help="How often saturated cells may split"),
    max_cells: Optional[int] = typer.Option(None, "--max-cells", help="Cap on searches this run"),
    show: int = typer.Option(30, "--show", help="Rows to print"),
    refresh: bool = typer.Option(False, "--refresh", help="Ignore cached discovery"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask before spending paid credits"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Cover a whole ZIP: free adaptive-grid scraping, then paid API only for shortlisted gaps."""
    try:
        zip_code = normalize_zip(zip_code)
    except ValueError as exc:
        _fail(str(exc))
    settings, sf = _bootstrap(verbose)
    credits = CreditTracker(sf, settings)
    cfg = settings.section("discovery")
    chosen = provider or cfg.get("provider", "playwright")
    if chosen in PROVIDERS and PROVIDERS[chosen].paid and not yes:
        cls = PROVIDERS[chosen]
        cells_cap = max_cells or int(cfg.get("max_cells", 40))
        pages = max(1, cls.max_per_query // 20)
        price = settings.provider(chosen).cost_per_call_usd
        console.print(f"[yellow]{chosen} is a paid API.[/] A grid scan can use up to {cells_cap} cells x {pages} "
                      f"pages = {cells_cap * pages} calls (~${cells_cap * pages * price:.2f}) if every cell is dense. "
                      "Cached results are reused for free.")
        if not typer.confirm("Continue?", default=False):
            raise typer.Exit(0)

    async def _run():
        async with HttpClient(settings.http, user_agent=settings.user_agent) as http:
            service = LeadService(settings, sf, http, credits)
            try:
                return await service.discover(
                    keyword, zip_code, provider_name=provider, refresh=refresh, towns=towns, activity=activity,
                    fill=fill, emails=emails, website=website, ads=ads, cell_km=cell_km, max_depth=max_depth, max_cells=max_cells,
                    on_progress=lambda m: console.print(f"[dim]{m}[/]"),
                )
            finally:
                await service.aclose()

    try:
        out = asyncio.run(_run())
    except ProviderNotConfigured as exc:
        _fail(f"{exc}. Run `python -m leadengine providers` to see what is set up.")
    except LeadEngineError as exc:
        _fail(str(exc))

    rows = sorted(out.results, key=lambda t: (-(t[0].review_count or 0), t[1]))
    table = Table(title=f"{keyword} - ZIP {zip_code}  [{out.provider}]  {len(out.results)} businesses")
    for col in ("Name", "Rating", "Reviews", "Last review", "Replies", "Ads", "Site score", "Website", "Email"):
        table.add_column(col, justify="right" if col in ("Rating", "Reviews") else "left",
                         no_wrap=True, overflow="ellipsis", min_width=4 if col != "Name" else 12)
    for biz, _rank in rows[:show]:
        table.add_row(
            biz.name[:38], f"{biz.rating:.1f}" if biz.rating is not None else "-",
            str(biz.review_count) if biz.review_count is not None else "-",
            f"{biz.last_review_at:%Y-%m-%d}" if biz.last_review_at else "-",
            f"{biz.owner_response_rate:.0%}" if biz.owner_response_rate is not None else "-",
            _ads_cell(biz), _site_cell(biz), _short_url(biz.website, 26), _email_cell(biz),
        )
    console.print(table)
    if out.from_cache:
        console.print("[bold green]From CACHE[/] - discovery reused, 0 searches.")
    elif out.grid:
        g = out.grid
        console.print(f"[bold cyan]Scanned[/] {g.cells_run} cell(s), split {g.cells_split}, "
                      f"failed {g.cells_failed}, still-saturated {g.saturated_leaves}, skipped by budget {g.cells_skipped}.")
        for err in g.errors[:3]:
            console.print(f"[yellow]  {err}[/]")
    console.print(f"In/near ZIP: {out.in_area}  |  Sponsored (running Maps ads): {out.sponsored}  |  "
                  f"Shortlisted: {out.shortlisted}  |  Activity pages opened: {out.activity_checked} "
                  f"(cached {out.activity_cached})  |  Paid fills: {out.filled} using {out.paid_calls} credit(s) "
                  f"(cached {out.fill_cached})")
    if out.emails:
        e = out.emails
        console.print(f"Emails: {e.with_email} of {e.with_email + e.guesses_only + e.unreachable} shortlisted sites "
                      f"(crawled {e.checked}, cached {e.cached}, unreachable {e.unreachable}, guesses only {e.guesses_only})")
    if out.skipped:
        console.print(f"[yellow]{out.skipped} record(s) skipped - see logs.[/]")


def _ads_cell(biz) -> str:
    if not biz.ads_status:
        return "-"
    return biz.ads_status + (" +LSA" if biz.lsa else "")


def _site_cell(biz) -> str:
    flags = biz.website_flags or []
    for f in ("no_website", "facebook_only", "broken", "parked"):
        if f in flags:
            return f.replace("_", " ")
    return f"{biz.website_score} {biz.website_grade}" if biz.website_score is not None else "-"


def _email_cell(biz) -> str:
    if not biz.best_email:
        return "-"
    mark = {"valid": " ✓", "invalid": " ✗", "catch_all": " ~"}.get(biz.email_status or "", "")
    return f"{biz.best_email} ({biz.email_confidence}){mark}"


def _print_email_rows(rows: list[dict], title: str) -> None:
    table = Table(title=title)
    for col in ("Business / site", "Best email", "Conf.", "Verified", "Found as", "Owner", "Other emails"):
        table.add_column(col, no_wrap=col not in ("Other emails", "Found as"), overflow="ellipsis")
    for r in rows:
        best = r.get("best")
        others = ", ".join(
            f"{o['email']} ({o['confidence']}{', guess' if o['is_guess'] else ''})" for o in r.get("others", []))
        table.add_row(
            (r.get("name") or r.get("website") or "")[:34],
            best["email"] if best else ("[dim]site unreachable[/]" if r.get("reachable") is False else "-"),
            str(best["confidence"]) if best else "-",
            ((best.get("verification") or {}).get("status") or "-") if best else "-",
            best["source"] if best else "-",
            r.get("owner") or "-",
            others or "-",
        )
    console.print(table)


@app.command()
def emails(
    zip_code: Optional[str] = typer.Option(None, "--zip", "-z"),
    keyword: Optional[str] = typer.Option(None, "--keyword", "-k"),
    min_reviews: Optional[int] = typer.Option(None, "--min-reviews"),
    min_rating: Optional[float] = typer.Option(None, "--min-rating"),
    all_leads: bool = typer.Option(False, "--all", help="Ignore the shortlist thresholds"),
    limit: int = typer.Option(50, "--limit"),
    refresh: bool = typer.Option(False, "--refresh", help="Re-crawl even if cached"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Find + verify emails for saved businesses (websites crawled once, then cached)."""
    settings, sf = _bootstrap(verbose)
    disc = settings.section("discovery")
    if not all_leads and settings.section("emails").get("shortlist_only", True):
        min_reviews = min_reviews if min_reviews is not None else int(disc.get("shortlist_min_reviews", 20))
        min_rating = min_rating if min_rating is not None else float(disc.get("shortlist_min_rating", 4.0))
    with sf() as s:
        rows = Repository(s).list_businesses(keyword=keyword, zip_code=zip_code, min_rating=min_rating,
                                             min_reviews=min_reviews, has_website=True, limit=limit)
        ids = [b.id for b in rows]
    if not ids:
        _fail("no saved businesses with a website match these filters (run `discover` first, or use --all)")
    console.print(f"Finding emails for {len(ids)} businesses...")

    async def _run():
        async with HttpClient(settings.http, user_agent=settings.user_agent) as http:
            return await LeadService(settings, sf, http, CreditTracker(sf, settings)).find_emails(
                ids, refresh=refresh, on_progress=(lambda m: console.print(f"[dim]{m}[/]")) if verbose else None)

    summary = asyncio.run(_run())
    _print_email_rows(summary.rows, f"Emails ({summary.with_email}/{len(ids)} businesses)")
    console.print(f"Crawled {summary.checked}, from cache {summary.cached}, unreachable {summary.unreachable}, "
                  f"guesses only {summary.guesses_only}, verified deliverable {summary.valid}.")


@app.command(name="find-email")
def find_email(
    sites: list[str] = typer.Argument(None, help="Domains or URLs"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Text file, one domain per line"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Save results to CSV"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Find emails for any list of websites (replaces v3's Bulk Email Finder)."""
    from leadengine.enrich.emails import build_email_finder

    targets = list(sites or [])
    if file:
        targets += [l.strip() for l in file.read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.strip().startswith("#")]
    if not targets:
        _fail("give one or more domains, or --file")
    settings, sf = _bootstrap(verbose)
    concurrency = int(settings.section("emails").get("concurrency", 5))

    async def _run():
        async with HttpClient(settings.http, user_agent=settings.user_agent) as http:
            with sf() as s:
                finder = build_email_finder(settings, http, Repository(s))
                sem = asyncio.Semaphore(concurrency)

                async def one(site):
                    async with sem:
                        return await finder.find(site)
                try:
                    reports = await asyncio.gather(*(one(t) for t in targets))
                finally:
                    await finder.crawler.aclose()
                s.commit()
                return reports

    reports = asyncio.run(_run())
    rows = []
    for rep in reports:
        d = rep.as_dict()
        rows.append({"website": rep.website, "best": d["best"], "reachable": rep.reachable,
                     "owner": rep.people[0]["name"] if rep.people else None,
                     "others": [e for e in d["emails"] if not d["best"] or e["email"] != d["best"]["email"]][:3]})
    _print_email_rows(rows, f"Email finder ({sum(1 for r in rows if r['best'])}/{len(rows)} found)")
    if out:
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["website", "email", "confidence", "verification", "source", "is_guess", "owner"])
            for rep in reports:
                owner = rep.people[0]["name"] if rep.people else ""
                if not rep.emails:
                    w.writerow([rep.website, "", "", "", "unreachable" if not rep.reachable else "none found", "", owner])
                for e in rep.emails:
                    w.writerow([rep.website, e.email, e.confidence, (e.verification or {}).get("status", ""),
                                e.source, "yes" if e.is_guess else "", owner])
        console.print(f"[green]Saved ->[/] {out}")


@app.command()
def website(
    zip_code: Optional[str] = typer.Option(None, "--zip", "-z"),
    keyword: Optional[str] = typer.Option(None, "--keyword", "-k"),
    min_reviews: Optional[int] = typer.Option(None, "--min-reviews"),
    min_rating: Optional[float] = typer.Option(None, "--min-rating"),
    all_leads: bool = typer.Option(False, "--all", help="Ignore the shortlist thresholds"),
    limit: int = typer.Option(50, "--limit"),
    screenshots: Optional[bool] = typer.Option(None, "--screenshots/--no-screenshots"),
    vision: Optional[bool] = typer.Option(None, "--vision/--no-vision", help="AI design review (uses [llm])"),
    refresh: bool = typer.Option(False, "--refresh"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Website Score 0-100 for saved businesses: why each site is weak, with screenshots."""
    settings, sf = _bootstrap(verbose)
    disc = settings.section("discovery")
    if not all_leads and settings.section("website").get("shortlist_only", True):
        min_reviews = min_reviews if min_reviews is not None else int(disc.get("shortlist_min_reviews", 20))
        min_rating = min_rating if min_rating is not None else float(disc.get("shortlist_min_rating", 4.0))
    with sf() as s:
        ids = [b.id for b in Repository(s).list_businesses(keyword=keyword, zip_code=zip_code, min_rating=min_rating,
                                                           min_reviews=min_reviews, limit=limit)]
    if not ids:
        _fail("no saved businesses match (run `discover` first, or use --all)")
    console.print(f"Scoring {len(ids)} websites...")

    async def _run():
        async with HttpClient(settings.http, user_agent=settings.user_agent) as http:
            return await LeadService(settings, sf, http, CreditTracker(sf, settings)).score_websites(
                ids, refresh=refresh, render=screenshots, vision=vision,
                on_progress=(lambda m: console.print(f"[dim]{m}[/]")) if verbose else None)

    rows = sorted(asyncio.run(_run()), key=lambda r: (r.get("score") is not None, r.get("score") or 0))
    table = Table(title=f"Website scores ({len(rows)})")
    for col in ("Business", "Score", "Grade", "Main weaknesses", "Flags"):
        table.add_column(col, overflow="fold" if col == "Main weaknesses" else "ellipsis",
                         no_wrap=col not in ("Main weaknesses",))
    for r in rows:
        table.add_row(r["name"][:30], str(r.get("score") if r.get("score") is not None else "-"),
                      r.get("grade", "-"), "; ".join((r.get("reasons") or [])[:3]) or r.get("error", "-"),
                      ", ".join(r.get("flags") or []) or "-")
    console.print(table)


@app.command(name="ads")
def ads_cmd(
    zip_code: Optional[str] = typer.Option(None, "--zip", "-z"),
    keyword: Optional[str] = typer.Option(None, "--keyword", "-k", help="Search keyword for the live ad check"),
    min_reviews: Optional[int] = typer.Option(None, "--min-reviews"),
    min_rating: Optional[float] = typer.Option(None, "--min-rating"),
    all_leads: bool = typer.Option(False, "--all"),
    limit: int = typer.Option(50, "--limit"),
    refresh: bool = typer.Option(False, "--refresh"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Who runs Google Ads? Live search ads + Local Services Ads + ad tags on the website."""
    settings, sf = _bootstrap(verbose)
    disc = settings.section("discovery")
    if not all_leads:
        min_reviews = min_reviews if min_reviews is not None else int(disc.get("shortlist_min_reviews", 20))
        min_rating = min_rating if min_rating is not None else float(disc.get("shortlist_min_rating", 4.0))
    with sf() as s:
        ids = [b.id for b in Repository(s).list_businesses(keyword=keyword, zip_code=zip_code, min_rating=min_rating,
                                                           min_reviews=min_reviews, limit=limit)]
    if not ids:
        _fail("no saved businesses match (run `discover` first, or use --all)")

    async def _run():
        async with HttpClient(settings.http, user_agent=settings.user_agent) as http:
            service = LeadService(settings, sf, http, CreditTracker(sf, settings))
            try:
                return await service.detect_ads(ids, keyword=keyword, refresh=refresh,
                                                on_progress=lambda m: console.print(f"[dim]{m}[/]"))
            finally:
                await service.aclose()

    rows = asyncio.run(_run())
    order = {"Active": 0, "Likely": 1, "Past": 2, "None": 3}
    table = Table(title=f"Google Ads status ({len(rows)})")
    for col in ("Business", "Ads", "LSA", "Conf.", "Meta ads", "Evidence"):
        table.add_column(col, overflow="fold" if col == "Evidence" else "ellipsis", no_wrap=col != "Evidence")
    for r in sorted(rows, key=lambda r: (order.get(r["status"], 9), -r["confidence"])):
        table.add_row(r["name"][:30], r["status"], "yes" if r["lsa"] else "-", str(r["confidence"]),
                      "yes" if r["meta_ads"] else "-", "; ".join(r["evidence"][:3]) or (r.get("serp_error") or "-"))
    console.print(table)


@app.command()
def ui(
    port: int = typer.Option(8765, "--port"),
    host: str = typer.Option("127.0.0.1", "--host", help="Keep 127.0.0.1 unless you know you want LAN access"),
    no_browser: bool = typer.Option(False, "--no-browser"),
) -> None:
    """Open the web dashboard (scans run in the background inside this process)."""
    import threading
    import webbrowser

    import uvicorn

    from leadengine.ui.app import create_app

    settings = Settings.load()
    setup_logging(settings.log_level, settings.log_dir)
    url = f"http://{host}:{port}"
    console.print(f"[green]LeadEngine dashboard:[/] {url}  (Ctrl+C to stop)")
    if not no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(settings), host=host, port=port, log_level="warning")


@app.command()
def worker(once: bool = typer.Option(False, "--once", help="Process the queue once and exit")) -> None:
    """Run queued jobs without the dashboard (e.g. on a server). Resumes interrupted jobs."""
    from leadengine.jobs import JobRunner, recover_stale
    from leadengine.workers import make_handlers

    settings, sf = _bootstrap()
    runner = JobRunner(sf, make_handlers(settings, sf))

    async def _run():
        if once:
            recover_stale(sf)
            while await runner.run_once():
                pass
        else:
            await runner.run_forever()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("stopped")


@app.command(name="update-fingerprints")
def update_fingerprints() -> None:
    """Download the open-source webappanalyzer fingerprints (GPL-3.0) for wider tech detection."""
    from leadengine.enrich.website.tech import FINGERPRINT_DIR, download_webappanalyzer

    count = asyncio.run(download_webappanalyzer())
    console.print(f"[green]{count} technology fingerprints saved to[/] {FINGERPRINT_DIR} "
                  "(GPL-3.0 data from github.com/enthec/webappanalyzer; kept local, not redistributed).")


@app.command(name="zip")
def zip_info(zip_code: str = typer.Argument(..., help="US ZIP code")) -> None:
    """Offline facts about a ZIP: city, population, income, area, towns, suggested grid."""
    try:
        zip_code = normalize_zip(zip_code)
    except ValueError as exc:
        _fail(str(exc))
    info = zip_directory().get(zip_code)
    if info is None:
        _fail(f"{zip_code} is not in the bundled ZIP data")
    settings = Settings.load()
    cfg = settings.section("discovery")
    area_km = info.radius_km * (1 + float(cfg.get("area_margin", 0.15)))
    cells = plan_cells(info.lat, info.lng, area_km, float(cfg.get("initial_cell_km", 6)))
    def money(v): return f"${v:,}" if v else "-"
    console.print(f"[bold]{zip_code}[/] {info.label}  ({info.county} County)")
    console.print(f"Centre {info.lat:.5f}, {info.lng:.5f}  |  land {info.land_sqmi or '?'} sq mi  |  "
                  f"search radius {area_km:.1f} km  |  starting grid {len(cells)} cell(s) at zoom {cells[0].zoom}")
    extra = ""
    if info.median_household_income:
        extra = f"  |  median income {money(info.median_household_income)}  |  median home {money(info.median_home_value)}"
    console.print(f"Population {info.population or 0:,}{extra}")
    console.print("Towns: " + ", ".join(zip_directory().towns(zip_code)))


@app.command()
def proxies(check: bool = typer.Option(False, "--check", help="Test each proxy now")) -> None:
    """Show configured proxies (from PROXIES / PROXY_FILE in .env) and optionally health-check them."""
    settings = Settings.load()
    pool = ProxyPool.from_env(settings.proxy_list, settings.proxy_file)
    if not pool.enabled:
        console.print("No proxies configured - running direct (fine for small runs). "
                      "Add PROXIES=... to .env for large runs.")
        return
    results = asyncio.run(pool.health_check()) if check else {}
    table = Table(title=f"Proxies ({len(pool)})")
    table.add_column("proxy")
    table.add_column("status")
    for p in pool.proxies():
        status = "-" if not check else ("[green]ok[/]" if results.get(p.label) else f"[red]dead[/] {p.last_error or ''}")
        table.add_row(p.label, status)
    console.print(table)


LABEL_STYLE = {"Hot": "bold red", "Warm": "yellow", "Cold": "cyan", "Skip": "dim"}


def _leads_table(rows, title: str) -> Table:
    table = Table(title=title)
    for col in ("id", "Label", "Opp.", "Name", "Rating", "Reviews", "Ads", "Site", "Email", "Why"):
        table.add_column(col, no_wrap=col != "Why", overflow="fold" if col == "Why" else "ellipsis")
    for b in rows:
        label = b.lead_label or "-"
        table.add_row(
            str(b.id), f"[{LABEL_STYLE.get(label, '')}]{label}[/]" if label != "-" else "-",
            str(b.opportunity_score) if b.opportunity_score is not None else "-", b.name[:32],
            f"{b.rating:.1f}" if b.rating is not None else "-",
            str(b.review_count if b.review_count is not None else "-"), _ads_cell(b), _site_cell(b),
            _email_cell(b), (b.lead_reason or "")[:120],
        )
    return table


@app.command()
def leads(
    keyword: Optional[str] = typer.Option(None, "--keyword", "-k"),
    zip_code: Optional[str] = typer.Option(None, "--zip", "-z"),
    min_rating: Optional[float] = typer.Option(None, "--min-rating"),
    min_reviews: Optional[int] = typer.Option(None, "--min-reviews"),
    label: list[str] = typer.Option(None, "--label", "-L", help="Hot / Warm / Cold / Skip (repeatable)"),
    ads: list[str] = typer.Option(None, "--ads", help="Active / Likely / Past / None (repeatable)"),
    max_site_score: Optional[int] = typer.Option(None, "--max-site-score"),
    email: Optional[str] = typer.Option(None, "--email", help="'verified' or 'any'"),
    min_opportunity: Optional[int] = typer.Option(None, "--min-opp"),
    sort: str = typer.Option("opportunity", "--sort", help="opportunity | reviews"),
    limit: int = typer.Option(50, "--limit"),
) -> None:
    """Saved leads with filters, best opportunities first (no API calls)."""
    settings, sf = _bootstrap()
    with sf() as s:
        rows = Repository(s).list_businesses(
            keyword=keyword, zip_code=zip_code, min_rating=min_rating, min_reviews=min_reviews,
            labels=[l.capitalize() for l in label] if label else None,
            ads_statuses=[a.capitalize() for a in ads] if ads else None, max_site_score=max_site_score,
            email=email, min_opportunity=min_opportunity, order=sort, limit=limit)
    console.print(_leads_table(rows, f"Leads ({len(rows)})"))


@app.command()
def score(
    zip_code: Optional[str] = typer.Option(None, "--zip", "-z"),
    keyword: Optional[str] = typer.Option(None, "--keyword", "-k"),
    show: int = typer.Option(25, "--show"),
) -> None:
    """Recompute the Opportunity Score (Hot / Warm / Cold / Skip) for saved businesses."""
    settings, sf = _bootstrap()
    with sf() as s:
        ids = None
        if zip_code or keyword:
            ids = [b.id for b in Repository(s).list_businesses(keyword=keyword, zip_code=zip_code)]

    async def _run():
        async with HttpClient(settings.http) as http:
            return LeadService(settings, sf, http).rescore(ids)

    results = asyncio.run(_run())
    counts = {k: sum(1 for r in results if r["label"] == k) for k in ("Hot", "Warm", "Cold", "Skip")}
    with sf() as s:
        rows = Repository(s).list_businesses(keyword=keyword, zip_code=zip_code, order="opportunity", limit=show)
    console.print(_leads_table(rows, f"Top opportunities ({len(results)} scored)"))
    console.print("  ".join(f"[{LABEL_STYLE[k]}]{k}: {v}[/]" for k, v in counts.items()))


@app.command()
def zips(
    state: Optional[str] = typer.Option(None, "--state", "-s", help="Two-letter state, e.g. TX"),
    near: Optional[str] = typer.Option(None, "--near", help="Centre ZIP"),
    radius_km: float = typer.Option(40.0, "--radius-km"),
    keyword: Optional[str] = typer.Option(None, "--keyword", "-k", help="Mark ZIPs already scanned for this keyword"),
    top: int = typer.Option(20, "--top"),
    min_population: int = typer.Option(2000, "--min-pop"),
) -> None:
    """Which ZIPs to scan first (by population, nearest, not yet scanned)."""
    from leadengine.scoring.zips import prioritise_zips

    if not state and not near:
        _fail("give --state or --near")
    settings, sf = _bootstrap()
    with sf() as s:
        scanned = Repository(s).scanned_zips(keyword)
    try:
        picks = prioritise_zips(zip_directory(), state=state, near_zip=near, radius_km=radius_km, scanned=scanned,
                                top=top, min_population=min_population)
    except ValueError as exc:
        _fail(str(exc))
    table = Table(title="ZIPs to scan first")
    for col in ("ZIP", "Place", "Population", "km", "Notes"):
        table.add_column(col)
    for p in picks:
        table.add_row(p.info.zip, p.info.label, f"{p.info.population or 0:,}",
                      "-" if p.distance_km is None else str(p.distance_km), p.why)
    console.print(table)


@app.command()
def credits(live: bool = typer.Option(False, "--live", help="Also ask SerpAPI for your remaining searches (free call)")) -> None:
    """API usage per provider: calls this month, free calls left, estimated cost."""
    settings, sf = _bootstrap()
    rows = CreditTracker(sf, settings).summary()
    table = Table(title="API usage (estimates at list price)")
    for col in ("provider", "calls this month", "failed", "free left", "est. $ this month", "calls all-time", "est. $ all-time"):
        table.add_column(col, justify="right" if col != "provider" else "left")
    for r in rows:
        table.add_row(
            r.provider, str(r.calls_month), str(r.failed_month),
            "-" if r.free_remaining is None else str(r.free_remaining),
            f"{r.est_cost_month_usd:.2f}", str(r.calls_total), f"{r.est_cost_total_usd:.2f}",
        )
    console.print(table if rows else "[dim]No API calls recorded yet.[/]")

    if live:
        if not settings.serpapi_api_key:
            _fail("SERPAPI_API_KEY missing in .env")

        async def _account():
            async with HttpClient(settings.http) as http:
                return await SerpApiProvider(settings, http).account()

        try:
            acct = asyncio.run(_account())
        except LeadEngineError as exc:
            _fail(str(exc))
        console.print(
            f"SerpAPI live: plan [bold]{acct.get('plan_name', '?')}[/], "
            f"used this month {acct.get('this_month_usage', '?')}, "
            f"searches left [bold]{acct.get('total_searches_left', acct.get('plan_searches_left', '?'))}[/]"
        )


@app.command()
def stats() -> None:
    """Database counts."""
    settings, sf = _bootstrap()
    with sf() as s:
        data = Repository(s).stats()
    for k, v in data.items():
        console.print(f"{k:>14}: {v}")


@app.command()
def export(
    path: Path = typer.Argument(..., help="leads.csv or leads.xlsx"),
    keyword: Optional[str] = typer.Option(None, "--keyword", "-k"),
    zip_code: Optional[str] = typer.Option(None, "--zip", "-z"),
    label: list[str] = typer.Option(None, "--label", "-L"),
) -> None:
    """Export saved leads to CSV or a formatted Excel file."""
    from leadengine.exporting import lead_rows, to_csv, to_xlsx

    settings, sf = _bootstrap()
    with sf() as s:
        rows = lead_rows(s, Repository(s).list_businesses(
            keyword=keyword, zip_code=zip_code, labels=[l.capitalize() for l in label] if label else None,
            order="opportunity"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".xlsx":
        path.write_bytes(to_xlsx(rows))
    else:
        path.write_text(to_csv(rows), encoding="utf-8")
    console.print(f"[green]{len(rows)} leads ->[/] {path}")


@app.command()
def status(
    business_id: int = typer.Argument(...),
    new_status: str = typer.Argument(..., help="New | Preview Built | Emailed | Replied | Won | Lost"),
    note: Optional[str] = typer.Option(None, "--note"),
    force: bool = typer.Option(False, "--force", help="Allow even if this business was already contacted"),
) -> None:
    """Move a lead through the pipeline (refuses to mark the same business Emailed twice)."""
    from leadengine import crm

    settings, sf = _bootstrap()
    match = next((st for st in crm.STATUSES if st.lower() == new_status.lower()), new_status)
    with sf() as s:
        try:
            crm.set_status(s, business_id, match, note, force=force)
            s.commit()
        except LeadEngineError as exc:
            _fail(str(exc))
    console.print(f"[green]#{business_id} -> {match}[/]")

if __name__ == "__main__":  # pragma: no cover
    app()
