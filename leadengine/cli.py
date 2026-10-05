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
from leadengine.normalize import normalize_zip
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
            return await LeadService(settings, sf, http, credits).search(query, provider, refresh=refresh)

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
def leads(
    keyword: Optional[str] = typer.Option(None, "--keyword", "-k"),
    zip_code: Optional[str] = typer.Option(None, "--zip", "-z"),
    min_rating: Optional[float] = typer.Option(None, "--min-rating"),
    min_reviews: Optional[int] = typer.Option(None, "--min-reviews"),
    limit: int = typer.Option(50, "--limit"),
) -> None:
    """Show businesses already saved in the database (no API calls)."""
    settings, sf = _bootstrap()
    with sf() as s:
        rows = Repository(s).list_businesses(
            keyword=keyword, zip_code=zip_code, min_rating=min_rating, min_reviews=min_reviews, limit=limit
        )
    table = Table(title=f"Saved leads ({len(rows)})")
    for col in ("id", "Name", "Rating", "Reviews", "Phone", "Website", "ZIP", "place_id"):
        table.add_column(col)
    for b in rows:
        table.add_row(
            str(b.id), b.name[:40], f"{b.rating:.1f}" if b.rating is not None else "-",
            str(b.review_count if b.review_count is not None else "-"), b.phone or "-",
            _short_url(b.website, 28), b.zip_code or "-", (b.place_id or "-")[:16],
        )
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
    path: Path = typer.Argument(..., help="Output CSV file"),
    keyword: Optional[str] = typer.Option(None, "--keyword", "-k"),
    zip_code: Optional[str] = typer.Option(None, "--zip", "-z"),
) -> None:
    """Export saved businesses to CSV (opens in Excel)."""
    settings, sf = _bootstrap()
    with sf() as s:
        rows = Repository(s).list_businesses(keyword=keyword, zip_code=zip_code)
    fields = ["id", "place_id", "name", "categories", "rating", "review_count", "phone", "website",
              "domain", "address", "city", "state", "zip_code", "lat", "lng", "google_maps_url",
              "first_seen", "last_seen"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for b in rows:
            row = {k: getattr(b, k) for k in fields}
            row["categories"] = ", ".join(b.categories or [])
            writer.writerow(row)
    console.print(f"[green]{len(rows)} businesses ->[/] {path}")


if __name__ == "__main__":  # pragma: no cover
    app()
