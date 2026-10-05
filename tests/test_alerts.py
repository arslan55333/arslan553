import asyncio
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient

from leadengine.db import utcnow
from leadengine.db.models import AdSweep, Alert, Job, Watch
from leadengine.service import LeadService


def test_watches_alert_only_on_new_advertisers(settings, session_factory, make_http, monkeypatch):
    from dataclasses import replace

    from leadengine.db import Repository
    from leadengine.models import BusinessRecord

    rounds = [["A", "B"], ["A", "B", "C"]]
    posted = []

    async def fake_sweep(self, keyword, locations, *, watch_id=None, **kw):
        names = rounds.pop(0)
        with self._sf() as s:
            repo = Repository(s)
            prev = s.query(AdSweep).filter(AdSweep.watch_id == watch_id).order_by(AdSweep.id.desc()).first()
            before = {a["business_id"] for a in (prev.advertisers or [])} if prev else set()
            rows = []
            for n in names:
                b = repo.upsert_business(BusinessRecord(name=f"{n} Dumpsters", provider="t", place_id=n))
                s.flush()
                rows.append({"business_id": b.id, "name": b.name, "kinds": ["search"], "hits": 2,
                             "new": bool(prev) and b.id not in before})
            sw = AdSweep(keyword=keyword, locations=locations, advertisers=rows, watch_id=watch_id)
            s.add(sw)
            s.commit()
            return {"sweep_id": sw.id}

    monkeypatch.setattr(LeadService, "ads_sweep", fake_sweep)
    st = replace(settings, alert_webhook_url="https://hooks.example.com/alerts")
    with session_factory() as s:
        s.add(Watch(keyword="dumpster rental", locations=["New York, NY"], every_days=7))
        s.add(Watch(keyword="paused", locations=["10001"], active=False))
        s.commit()

    def hook(request):
        posted.append(request.content)
        return httpx.Response(200)

    async def go():
        async with make_http(hook) as http:
            svc = LeadService(st, session_factory, http)
            first = await svc.run_watches()                     # baseline: no alerts
            assert svc.due_watches() == []                      # not due again for a week
            with session_factory() as s:
                w = s.get(Watch, 1)
                w.last_run_at = utcnow() - timedelta(days=8)
                s.commit()
            assert svc.due_watches() == [1]
            second = await svc.run_watches()
            return first, second
    first, second = asyncio.run(go())
    assert first == {"watches": 1, "alerts": 0, "link": "/alerts"} and second["alerts"] == 1
    with session_factory() as s:
        alerts = s.query(Alert).all()
        assert len(alerts) == 1 and "C Dumpsters started advertising for 'dumpster rental'" in alerts[0].message
    assert len(posted) == 1 and b"C Dumpsters" in posted[0]


def test_alerts_page_and_watch_actions(settings):
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        sf = app.state.sf
        r = c.post("/alerts/watch", data={"keyword": "septic", "places": "Dallas, TX\n75201 75204", "every_days": 7,
                                          "variations": 3, "run_now": "true"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
        with sf() as s:
            w = s.query(Watch).one()
            assert w.locations == ["Dallas, TX", "75201", "75204"] and w.variations == 3
            assert s.get(Job, int(r.headers["location"].rsplit("/", 1)[1])).kind == "monitor"
            s.add(Alert(business_id=None, message="Acme started advertising for 'septic'"))
            s.commit()
        page = c.get("/alerts").text
        assert "Acme started advertising" in page and "septic" in page
        assert 'class="nbadge">1<' in c.get("/").text and "1 new alert" in c.get("/").text
        c.post("/alerts/seen")
        assert "nbadge" not in c.get("/leads").text
        c.post(f"/alerts/watch/{w.id}/toggle")
        with sf() as s:
            assert s.get(Watch, w.id).active is False
        c.post(f"/alerts/watch/{w.id}/delete")
        with sf() as s:
            assert s.query(Watch).count() == 0
