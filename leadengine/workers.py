"""Job handlers (what a queued job actually does)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from leadengine.config import Settings
from leadengine.credits import CreditTracker
from leadengine.http import HttpClient
from leadengine.jobs import Handler, JobContext
from leadengine.normalize import normalize_zip

DISCOVER_OPTIONS = ("provider_name", "towns", "activity", "fill", "emails", "website", "ads", "refresh", "max_cells")


def make_handlers(settings: Settings, sf: sessionmaker[Session]) -> dict[str, Handler]:
    from leadengine.service import LeadService

    async def discover(ctx: JobContext) -> list[dict[str, Any]]:
        keyword = ctx.params["keyword"]
        zips = [normalize_zip(z) for z in ctx.params["zips"]]
        opts = {k: v for k, v in (ctx.params.get("options") or {}).items() if k in DISCOVER_OPTIONS and v is not None}
        summary: list[dict[str, Any]] = []
        async with HttpClient(settings.http, user_agent=settings.user_agent) as http:
            service = LeadService(settings, sf, http, CreditTracker(sf, settings))
            try:
                for z in zips:
                    if ctx.cancelled():
                        ctx.log("cancelled by user")
                        break
                    if ctx.is_done(z):
                        ctx.log(f"ZIP {z}: already finished earlier, skipping")
                        continue
                    ctx.log(f"ZIP {z}: scanning '{keyword}'")
                    out = await service.discover(keyword, z, on_progress=ctx.log, **opts)
                    labels = [b.lead_label for b, _ in out.results]
                    row = {"zip": z, "businesses": len(out.results), "hot": labels.count("Hot"),
                           "warm": labels.count("Warm"), "sponsored": out.sponsored, "from_cache": out.from_cache}
                    summary.append(row)
                    ctx.log(f"ZIP {z}: done - {row['businesses']} businesses, {row['hot']} Hot, {row['warm']} Warm")
                    ctx.step_done(z)
            finally:
                await service.aclose()
        return summary

    async def preview(ctx: JobContext) -> list[dict[str, Any]]:
        async with HttpClient(settings.http, user_agent=settings.user_agent) as http:
            service = LeadService(settings, sf, http, CreditTracker(sf, settings))
            rows = await service.build_previews(ctx.params["ids"], style=ctx.params.get("style"),
                                                deploy=ctx.params.get("deploy"), on_progress=ctx.log)
        for r in rows:
            ctx.log(f"{r['name']}: {r.get('url') or r.get('path') or r.get('error')}")
        return [{k: r.get(k) for k in ("id", "name", "url", "path", "error")} for r in rows]

    async def outreach(ctx: JobContext) -> list[dict[str, Any]]:
        service = LeadService(settings, sf, None, CreditTracker(sf, settings))
        rows = await service.draft_outreach(ctx.params["ids"], use_ai=ctx.params.get("ai"),
                                            force=bool(ctx.params.get("force")), on_progress=ctx.log)
        for r in rows:
            ctx.log(f"{r['name']}: " + (f"skipped - {r['skipped']}" if r.get("skipped") else
                                        f"{len(r['subjects'])} drafts ({r['source']})"))
        return rows

    async def send(ctx: JobContext) -> dict[str, Any]:
        from leadengine.outreach.mail import Sender

        report = await Sender(settings, sf).run(limit=ctx.params.get("limit"), on_progress=ctx.log)
        if report.stopped:
            ctx.log(f"stopped: {report.stopped}")
        return {"sent": report.sent, "skipped": report.skipped, "failed": report.failed, "stopped": report.stopped}

    return {"discover": discover, "preview": preview, "outreach": outreach, "send": send}
