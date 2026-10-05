import json
from datetime import datetime, timedelta

import pytest

from leadengine.providers.maps_parser import (
    businesses_from_payload,
    get_path,
    is_blocked_page,
    loads_maps_json,
    parse_card,
    parse_place_dom,
    parse_place_url,
    parse_rating_label,
    parse_relative_date,
    strip_xssi,
    unwrap_google_redirect,
)
from tests.fake_maps import business, darray, search_payload

NOW = datetime(2026, 10, 5, 12, 0)


def test_strip_and_load_xssi():
    assert strip_xssi(")]}'\n[1,2]") == "[1,2]"
    assert strip_xssi(')]}\'\n[1]/*""*/') == "[1]"
    assert loads_maps_json(")]}'\n[1, [2]]") == [1, [2]]
    assert loads_maps_json("<html>") is None


def test_get_path_is_safe():
    data = [0, [1, [2, 3]]]
    assert get_path(data, 1, 1, 0) == 2
    assert get_path(data, 1, 5) is None
    assert get_path(data, 0, 0) is None
    assert get_path(None, 0) is None


def test_businesses_from_search_payload_and_nested_strings():
    items = [business(i) for i in (1, 2, 5)]
    payload = loads_maps_json(search_payload(items))
    found = businesses_from_payload(payload)
    assert [f["name"] for f in found] == [b["name"] for b in items]
    first = found[0]
    assert first["place_id"] == items[0]["place_id"]
    assert first["rating"] == items[0]["rating"] and first["review_count"] == items[0]["reviews"]
    assert first["website"] == items[0]["website"]
    assert first["address"] == "101 Elm St, Dallas, TX 75201"     # business name prefix removed
    assert first["categories"][0] == "Dumpster rental service"
    assert first["hours"] == {"Monday": "7 AM–6 PM", "Sunday": "Closed"}
    assert found[2]["phone"] is None                                 # business 5 has no phone
    # same data wrapped as a string inside APP_INITIALIZATION_STATE-like structure
    state = [[1], None, [None, json.dumps("x"), search_payload(items)]]
    assert len(businesses_from_payload(state)) == 3


def test_wrong_types_are_dropped_not_trusted():
    d = darray(business(3))
    d[4] = [None] * 7 + ["4.5", -3]        # strings / negatives where numbers belong
    d[7] = ["javascript:alert(1)"]
    d[9] = [None, None, 500, "x"]
    d[178] = [["not a phone"]]
    d[78] = "notaplaceid"
    parsed = businesses_from_payload([d])[0]
    assert parsed["rating"] is None and parsed["review_count"] is None
    assert parsed["website"] is None and parsed["lat"] is None and parsed["lng"] is None
    assert parsed["phone"] is None and parsed["place_id"] is None


def test_parse_place_url_and_redirect():
    url = ("https://www.google.com/maps/place/Acme/data=!4m7!3m6!1s0x864c19f77b45974b:0xb9ec9ba4f647678f"
           "!8m2!3d32.7767!4d-96.797!16s%2Fg%2F11b6!19sChIJS5dFe_cZTIYRj2dH9qSb7Lk?authuser=0")
    assert parse_place_url(url) == {"place_id": "ChIJS5dFe_cZTIYRj2dH9qSb7Lk",
                                     "data_id": "0x864c19f77b45974b:0xb9ec9ba4f647678f",
                                     "lat": 32.7767, "lng": -96.797}
    assert unwrap_google_redirect("https://www.google.com/url?q=https://acme.com/&sa=U") == "https://acme.com/"
    assert unwrap_google_redirect("https://acme.com") == "https://acme.com"


@pytest.mark.parametrize("label, expected", [
    ("4.6 stars 1,234 Reviews", (4.6, 1234)),
    ("4,8 stars 87 reviews", (4.8, 87)),
    ("No reviews", (None, None)),
    (None, (None, None)),
])
def test_parse_rating_label(label, expected):
    assert parse_rating_label(label) == expected


def test_parse_card():
    card = parse_card({
        "href": "https://www.google.com/maps/place/X/data=!4m7!3m6!1s0xabc:0xdef!8m2!3d32.1!4d-96.2!19sChIJabcdefghijkl",
        "name": "Acme Roll-Off",
        "text": "Sponsored\nAcme Roll-Off\n4.7(1.2K)\nDumpster rental service · 12 Main St\nOpen 24 hours · (214) 555-0188",
        "rating_label": None,
    })
    assert card["rating"] == 4.7 and card["review_count"] == 1200
    assert card["category"] == "Dumpster rental service"
    assert card["phone"] == "(214) 555-0188" and card["sponsored"] is True
    assert card["data_id"] == "0xabc:0xdef" and card["place_id"] == "ChIJabcdefghijkl"


@pytest.mark.parametrize("text, days", [
    ("2 weeks ago", 14), ("a month ago", 30.44), ("Edited 3 years ago", 3 * 365.25),
    ("yesterday", 1), ("an hour ago", 1 / 24), ("5 days ago", 5),
])
def test_relative_dates(text, days):
    assert parse_relative_date(text, NOW) == NOW - timedelta(days=days)


def test_relative_date_unknown():
    assert parse_relative_date("on 3/4/2021", NOW) is None and parse_relative_date(None, NOW) is None


def test_parse_place_dom():
    dom = {
        "name": "Acme", "rating_label": "4.9 stars 210 reviews", "category": "Roofing contractor",
        "address": "1 Main St, Dallas, TX 75201", "website": "https://www.google.com/url?q=https://acme.com/",
        "phone_id": "phone:tel:+12145550100", "claim_link": True, "photos_text": "1,204 photos",
        "reviews": [{"date": "3 days ago", "owner_response": True}, {"date": "2 months ago", "owner_response": False},
                    {"date": "a week ago", "owner_response": True}, {"date": None, "owner_response": False}],
    }
    out = parse_place_dom(dom, NOW)
    assert out["rating"] == 4.9 and out["review_count"] == 210
    assert out["website"] == "https://acme.com/" and out["phone"] == "+12145550100"
    assert out["claimed"] is False and out["photo_count"] == 1204
    assert out["last_review_at"] == NOW - timedelta(days=3)
    assert out["recent_review_dates"][0] == "2026-10-02" and len(out["recent_review_dates"]) == 3
    assert out["owner_response_rate"] == 0.5


def test_blocked_detection():
    assert is_blocked_page("https://www.google.com/sorry/index?continue=x", "")
    assert is_blocked_page("https://x", "Our systems have detected unusual traffic from your computer network.")
    assert not is_blocked_page("https://www.google.com/maps/search/x", "Results")
