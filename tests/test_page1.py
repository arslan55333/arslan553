from leadengine.enrich.ads.serp import SerpSnapshot, canonical_location, page1_from_serpapi
from leadengine.enrich.page1 import page1, page1_line

SNAP = {"keyword": "junk removal", "location": "Jackson Heights, NY",
        "ads": [{"kind": "search", "title": "Junk Removal Made Easy", "domain": "junkluggers.com", "position": 1},
                {"kind": "lsa", "title": "The Junkluggers of Queens", "domain": None, "position": 1, "phone": "7185550111"}],
        "local_pack": [{"position": 1, "name": "The Junkluggers of Queens", "rating": 4.9, "reviews": 233},
                       {"position": 2, "name": "Ant's Junk Removal", "rating": 5, "reviews": 179},
                       {"position": 3, "name": "MYG - Junk Removal", "rating": 5, "reviews": 128}],
        "organic": [{"position": 1, "title": "Junk Removal in Jackson Heights", "url": "https://junkluggers.com/x",
                     "domain": "junkluggers.com"},
                    {"position": 2, "title": "THE BEST 10 JUNK REMOVAL", "url": "https://yelp.com/x", "domain": "yelp.com"},
                    {"position": 3, "title": "Contact Us", "url": "https://mygjunk.com/c", "domain": "mygjunk.com"}]}


def test_page1_owners_and_me():
    p = page1(SNAP, name="Ant's Junk Removal", domain="antsjunkremoval.com",
              name_domains={"The Junkluggers of Queens": "junkluggers.com", "MYG - Junk Removal": "mygjunk.com"})
    top = p["owners"][0]
    assert top["name"] == "The Junkluggers of Queens" and top["spots"] == 3 and set(top["sections"]) == {"search", "map", "organic"}
    assert p["my_spots"] == 1 and p["map_position"] == 2 and p["organic_position"] is None and "yelp.com" in p["directories"]
    myg = next(o for o in p["owners"] if o["domain"] == "mygjunk.com")
    assert myg["spots"] == 2 and myg["name"] == "MYG - Junk Removal"           # map + organic merged, business name kept
    assert 'The Junkluggers of Queens holds 3 spots' in page1_line(p)
    assert page1_line(page1({"keyword": "x", "ads": [], "local_pack": [], "organic": []}, name="A", domain=None)) is None


def test_serpapi_page1_and_zip_location():
    org, pack = page1_from_serpapi({"organic_results": [{"position": 1, "title": "A", "link": "https://a.com/x"}],
                                    "local_results": {"places": [{"position": 1, "title": "Ant's", "rating": 5, "reviews": 179,
                                                                  "links": {"website": "https://antsjunkremoval.com"}}]}})
    assert org[0]["domain"] == "a.com" and pack[0]["domain"] == "antsjunkremoval.com"
    assert canonical_location("Jackson Heights", "NY", "11372") == "11372,New York,United States"
    s = SerpSnapshot.from_dict({**SNAP, "provider": "playwright", "ads": []})
    assert s.organic and s.local_pack and SerpSnapshot.from_dict({"keyword": "k", "location": "l", "provider": "p"}).organic == []
