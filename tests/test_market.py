from fastapi.testclient import TestClient

from leadengine.db import Business, Repository
from leadengine.geo.market import label, market_stats, markets, ranked, weakness
from leadengine.models import BusinessRecord, SearchQuery


def biz(i, reviews, rating=4.8, website="https://x.com", spam=0, ads=None):
    return Business(id=i, name=f"B{i}", review_count=reviews, rating=rating, website=website, spam_score=spam,
                    ads_status=ads, website_score=80 if website else None)


def test_weak_vs_strong_market():
    weak = market_stats([(biz(1, 0, spam=90), 1), (biz(2, 6, website=None), 2), (biz(3, 9), 3), (biz(4, 12), 4),
                         (biz(5, 3, spam=80), 5), (biz(6, 1, website=None), 6)])
    strong = market_stats([(biz(i, 300, 4.9, ads="Active"), i) for i in range(1, 11)])
    assert weak["fakes_top5"] == 2 and weak["top3_reviews"] == 9            # fakes skipped from the real top 3
    assert weakness(weak) >= 60 and label(weakness(weak)) == "easy"
    assert weakness(strong) < 35 and label(weakness(strong)) == "hard"
    assert market_stats([(biz(1, 1), 1)]) is None


def test_markets_from_db_and_pages(settings):
    from leadengine.ui.app import create_app

    app = create_app(settings, start_runner=False)
    with TestClient(app) as c:
        sf = app.state.sf
        with sf() as s:
            repo = Repository(s)
            rows = []
            for i, rv in enumerate([3, 5, 8, 2, 1]):
                b = repo.upsert_business(BusinessRecord(name=f"Corona Haul {i}", provider="t", place_id=f"C{i}",
                                                        review_count=rv, rating=4.5, zip_code="11368"))
                rows.append((b, i + 1))
            repo.record_search(SearchQuery("junk removal", "11368"), "t", rows, exhausted=True, api_calls=0)
            rows2 = []
            for i in range(5):
                b = repo.upsert_business(BusinessRecord(name=f"LIC Pro {i}", provider="t", place_id=f"L{i}",
                                                        review_count=400, rating=4.9, zip_code="11101",
                                                        website="https://licpro.com"))
                rows2.append((b, i + 1))
            repo.record_search(SearchQuery("junk removal", "11101"), "t", rows2, exhausted=True, api_calls=0)
            s.commit()
            m = markets(s, "Junk Removal")
            assert m["11368"]["label"] == "easy" and m["11101"]["label"] == "hard"
            assert [x["zip"] for x in ranked(list(m.values()))][0] == "11368" and m["11368"]["demand"] > 0
        page = c.get("/markets?keyword=junk+removal").text
        assert "Corona" in page and "easy" in page and "Long Island City" in page
        areas = c.get("/areas?state=NY&county=Queens&keyword=junk+removal&sort=easy").text
        assert "Measure markets" in areas and "easy" in areas
        r = c.post("/markets/scan", data={"keyword": "junk removal", "zips": "11368 11372"}, follow_redirects=False)
        assert r.status_code == 303 and "/jobs/" in r.headers["location"]
