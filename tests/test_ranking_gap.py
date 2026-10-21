import asyncio
from dataclasses import replace

import httpx

from leadengine.db import Business, Repository
from leadengine.geo.ranking import formula, levers, observations, spearman
from leadengine.models import BusinessRecord, SearchQuery
from leadengine.service import LeadService


def test_spearman():
    assert round(spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]), 6) == -1.0
    assert round(spearman([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]), 6) == 1.0
    assert spearman([1, 1, 1, 1, 1], [1, 2, 3, 4, 5]) is None


def test_formula_finds_reviews_matter(session_factory):
    with session_factory() as s:
        repo = Repository(s)
        for z in range(4):                                      # 4 searches x 10 listings, ranked by reviews
            rows = []
            for i in range(10):
                b = repo.upsert_business(BusinessRecord(name=f"Haul Co {z}-{i}", provider="t", place_id=f"P{z}{i}",
                                                        review_count=500 - 50 * i, rating=4.0 + (i % 3) / 10,
                                                        lat=40.7, lng=-73.9))
                rows.append((b, i + 1))
            repo.record_search(SearchQuery("junk removal", f"1136{z}", lat=40.7, lng=-73.9), "t", rows,
                               exhausted=True, api_calls=0)
        s.commit()
        obs = observations(s, "junk removal")
        f = formula(obs)
        top = f["signals"][0]
        assert len(obs) == 40 and top["key"] == "reviews" and top["strength"] > 0.9 and top["expected"]
        weak = Business(name="Small Haul", review_count=20, rating=4.9)
        lv = levers(weak, "junk removal", obs, f)
        assert lv and lv[0]["signal"] == "Number of Google reviews" and "more Google reviews" in lv[0]["action"]
        assert formula(obs[:10]) is None                        # too little data: no formula


def test_citation_gap_against_competitors(settings, session_factory, make_http):
    with session_factory() as s:
        repo = Repository(s)
        me = repo.upsert_business(BusinessRecord(name="Flat Rate Junk", provider="t", place_id="M", phone="7185550100"))
        comps = [repo.upsert_business(BusinessRecord(name=f"Rival {i} Haul", provider="t", place_id=f"R{i}",
                                                     phone=f"71855501{i}9", review_count=200)) for i in range(3)]
        repo.record_search(SearchQuery("junk removal", "11368"), "t", [(c, i + 1) for i, c in enumerate(comps)] + [(me, 4)],
                           exhausted=True, api_calls=0)
        s.commit()
        mid = me.id

    def handler(request):
        q = request.url.params.get("q", "")
        res = []
        if "Rival" in q or any(f"71855501{i}9"[6:] in q for i in range(3)):
            res = [{"link": "https://www.thumbtack.com/ny/rival", "title": q.strip('"') + " | Thumbtack",
                    "snippet": "Rival Haul (718) 555-0109"},
                   {"link": "https://m.yelp.com/biz/rival", "title": q.strip('"') + " - Yelp", "snippet": ""}]
        elif "Flat Rate" in q or "555-0100" in q:
            res = [{"link": "https://m.yelp.com/biz/flat-rate", "title": "Flat Rate Junk - Yelp", "snippet": "(718) 555-0100"}]
        return httpx.Response(200, json={"organic_results": res})

    async def go():
        async with make_http(handler) as http:
            svc = LeadService(replace(settings, sections={**settings.sections, "firecrawl": {"mode": "off"}}),
                              session_factory, http)
            return await svc.citation_audits([mid])
    out = asyncio.run(go())[0]
    assert [g["site"] for g in out["gap"]] == ["Thumbtack"] and out["gap"][0]["competitors"] == 3
