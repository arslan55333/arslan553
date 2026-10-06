"""Job handlers (what a queued job actually does)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from leadengine.config import Settings
from leadengine.credits import CreditTracker
from leadengine.http import HttpClient
from leadengine.jobs import Handler, JobContext
from leadengine.normalize import normalize_zip

DISCOVER_OPTIONS = ("provider_name", "towns", "activity", "fill", "emails", "website", "ads", "refresh", "max_cells",
                    "scope")


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
                    out = await service.discover(keyword, z, on_progress=ctx.log, on_found=ctx.add_live, **opts)
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

    async def enrich(ctx: JobContext) -> dict[str, Any]:
        """Deep-check chosen leads: emails, website score, Google Ads (each step resumable)."""
        ids = [int(i) for i in ctx.params["ids"]]
        kinds = ctx.params.get("kinds") or ["emails", "website", "ads", "seo"]
        out: dict[str, Any] = {"leads": len(ids)}
        async with HttpClient(settings.http, user_agent=settings.user_agent) as http:
            service = LeadService(settings, sf, http, CreditTracker(sf, settings))
            try:
                for kind in kinds:
                    if ctx.cancelled():
                        ctx.log("cancelled by user")
                        break
                    if ctx.is_done(kind):
                        continue
                    ctx.log(f"{kind}: checking {len(ids)} lead(s)...")
                    if kind == "emails":
                        summary = await service.find_emails(ids, refresh=bool(ctx.params.get("refresh")))
                        out["emails_found"] = getattr(summary, "with_email", None)
                    elif kind == "website":
                        rows = await service.score_websites(ids, refresh=bool(ctx.params.get("refresh")), on_progress=ctx.log)
                        out["websites_scored"] = len(rows)
                    elif kind == "seo":
                        rows = await service.seo_audits(ids, refresh=bool(ctx.params.get("refresh")), on_progress=ctx.log)
                        out["seo_scored"] = len(rows)
                    elif kind == "ads":
                        rows = await service.detect_ads(ids, refresh=bool(ctx.params.get("refresh")), on_progress=ctx.log)
                        out["ads_active"] = sum(1 for r in rows if r.get("status") in ("Active", "Likely"))
                        for r in rows:
                            ctx.log(f"  {r['name']}: ads {r['status']}" + (f" ({r['evidence'][0]})" if r.get("evidence") else ""))
                    ctx.step_done(kind)
            finally:
                await service.aclose()
        labels = [r["label"] for r in service.rescore(ids)]
        out.update(hot=labels.count("Hot"), warm=labels.count("Warm"))
        ctx.log(f"done: {out}")
        return out

    async def sweep(ctx: JobContext) -> dict[str, Any]:
        """Ads finder: everyone paying Google for these searches in these places."""
        p = ctx.params
        async with HttpClient(settings.http, user_agent=settings.user_agent) as http:
            service = LeadService(settings, sf, http, CreditTracker(sf, settings))
            try:
                return await service.ads_sweep(p["keyword"], p["locations"], variations=p.get("variations"),
                                               landing=p.get("landing"), deep=p.get("deep"),
                                               refresh=bool(p.get("refresh")), watch_id=p.get("watch_id"),
                                               on_progress=ctx.log, on_found=ctx.add_live)
            finally:
                await service.aclose()

    async def rankgrid(ctx: JobContext) -> dict[str, Any]:
        p = ctx.params
        async with HttpClient(settings.http, user_agent=settings.user_agent) as http:
            service = LeadService(settings, sf, http, CreditTracker(sf, settings))
            try:
                return await service.rank_grid(p["keyword"], zip_code=p.get("zip"), business_id=p.get("business_id"),
                                               size=p.get("size"), spacing_km=p.get("spacing_km"), on_progress=ctx.log)
            finally:
                await service.aclose()

    async def audit(ctx: JobContext) -> list[dict[str, Any]]:
        async with HttpClient(settings.http, user_agent=settings.user_agent) as http:
            service = LeadService(settings, sf, http, CreditTracker(sf, settings))
            rows = await service.build_reports(ctx.params["ids"], deploy=ctx.params.get("deploy"), on_progress=ctx.log)
        for r in rows:
            ctx.log(f"{r['name']}: {r.get('url') or r.get('path')}" + (f" ({r['error']})" if r.get("error") else ""))
        return [{k: r.get(k) for k in ("id", "name", "url", "path", "error")} for r in rows]

    async def monitor(ctx: JobContext) -> dict[str, Any]:
        async with HttpClient(settings.http, user_agent=settings.user_agent) as http:
            service = LeadService(settings, sf, http, CreditTracker(sf, settings))
            try:
                return await service.run_watches(ctx.params.get("watch_ids"), on_progress=ctx.log)
            finally:
                await service.aclose()

    return {"discover": discover, "preview": preview, "outreach": outreach, "send": send, "enrich": enrich,
            "sweep": sweep, "rankgrid": rankgrid, "audit": audit, "monitor": monitor}
