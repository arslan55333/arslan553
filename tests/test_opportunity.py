import asyncio
from datetime import date, datetime

import httpx

from leadengine.db import Business, LeadStatus, Repository
from leadengine.geo.zipdata import zip_directory
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.scoring.opportunity import score_business
from leadengine.scoring.zips import prioritise_zips
from leadengine.service import LeadService

TODAY = date(2026, 10, 5)


def biz(**kw) -> Business:
    base = dict(name="Acme Septic", rating=4.8, review_count=210, last_review_at=datetime(2026, 9, 30),
                recent_review_dates=["2026-09-30", "2026-09-20", "2026-09-01", "2026-08-15", "2026-07-20"],
                owner_response_rate=0.6, ads_status="Active", lsa=False, website="https://acme.com",
                website_score=22, website_flags=[], best_email="info@acme.com", email_confidence=90,
                email_status="valid", phone="(214) 555-0100", business_status="OPERATIONAL")
    base.update(kw)
    return Business(**base)


def test_ideal_target_is_hot_with_readable_reason():
    o = score_business(biz(), today=TODAY, website_reasons=["copyright 2014 (~12 years without updates)",
                                                            "not mobile friendly (no mobile viewport tag)"])
    assert o.label == "Hot" and o.score >= 85
    assert o.reason == ("4.8★, 210 reviews, 5 reviews in 90 days, running Google Ads, "
                        "website score 22 — copyright 2014, not mobile friendly")
    assert set(o.parts) == {"reputation", "activity", "ads", "website", "reachability"}


def test_label_ladder():
    warm = score_business(biz(ads_status="None", website_score=45), today=TODAY)
    cold = score_business(biz(rating=4.0, review_count=12, ads_status="None", website_score=70, last_review_at=None,
                              recent_review_dates=None, best_email=None, email_status=None), today=TODAY)
    assert warm.label == "Warm" and cold.label == "Cold"
    assert "no email found" in cold.reason
    no_site = score_business(biz(website=None, website_score=None, website_flags=["no_website"]), today=TODAY)
    fb = score_business(biz(website="https://facebook.com/acme", website_score=None, website_flags=["facebook_only"]),
                        today=TODAY)
    assert no_site.label == "Hot" and "no website" in no_site.reason and "only a Facebook page" in fb.reason
    lsa = score_business(biz(ads_status="Active", lsa=True), today=TODAY)
    assert "Local Services Ads" in lsa.reason


def test_skip_rules():
    assert score_business(biz(website_score=88), today=TODAY).skip_reason == "website already modern (88)"
    assert score_business(biz(rating=3.1), today=TODAY).label == "Skip"
    assert score_business(biz(business_status="CLOSED_PERMANENTLY"), today=TODAY).skip_reason == "business closed"
    assert score_business(biz(), today=TODAY, lead_status="Emailed").skip_reason == "already contacted (Emailed)"
    assert score_business(biz(phone=None, best_email=None), today=TODAY).skip_reason == "no way to contact"
    # a broken site is an opportunity even if an old score was high
    assert score_business(biz(website_score=90, website_flags=["broken"]), today=TODAY).label == "Hot"


def test_unknown_ads_caps_score_and_weights_are_configurable():
    unknown = score_business(biz(ads_status=None), today=TODAY)
    assert "ads" not in unknown.parts and unknown.score <= 85
    no_ads_weight = score_business(biz(ads_status="None"), today=TODAY, weights={"ads": 0})
    assert no_ads_weight.score > score_business(biz(ads_status="None"), today=TODAY).score


def test_rescore_and_filters(settings, session_factory, make_http):
    with session_factory() as db:
        repo = Repository(db)
        hot = repo.upsert_business(BusinessRecord(name="Hot Co", provider="t", place_id="H", rating=4.9, review_count=300,
                                                  phone="2145550001", website="https://hot.com", zip_code="75201"))
        hot.ads_status, hot.website_score, hot.best_email, hot.email_status = "Active", 15, "a@hot.com", "valid"
        cold = repo.upsert_business(BusinessRecord(name="Cold Co", provider="t", place_id="C", rating=4.1,
                                                   review_count=8, phone="2145550002", zip_code="75201"))
        cold.ads_status, cold.website_score = "None", 75
        done = repo.upsert_business(BusinessRecord(name="Done Co", provider="t", place_id="D", rating=4.9,
                                                   review_count=300, phone="2145550003", zip_code="75201"))
        done.ads_status, done.website_score = "Active", 10
        db.add(LeadStatus(business_id=done.id, status="Won"))
        repo.record_search(SearchQuery("septic", "75201"), "t", [(hot, 1), (cold, 2), (done, 3)], exhausted=True,
                           api_calls=1)
        db.commit()

    async def go():
        async with make_http(lambda r: httpx.Response(404)) as http:
            return LeadService(settings, session_factory, http).rescore()

    labels = {r["name"]: r["label"] for r in asyncio.run(go())}
    assert labels == {"Hot Co": "Hot", "Cold Co": "Cold", "Done Co": "Skip"}
    with session_factory() as db:
        repo = Repository(db)
        assert [b.name for b in repo.list_businesses(order="opportunity")][0] == "Hot Co"
        assert [b.name for b in repo.list_businesses(labels=["Hot"])] == ["Hot Co"]
        assert [b.name for b in repo.list_businesses(ads_statuses=["Active"], max_site_score=20, email="verified")] == ["Hot Co"]
        assert repo.scanned_zips("Septic") == {"75201"} and repo.scanned_zips("roofer") == set()


def test_zip_prioritisation():
    d = zip_directory()
    picks = prioritise_zips(d, near_zip="75201", radius_km=15, scanned={"75217"}, top=50)
    assert picks and all(p.distance_km <= 15 for p in picks)
    pops = [p.info.population for p in picks if not p.scanned]
    assert pops[0] == max(pops)
    assert next(p for p in picks if p.info.zip == "75217").scanned
    tx = prioritise_zips(d, state="tx", top=5)
    assert all(p.info.state == "TX" for p in tx) and len(tx) == 5
