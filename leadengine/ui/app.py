"""Local web dashboard + mini CRM (FastAPI + Jinja2 + htmx). Start with ``python -m leadengine ui``."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from leadengine import crm, jobs
from leadengine.config import Settings
from leadengine.credits import CreditTracker
from leadengine.db import Business, Email, Job, OutboundEmail, Repository, Suppression, init_db, make_engine, make_session_factory, utcnow
from leadengine.exporting import lead_rows, to_csv, to_google_sheet, to_xlsx
from leadengine.http import HttpClient
from leadengine.normalize import normalize_zip

HERE = Path(__file__).resolve().parent
LABELS = ["Hot", "Warm", "Cold", "Skip"]
ADS = ["Active", "Likely", "Past", "None"]


def _split_zips(raw: str) -> list[str]:
    out = []
    for part in raw.replace(",", " ").split():
        try:
            out.append(normalize_zip(part))
        except ValueError:
            continue
    return list(dict.fromkeys(out))


def create_app(settings: Settings | None = None, *, start_runner: bool = True, handlers=None) -> FastAPI:
    settings = settings or Settings.load()
    engine = make_engine(settings.database_url)
    init_db(engine)
    sf = make_session_factory(engine)
    data_dir = (settings.root / "data").resolve()
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update(LABELS=LABELS, ADS=ADS, STATUSES=crm.STATUSES)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if start_runner:
            from leadengine.workers import make_handlers

            runner = jobs.JobRunner(sf, handlers or make_handlers(settings, sf))
            app.state.runner = runner
            task = asyncio.create_task(runner.run_forever())
        yield
        if task:
            app.state.runner.stop()
            await asyncio.wait([task], timeout=5)

    app = FastAPI(title="LeadEngine", lifespan=lifespan)
    app.state.sf = sf
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    def render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx)

    def media_url(path: str | None) -> str | None:
        if not path:
            return None
        try:
            rel = Path(path).resolve().relative_to(data_dir)
        except ValueError:
            return None
        return "/media/" + rel.as_posix()

    templates.env.globals["media_url"] = media_url

    def job_info(job: Job) -> dict[str, Any]:
        """Title, detail and progress for any job kind (scan, check, preview, drafts, send ...)."""
        p = job.params or {}
        n_ids = len(p.get("ids") or [])
        info = {
            "discover": (f"Scan: {p.get('keyword', '')}", ", ".join(p.get("zips") or []), len(p.get("zips") or []), "ZIPs"),
            "enrich": ("Deep check", f"{n_ids} lead(s): " + ", ".join(p.get("kinds") or ["emails", "website", "ads"]),
                       len(p.get("kinds") or [1, 2, 3]), "steps"),
            "preview": ("Preview sites", f"{n_ids} lead(s)", 0, ""),
            "outreach": ("Email drafts", f"{n_ids} lead(s)", 0, ""),
            "send": ("Send approved emails", "", 0, ""),
            "sweep": (f"Ads sweep: {p.get('keyword', '')}", ", ".join(p.get("locations") or p.get("zips") or []),
                      len(p.get("locations") or p.get("zips") or []), "places"),
            "rankgrid": (f"Rank heatmap: {p.get('keyword', '')}", f"{p.get('size', 7)}x{p.get('size', 7)} grid around "
                         f"{p.get('zip', '')}", 0, ""),
            "monitor": ("Weekly watch", f"{len(p.get('watch_ids') or [])} watch(es)", 0, ""),
            "audit": ("Audit reports", f"{n_ids} lead(s)", 0, ""),
        }.get(job.kind, (job.kind, "", 0, ""))
        return {"title": info[0], "detail": info[1], "total": info[2], "unit": info[3],
                "done": len(job.done_steps or [])}

    templates.env.globals["job_info"] = job_info

    def outreach_cfg():
        from leadengine.outreach.mail import outreach_cfg as load
        return load(settings)

    def render_final(body: str, preview_url: str | None = None) -> str:
        from leadengine.outreach.compose import final_body
        return final_body(body, outreach_cfg(), preview_url=preview_url)

    templates.env.globals["final_body"] = render_final

    def filtered(session, q: dict[str, Any], limit: int | None = 200) -> list[Business]:
        rows = Repository(session).list_businesses(
            keyword=q.get("keyword") or None, zip_code=q.get("zip") or None,
            min_rating=float(q["min_rating"]) if q.get("min_rating") else None,
            min_reviews=int(q["min_reviews"]) if q.get("min_reviews") else None,
            labels=[q["label"]] if q.get("label") else None, ads_statuses=[q["ads"]] if q.get("ads") else None,
            max_site_score=int(q["max_site"]) if q.get("max_site") else None, email=q.get("email") or None,
            text=q.get("q") or None, status=q.get("status") or None,
            order=q.get("sort") or "opportunity", limit=limit)
        return rows

    # ── pages ────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        with sf() as s:
            total = s.scalar(select(func.count()).select_from(Business)) or 0
            by_label = dict(s.execute(select(Business.lead_label, func.count()).group_by(Business.lead_label)).all())
            with_email = s.scalar(select(func.count()).select_from(Business).where(Business.best_email.is_not(None))) or 0
            ads_active = s.scalar(select(func.count()).select_from(Business).where(Business.ads_status == "Active")) or 0
            ads_likely = s.scalar(select(func.count()).select_from(Business).where(Business.ads_status == "Likely")) or 0
            hot = Repository(s).list_businesses(labels=["Hot"], order="opportunity", limit=10)
            recent = list(s.scalars(select(Job).order_by(Job.created_at.desc()).limit(6)))
            pipeline = {st: 0 for st in crm.STATUSES}
            from leadengine.db.models import LeadStatus
            for st, n in s.execute(select(LeadStatus.status, func.count()).group_by(LeadStatus.status)).all():
                pipeline[st] = n
            pipeline["New"] = max(0, total - sum(v for k, v in pipeline.items() if k != "New"))
        credits = CreditTracker(sf, settings).summary()
        return render(request, "dashboard.html", total=total, by_label=by_label, with_email=with_email,
                      ads_active=ads_active, ads_likely=ads_likely, hot=hot, jobs=recent, credits=credits, pipeline=pipeline)

    @app.get("/run", response_class=HTMLResponse)
    def run_form(request: Request):
        return render(request, "run.html", disc=settings.section("discovery"))

    @app.post("/run")
    def run_submit(keyword: str = Form(...), zips: str = Form(...), provider: str = Form("playwright"),
                   towns: bool = Form(False), emails: bool = Form(False), website: bool = Form(False),
                   ads: bool = Form(False), activity: bool = Form(False), fill: bool = Form(False),
                   refresh: bool = Form(False), area_only: bool = Form(False)):
        zip_list = _split_zips(zips)
        if not keyword.strip() or not zip_list:
            raise HTTPException(400, "Enter a keyword and at least one valid 5-digit ZIP")
        job_id = jobs.enqueue(sf, "discover", {
            "keyword": keyword.strip(), "zips": zip_list,
            "options": {"provider_name": provider, "towns": towns, "emails": emails, "website": website,
                        "ads": ads, "activity": activity, "fill": fill, "refresh": refresh,
                        "scope": "area" if area_only else "all"}})
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    # ── search-box suggestions (keyword + places) ───────────────────
    app.state.http_factory = lambda: HttpClient(settings.http, user_agent=settings.user_agent)

    def online_ok() -> bool:
        return bool(settings.section("ui").get("online_suggestions", True))

    @app.get("/api/suggest/keyword")
    async def suggest_keyword(q: str = ""):
        from leadengine.db.models import Search
        from leadengine.suggest import keyword_suggestions

        with sf() as s:
            past = list(dict.fromkeys(s.scalars(select(Search.keyword).order_by(Search.id.desc()).limit(200))))
        async with app.state.http_factory() as http:
            return await keyword_suggestions(q, http=http, past=past, online=online_ok())

    @app.get("/api/suggest/place")
    async def suggest_place(q: str = ""):
        from leadengine.suggest import offline_places, online_places

        items = offline_places(q)
        if online_ok() and len(items) < 8:
            async with app.state.http_factory() as http:
                items += await online_places(q, http)
        return items[:12]

    @app.get("/api/areas")
    async def areas(zip: str):
        from leadengine.suggest import areas_for_zip

        try:
            z = normalize_zip(zip)
        except ValueError:
            raise HTTPException(400, "5-digit ZIP please")
        async with app.state.http_factory() as http:
            with sf() as s:
                out = await areas_for_zip(z, http=http, repo=Repository(s), online=online_ok())
                s.commit()
        return out

    # ── Ads finder (ads-first discovery) ─────────────────────────────
    @app.get("/ads", response_class=HTMLResponse)
    def ads_page(request: Request):
        from leadengine.db.models import AdSweep

        with sf() as s:
            sweeps = list(s.scalars(select(AdSweep).order_by(AdSweep.id.desc()).limit(30)))
        return render(request, "ads.html", sweeps=sweeps, cfg=settings.section("ads"))

    @app.post("/ads")
    def ads_submit(keyword: str = Form(...), places: str = Form(...), variations: int = Form(6),
                   landing: bool = Form(False), deep: bool = Form(False), refresh: bool = Form(False)):
        locs = [x.strip() for x in places.replace(";", "\n").splitlines() if x.strip()]
        locs = [p for x in locs for p in ([x] if "," in x else x.split())]
        if not keyword.strip() or not locs:
            raise HTTPException(400, "Enter a keyword and at least one place")
        job_id = jobs.enqueue(sf, "sweep", {"keyword": keyword.strip(), "locations": locs[:25],
                                            "variations": max(1, min(15, variations)), "landing": landing,
                                            "deep": deep, "refresh": refresh})
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.get("/ads/{sweep_id}", response_class=HTMLResponse)
    def ads_detail(request: Request, sweep_id: int):
        from leadengine.db.models import AdSweep

        with sf() as s:
            sw = s.get(AdSweep, sweep_id)
            if sw is None:
                raise HTTPException(404)
            ids = [a["business_id"] for a in sw.advertisers or []]
            repo = Repository(s)
            biz = {b.id: b for b in (repo.get_business(i) for i in ids) if b}
            landing = {k: e.payload for k, e in repo.latest_enrichments(ids, "landing").items()}
        return render(request, "ads_detail.html", sw=sw, biz=biz, landing=landing)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request):
        with sf() as s:
            rows = list(s.scalars(select(Job).order_by(Job.created_at.desc()).limit(100)))
        return render(request, "jobs.html", jobs=rows)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: int):
        with sf() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise HTTPException(404)
        return render(request, "job.html", job=job)

    @app.get("/jobs/{job_id}/log", response_class=HTMLResponse)
    def job_log(request: Request, job_id: int):
        with sf() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise HTTPException(404)
        return render(request, "_job_log.html", job=job)

    @app.post("/jobs/{job_id}/cancel")
    def job_cancel(job_id: int):
        jobs.cancel(sf, job_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.get("/leads", response_class=HTMLResponse)
    def leads_page(request: Request):
        q = dict(request.query_params)
        with sf() as s:
            rows = filtered(s, q)
            repo = Repository(s)
            ids = [b.id for b in rows]
            known = repo.lead_statuses(ids)
            statuses = {b.id: known.get(b.id, "New") for b in rows}
            checked = {k: set(repo.latest_enrichments(ids, k)) for k in ("emails", "website", "ads")}
        disc = settings.section("discovery")
        return render(request, "leads.html", rows=rows, q=q, statuses=statuses, checked=checked,
                      min_reviews=int(disc.get("shortlist_min_reviews", 20)),
                      min_rating=float(disc.get("shortlist_min_rating", 4.0)), query=str(request.url.query))

    @app.post("/leads/check")
    async def leads_check(request: Request):
        form = await request.form()
        ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
        if not ids:
            return RedirectResponse("/leads?error=" + "select at least one lead", status_code=303)
        job_id = jobs.enqueue(sf, "enrich", {"ids": ids[:300]})
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/leads/{business_id}/check")
    def lead_check(business_id: int, refresh: bool = Form(False)):
        job_id = jobs.enqueue(sf, "enrich", {"ids": [business_id], "refresh": refresh})
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.get("/leads/{business_id}", response_class=HTMLResponse)
    def lead_page(request: Request, business_id: int, error: str | None = None):
        with sf() as s:
            biz = s.get(Business, business_id)
            if biz is None:
                raise HTTPException(404)
            repo = Repository(s)
            enr = {k: (e.payload if (e := repo.latest_enrichment(business_id, k, fresh_only=False)) else None)
                   for k in ("website", "ads", "emails", "maps_activity", "landing")}
            emails = list(s.scalars(select(Email).where(Email.business_id == business_id)
                                    .order_by(Email.is_guess, Email.confidence.desc().nulls_last())))
            status = crm.current_status(s, business_id)
            events = crm.timeline(s, business_id)
            previews = repo.latest_enrichment(business_id, "preview", fresh_only=False)
            drafts = repo.latest_enrichment(business_id, "outreach", fresh_only=False)
            outbox = list(s.scalars(select(OutboundEmail).where(OutboundEmail.business_id == business_id,
                                                                OutboundEmail.status != "cancelled")
                                    .order_by(OutboundEmail.id.desc()).limit(20)))
        return render(request, "lead.html", b=biz, enr=enr, emails=emails, status=status, events=events,
                      error=error, preview=previews.payload if previews else None,
                      drafts=drafts.payload if drafts else None, outbox=outbox)

    @app.post("/leads/{business_id}/status")
    def lead_status(business_id: int, status: str = Form(...), note: str = Form(""), force: bool = Form(False)):
        with sf() as s:
            try:
                crm.set_status(s, business_id, status, note or None, force=force)
                s.commit()
            except crm.AlreadyContacted as exc:
                s.rollback()
                from urllib.parse import quote
                return RedirectResponse(f"/leads/{business_id}?error={quote(str(exc))}", status_code=303)
        return RedirectResponse(f"/leads/{business_id}", status_code=303)

    @app.post("/leads/{business_id}/preview")
    def lead_preview(business_id: int, style: str = Form("auto"), deploy: bool = Form(False)):
        job_id = jobs.enqueue(sf, "preview", {"ids": [business_id], "style": None if style == "auto" else style,
                                              "deploy": deploy})
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/leads/{business_id}/draft")
    def lead_draft(business_id: int, ai: bool = Form(False)):
        job_id = jobs.enqueue(sf, "outreach", {"ids": [business_id], "ai": ai})
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/leads/{business_id}/approve")
    def lead_approve(business_id: int, angle: str = Form(...), followups: bool = Form(False)):
        from urllib.parse import quote

        from leadengine.errors import LeadEngineError
        from leadengine.outreach.mail import approve

        with sf() as s:
            try:
                approve(s, business_id, angle, followups=followups)
                s.commit()
            except LeadEngineError as exc:
                s.rollback()
                return RedirectResponse(f"/leads/{business_id}?error={quote(str(exc))}#drafts", status_code=303)
        return RedirectResponse("/outbox", status_code=303)

    @app.get("/outbox", response_class=HTMLResponse)
    def outbox_page(request: Request, error: str | None = None):
        from leadengine.outreach.mail import compliance_problems, due_rows, sent_last_24h

        cfg = outreach_cfg()
        with sf() as s:
            rows = list(s.execute(select(OutboundEmail, Business.name)
                                  .join(Business, Business.id == OutboundEmail.business_id)
                                  .order_by(OutboundEmail.id.desc()).limit(300)))
            due = len(due_rows(s))
            sent_today = sent_last_24h(s)
            suppressed = list(s.scalars(select(Suppression).order_by(Suppression.id.desc()).limit(100)))
        return render(request, "outbox.html", rows=rows, due=due, sent_today=sent_today,
                      max_day=(cfg.get("sending") or {}).get("max_per_day", 30), suppressed=suppressed,
                      problems=compliance_problems(cfg), error=error)

    @app.post("/outbox/send")
    def outbox_send(confirm: bool = Form(False)):
        from leadengine.outreach.mail import compliance_problems

        problems = compliance_problems(outreach_cfg())
        if problems or not confirm:
            from urllib.parse import quote
            msg = "; ".join(problems) or "tick the confirmation box first"
            return RedirectResponse(f"/outbox?error={quote(msg)}", status_code=303)
        job_id = jobs.enqueue(sf, "send", {})
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/outbox/{row_id}/cancel")
    def outbox_cancel(row_id: int):
        with sf() as s:
            row = s.get(OutboundEmail, row_id)
            if row is not None and row.status in ("approved", "scheduled"):
                row.status, row.error = "cancelled", "cancelled by you"
                s.commit()
        return RedirectResponse("/outbox", status_code=303)

    @app.post("/suppress")
    def suppress_add(value: str = Form(...), reason: str = Form("manual")):
        from leadengine.outreach.mail import suppress

        with sf() as s:
            suppress(s, value, reason or "manual")
            s.commit()
        return RedirectResponse("/outbox", status_code=303)

    @app.get("/outreach/drafts.csv")
    def drafts_csv(angle: str = "best"):
        from leadengine.outreach.export import load_draft_items, merge_rows, to_merge_csv

        with sf() as s:
            items, _ = load_draft_items(s)
            text = to_merge_csv(merge_rows(items, outreach_cfg(), angle))
        return Response(text, media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=drafts.csv"})

    @app.post("/leads/{business_id}/note")
    def lead_note(business_id: int, note: str = Form(...)):
        with sf() as s:
            crm.add_note(s, business_id, note)
            s.commit()
        return RedirectResponse(f"/leads/{business_id}", status_code=303)

    @app.post("/rescore")
    def rescore():
        from leadengine.service import LeadService

        LeadService(settings, sf, None).rescore()   # pure DB work, no network client needed
        return RedirectResponse("/leads", status_code=303)

    @app.get("/export.csv")
    def export_csv(request: Request):
        with sf() as s:
            rows = lead_rows(s, filtered(s, dict(request.query_params), limit=None))
        return Response(to_csv(rows), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=leads.csv"})

    @app.get("/export.xlsx")
    def export_xlsx(request: Request):
        with sf() as s:
            rows = lead_rows(s, filtered(s, dict(request.query_params), limit=None))
        return Response(to_xlsx(rows), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": "attachment; filename=leads.xlsx"})

    @app.post("/export/sheets", response_class=HTMLResponse)
    def export_sheets(request: Request, spreadsheet: str = Form(...)):
        creds = settings.section("export").get("google_credentials_file", "")
        if not creds:
            return HTMLResponse("<p class='error'>Set google_credentials_file in [export] (service-account JSON) "
                                "and share the sheet with that account.</p>", status_code=400)
        with sf() as s:
            rows = lead_rows(s, filtered(s, {}, limit=None))
        try:
            url = to_google_sheet(rows, credentials_file=creds, spreadsheet=spreadsheet)
        except Exception as exc:
            return HTMLResponse(f"<p class='error'>Google Sheets export failed: {exc}</p>", status_code=400)
        return HTMLResponse(f"<p>Exported {len(rows)} leads to <a href='{url}'>your sheet</a>.</p>")

    @app.get("/credits", response_class=HTMLResponse)
    def credits_page(request: Request):
        return render(request, "credits.html", rows=CreditTracker(sf, settings).summary())

    @app.get("/media/{path:path}")
    def media(path: str):
        target = (data_dir / path).resolve()
        if data_dir not in target.parents or not target.is_file():
            raise HTTPException(404)
        return FileResponse(target)

    @app.get("/health")
    def health():
        return {"ok": True, "time": utcnow().isoformat()}

    return app

