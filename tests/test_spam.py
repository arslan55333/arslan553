from datetime import date

from fastapi.testclient import TestClient

from leadengine.db import Business, Repository
from leadengine.insights import competitor_gap
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.scoring.spam import duplicate_groups, name_matches_domain, review_burst, spam_signals, stuffed_name
from leadengine.service import LeadService

TODAY = date(2026, 10, 20)


def test_signal_helpers():
    assert name_matches_domain("Royal Waste Services", "royalwaste.com") is True
    assert name_matches_domain("Stratum Lab Dumpster Rental", "trueperformance.site") is False
    assert stuffed_name("Dumpster Rental Queens Pros", {"dumpster", "rental"}, {"queens"})
    assert not stuffed_name("Royal Waste Services", {"dumpster", "rental"}, {"queens"})
    assert review_burst(["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-05", "2026-10-09"], 6)
    assert not review_burst(["2026-01-01", "2026-03-02", "2026-05-03", "2026-07-05", "2026-10-09"], 6)


def test_spam_score_fake_vs_real():
    fake = Business(name="Cascade Cool Dumpster Rental", domain="premiersummit.site", phone="9295550100",
                    categories=["Dumpster rental service"])
    s = spam_signals(fake, domain_info={"status": "site_down", "label": "Domain fine, site down",
                                        "registered": "2026-07-08"}, phone_count=2, today=TODAY)
    assert s["label"] == "likely_fake" and s["score"] >= 80 and any(".site" in r for r in s["reasons"])
    real = Business(name="Royal Waste Services", domain="royalwaste.com", phone="7185262623", review_count=810,
                    rating=4.4, categories=["Waste management service", "Dumpster rental service"], address="1 Main St")
    assert spam_signals(real, today=TODAY)["label"] == "ok"


def test_rescore_skips_fakes_and_gap_uses_real_competitors(settings, session_factory):
    with session_factory() as s:
        repo = Repository(s)
        me = repo.upsert_business(BusinessRecord(name="Me Junk", provider="t", place_id="M", phone="7185550101",
                                                 review_count=40, rating=4.9, zip_code="11368", city="Corona",
                                                 categories=["Junk removal service"]))
        fakes = [repo.upsert_business(BusinessRecord(name=f"{n} Dumpster Rental", provider="t", place_id=f"F{i}",
                                                     website=f"https://{d}.site", phone="9295550199", zip_code="11368"))
                 for i, (n, d) in enumerate([("Cascade Cool", "premiersummit"), ("Stratum Lab", "trueperformance"),
                                              ("Luminary Works", "vertexsolutions")])]
        real = repo.upsert_business(BusinessRecord(name="Real Haulers", provider="t", place_id="R", phone="7185550102",
                                                   review_count=300, rating=4.8, zip_code="11368",
                                                   categories=["Junk removal service", "Moving company"]))
        dup = repo.upsert_business(BusinessRecord(name="Me Junk Corona", provider="t", place_id="M2", phone="7185550101"))
        repo.record_search(SearchQuery("junk removal", "11368"), "t",
                           [(fakes[0], 1), (fakes[1], 2), (real, 3), (me, 4), (fakes[2], 5), (dup, 6)],
                           exhausted=True, api_calls=1)
        s.commit()
        ids = [me.id, real.id, dup.id] + [f.id for f in fakes]
    LeadService(settings, session_factory, None).rescore()
    with session_factory() as s:
        f0 = s.get(Business, ids[3])
        assert f0.spam_score >= 55 and f0.lead_label == "Skip" and "likely fake" in f0.lead_reason
        assert s.get(Business, ids[0]).duplicates == [ids[2]]
        g = competitor_gap(s, s.get(Business, ids[0]), "junk removal", None)
        assert len(g["fake_above"]) == 2 and [c["name"] for c in g["competitors"]] == ["Real Haulers"]
    from leadengine.outreach.facts import _fake_line
    with session_factory() as s:
        assert _fake_line(s, s.get(Business, ids[0])).startswith("2 of the listings that show above you")


def test_dup_groups_and_ui_badges(settings):
    a = Business(id=1, name="A", phone_norm="1", domain=None)
    b = Business(id=2, name="B", phone_norm="1", domain=None)
    c = Business(id=3, name="C", phone_norm="2", domain=None)
    assert duplicate_groups([a, b, c]) == {1: [2], 2: [1]}
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as cl:
        sf = app.state.sf
        with sf() as s:
            repo = Repository(s)
            repo.upsert_business(BusinessRecord(name="Cascade Cool Dumpster Rental", provider="t", place_id="F",
                                                website="https://premiersummit.site", phone="9295550100"))
            repo.upsert_business(BusinessRecord(name="Stratum Lab Dumpster Rental", provider="t", place_id="F2",
                                                website="https://trueperformance.site", phone="9295550100"))
            s.commit()
        LeadService(settings, sf, None).rescore()
        page = cl.get("/leads").text
        assert "likely fake" in page and "Cascade Cool" in page
        assert "Cascade Cool" not in cl.get("/leads?hide_fake=1").text
