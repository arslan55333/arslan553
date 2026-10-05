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
from leadengine.db import Business, Email, Job, Repository, init_db, make_engine, make_session_factory, utcnow
from leadengine.exporting import lead_rows, to_csv, to_google_sheet, to_xlsx
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

    def filtered(session, q: dict[str, Any], limit: int | None = 200) -> list[Business]:
        rows = Repository(session).list_businesses(
            keyword=q.get("keyword") or None, zip_code=q.get("zip") or None,
            min_rating=float(q["min_rating"]) if q.get("min_rating") else None,
            min_reviews=int(q["min_reviews"]) if q.get("min_reviews") else None,
            labels=[q["label"]] if q.get("label") else None, ads_statuses=[q["ads"]] if q.get("ads") else None,
            max_site_score=int(q["max_site"]) if q.get("max_site") else None, email=q.get("email") or None,
            order=q.get("sort") or "opportunity", limit=limit)
        text = (q.get("q") or "").lower().strip()
        if text:
            rows = [b for b in rows if text in " ".join(filter(None, [b.name, b.best_email, b.website, b.phone,
                                                                       b.city])).lower()]
        return rows

    # ── pages ────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        with sf() as s:
            total = s.scalar(select(func.count()).select_from(Business)) or 0
            by_label = dict(s.execute(select(Business.lead_label, func.count()).group_by(Business.lead_label)).all())
            with_email = s.scalar(select(func.count()).select_from(Business).where(Business.best_email.is_not(None))) or 0
            ads_active = s.scalar(select(func.count()).select_from(Business).where(Business.ads_status == "Active")) or 0
            hot = Repository(s).list_businesses(labels=["Hot"], order="opportunity", limit=10)
            recent = list(s.scalars(select(Job).order_by(Job.created_at.desc()).limit(6)))
            pipeline = {st: 0 for st in crm.STATUSES}
            from leadengine.db.models import LeadStatus
            for st, n in s.execute(select(LeadStatus.status, func.count()).group_by(LeadStatus.status)).all():
                pipeline[st] = n
            pipeline["New"] = max(0, total - sum(v for k, v in pipeline.items() if k != "New"))
        credits = CreditTracker(sf, settings).summary()
        return render(request, "dashboard.html", total=total, by_label=by_label, with_email=with_email,
                      ads_active=ads_active, hot=hot, jobs=recent, credits=credits, pipeline=pipeline)

    @app.get("/run", response_class=HTMLResponse)
    def run_form(request: Request):
        return render(request, "run.html", disc=settings.section("discovery"))

    @app.post("/run")
    def run_submit(keyword: str = Form(...), zips: str = Form(...), provider: str = Form("playwright"),
                   towns: bool = Form(False), emails: bool = Form(False), website: bool = Form(False),
                   ads: bool = Form(False), activity: bool = Form(False), fill: bool = Form(False),
                   refresh: bool = Form(False)):
        zip_list = _split_zips(zips)
        if not keyword.strip() or not zip_list:
            raise HTTPException(400, "Enter a keyword and at least one valid 5-digit ZIP")
        job_id = jobs.enqueue(sf, "discover", {
            "keyword": keyword.strip(), "zips": zip_list,
            "options": {"provider_name": provider, "towns": towns, "emails": emails, "website": website,
                        "ads": ads, "activity": activity, "fill": fill, "refresh": refresh}})
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

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
            statuses = {b.id: crm.current_status(s, b.id) for b in rows}
        return render(request, "leads.html", rows=rows, q=q, statuses=statuses,
                      query=str(request.url.query))

    @app.get("/leads/{business_id}", response_class=HTMLResponse)
    def lead_page(request: Request, business_id: int, error: str | None = None):
        with sf() as s:
            biz = s.get(Business, business_id)
            if biz is None:
                raise HTTPException(404)
            repo = Repository(s)
            enr = {k: (e.payload if (e := repo.latest_enrichment(business_id, k, fresh_only=False)) else None)
                   for k in ("website", "ads", "emails", "maps_activity")}
            emails = list(s.scalars(select(Email).where(Email.business_id == business_id)
                                    .order_by(Email.is_guess, Email.confidence.desc().nulls_last())))
            status = crm.current_status(s, business_id)
            events = crm.timeline(s, business_id)
            previews = repo.latest_enrichment(business_id, "preview", fresh_only=False)
            drafts = repo.latest_enrichment(business_id, "outreach", fresh_only=False)
        return render(request, "lead.html", b=biz, enr=enr, emails=emails, status=status, events=events,
                      error=error, preview=previews.payload if previews else None,
                      drafts=drafts.payload if drafts else None)

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

