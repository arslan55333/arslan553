from fastapi.testclient import TestClient

from leadengine.db import Repository
from leadengine.geo import explorer
from leadengine.models import BusinessRecord, SearchQuery


def test_hierarchy_and_wealth():
    nys = {c["name"]: c for c in explorer.counties("NY")}
    assert "Queens" in nys and "Nassau" in nys and nys["Queens"]["population"] > 2_000_000
    towns = {t["name"]: t for t in explorer.towns("NY", "Queens")}
    assert "11368" in towns["Corona"]["zips"] and towns["Astoria"]["income"]          # Census income bundled
    assert explorer.wealth_score(250_000, 2_000_000) > 95 > explorer.wealth_score(30_000, 90_000)
    assert explorer.wealth_label(90) == "$$$$" and explorer.wealth_label(None) == ""
    assert any(s["code"] == "TX" for s in explorer.states())


def test_coverage_counts_scans_and_targets(session_factory):
    with session_factory() as s:
        repo = Repository(s)
        b = repo.upsert_business(BusinessRecord(name="Corona Junk", provider="t", place_id="C1", zip_code="11368",
                                                phone="7185550100"))
        repo.record_search(SearchQuery("Junk Removal", "11368"), "t", [(b, 1)], exhausted=True, api_calls=1)
        b.ads_status = "Active"
        s.commit()
        cov = explorer.coverage(s, "junk removal")
        assert cov["11368"]["businesses"] == 1 and cov["11368"]["targets"] == 1
        assert explorer.coverage(s, "plumber") == {}
        towns = explorer.with_coverage(explorer.towns("NY", "Queens"), cov)
        corona = next(t for t in towns if t["name"] == "Corona")
        assert corona["coverage"] == 100 and corona["targets"] == 1 and corona["last_scan_days"] == 0


def test_areas_page_and_run_prefill(settings):
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        assert "Queens" in c.get("/areas?state=NY").text
        page = c.get("/areas?state=NY&county=Queens&sort=wealth").text
        assert "Corona" in page and "11368" in page and "Scan selected" in page
        run = c.get("/run?zips=11368,11372&keyword=junk%20removal").text
        assert "11368 11372" in run and 'value="junk removal"' in run
