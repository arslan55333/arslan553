from leadengine.db import Business, Repository
from leadengine.insights import ad_waste, competitor_gap, insights, money_line, money_lost, niche_for
from leadengine.models import BusinessRecord, SearchQuery


def test_niche_lookup():
    assert niche_for("junk removal", None)["niche"] == "junk removal"
    assert niche_for(None, "Roofing contractor")["job_value"] == 9000
    assert niche_for("underwater basket weaving")["niche"] == "local service"


def test_ad_waste_no_site_and_leaky_landing():
    b = Business(name="A", ads_status="Active", website=None)
    w = ad_waste(b, site=None, landing=None, budget=2000)
    assert w["wasted_share"] == 0.35 and w["wasted_monthly"] == 700 and "no website" in w["leaks"][0]["problem"]
    b2 = Business(name="B", ads_status="Likely", website="https://b.com", domain="b.com", website_flags=[])
    w2 = ad_waste(b2, site={"conversion_tag": False, "google_ads_ids": [], "call_tracking": []},
                  landing={"facts": {"is_homepage": True, "tel_links": 0, "forms": 0, "viewport": True, "https": True},
                           "pagespeed": {"performance": 22}}, budget=1500)
    probs = " | ".join(l["problem"] for l in w2["leaks"])
    assert "conversion tracking" in probs and "homepage" in probs and "tap-to-call" in probs and "22/100" in probs
    assert 0.5 < w2["wasted_share"] <= 0.70
    assert ad_waste(Business(name="C", ads_status="None"), site=None, landing=None, budget=1) is None


def test_money_lost_estimate():
    b = Business(name="A", zip_code="11368")
    m = money_lost(b, keyword="junk removal", position=12, niche=niche_for("junk removal"))
    assert m["monthly_searches"] > 100 and m["calls_top"] > m["calls_now"] and m["lost_monthly"] > 0
    assert money_lost(Business(name="X", zip_code="00000"), keyword=None, position=1, niche=niche_for("x")) is None


def test_competitor_gap_and_lines(settings, session_factory):
    with session_factory() as s:
        repo = Repository(s)
        me = repo.upsert_business(BusinessRecord(name="Me", provider="t", place_id="M", zip_code="11368",
                                                 review_count=12, rating=4.9, categories=["Junk removal service"]))
        tops = [repo.upsert_business(BusinessRecord(name=f"Top{i}", provider="t", place_id=f"T{i}", review_count=200,
                                                    rating=4.8, categories=["Junk removal service", "Hauling", "Moving"]))
                for i in range(3)]
        repo.record_search(SearchQuery("junk removal", "11368"), "t", [(t, i + 1) for i, t in enumerate(tops)] + [(me, 9)],
                           exhausted=True, api_calls=1)
        me.ads_status = "Active"
        s.commit()
        g = competitor_gap(s, me, "junk removal", None)
        labels = {r["label"]: r for r in g["rows"]}
        assert labels["Google reviews"]["behind"] and not labels["Star rating"]["behind"]
        assert "get about 188 more reviews" in g["actions"] and "add 2 more categories" in g["actions"]
        ins = insights(s, me, settings)
        assert ins["money"]["position"] == 9 and ins["ad_waste"]["wasted_share"] == 0.35
        assert "Google Ads budget" in money_line(ins)
