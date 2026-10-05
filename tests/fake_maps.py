"""A tiny local imitation of Google Maps for browser tests (no internet needed).

It mimics what the Playwright provider relies on: a scrollable ``div[role=feed]``
that loads more cards via ``/search?tbm=map`` XHR (XSSI JSON), business data in
``APP_INITIALIZATION_STATE``, place pages with a Reviews tab + "Newest" sort,
"Sponsored" cards, a claim link, and a captcha page.

Behaviour knobs (by keyword in the search path):
* ``blockme``  -> redirect to a /sorry/ captcha page
* ``nothing``  -> empty results (no feed)
* zoom < 15    -> 130 results, list never "ends" (saturated)
* zoom >= 15   -> 12 results then "You've reached the end of the list."
* otherwise    -> 23 results then the end message
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

XSSI = ")]}'\n"
PAGE = 10


def business(i: int, lat: float = 32.78, lng: float = -96.80) -> dict:
    h = hashlib.md5(f"{i}:{lat:.4f}:{lng:.4f}".encode()).hexdigest()
    return {
        "name": f"Dumpster Pros {h[:4].upper()} #{i}",
        "data_id": f"0x{h[:16]}:0x{h[16:32]}",
        "place_id": f"ChIJ{h[:22]}",
        "rating": round(3.5 + (i % 15) / 10, 1),
        "reviews": 10 + i * 7,
        "website": f"https://pros{h[:6]}.com/" if i % 4 else None,
        "phone": f"(214) 555-{1000 + i:04d}" if i % 5 else None,
        "street": f"{100 + i} Elm St",
        "lat": lat + i * 0.001,
        "lng": lng - i * 0.001,
        "category": "Dumpster rental service",
        "sponsored": i == 1,
        "claimed": i % 3 != 0,
    }


def darray(b: dict) -> list:
    d: list = [None] * 200
    d[4] = [None] * 7 + [b["rating"], b["reviews"]]
    d[7] = [b["website"], b["website"][8:] if b["website"] else None] if b["website"] else None
    d[9] = [None, None, b["lat"], b["lng"]]
    d[10] = b["data_id"]
    d[11] = b["name"]
    d[13] = [b["category"], "Waste management service"]
    d[18] = f'{b["name"]}, {b["street"]}, Dallas, TX 75201'
    d[34] = [None, [["Monday", ["7 AM–6 PM"]], ["Sunday", ["Closed"]]]]
    d[78] = b["place_id"]
    d[178] = [[b["phone"], [[b["phone"], 1]]]] if b["phone"] else None
    return d


def search_payload(items: list[dict]) -> str:
    rows = [[None] * 14 + [darray(b)] for b in items]
    return XSSI + json.dumps([["dumpster rental", [["meta"]] + rows]])


def place_href(b: dict) -> str:
    return (f'/maps/place/{b["name"].replace(" ", "+")}/data=!4m7!3m6!1s{b["data_id"]}'
            f'!8m2!3d{b["lat"]}!4d{b["lng"]}!16s%2Fg%2F11x!19s{b["place_id"]}')


SEARCH_HTML = """<!doctype html><html><head><title>Maps</title>
<style>#feed{height:500px;overflow-y:auto} .Nv2PK{height:120px;border-bottom:1px solid #ccc}</style></head>
<body><div role="main"><div role="feed" id="feed"></div></div>
<script>
window.APP_INITIALIZATION_STATE = [[1, 2], null, null, [null, null, %(initial_json)s]];
const TOTAL = %(total)d, ENDS = %(ends)s;
const feed = document.getElementById('feed');
let loaded = 0, loading = false;
function card(d) {
  const b = {name: d[11], data_id: d[10], rating: d[4][7], reviews: d[4][8], lat: d[9][2], lng: d[9][3],
             place_id: d[78], cat: d[13][0], street: d[18].split(', ')[1], phone: d[178] ? d[178][0][0] : ''};
  const sp = b.name.endsWith('#1') ? '<div>Sponsored</div>' : '';
  const href = '/maps/place/' + encodeURIComponent(b.name).replace(/%%20/g, '+') + '/data=!4m7!3m6!1s' + b.data_id +
               '!8m2!3d' + b.lat + '!4d' + b.lng + '!16s%%2Fg%%2F11x!19s' + b.place_id;
  return '<div><div class="Nv2PK"><a class="hfpxzc" aria-label="' + b.name + '" href="' + href + '"></a>' + sp +
    '<div class="fontHeadlineSmall">' + b.name + '</div>' +
    '<span role="img" aria-label="' + b.rating + ' stars ' + b.reviews + ' Reviews"></span>' +
    '<div>' + b.cat + ' · ' + b.street + '</div><div>' + b.phone + '</div></div></div>';
}
function render(rows) { for (const r of rows.slice(1)) feed.insertAdjacentHTML('beforeend', card(r[14])); loaded += rows.length - 1; }
render(JSON.parse(window.APP_INITIALIZATION_STATE[3][2].slice(5))[0][1]);
async function more() {
  if (loading || loaded >= TOTAL) return;
  loading = true;
  const t = await (await fetch('/search?tbm=map&q=%(q)s&start=' + loaded + '&total=' + TOTAL + '&seed=%(seed)s')).text();
  render(JSON.parse(t.slice(5))[0][1]);
  if (loaded >= TOTAL && ENDS) feed.insertAdjacentHTML('beforeend', "<p>You've reached the end of the list.</p>");
  loading = false;
}
feed.addEventListener('scroll', () => { if (feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 40) more(); });
</script></body></html>"""

PLACE_HTML = """<!doctype html><html><body><div role="main">
<h1>%(name)s</h1>
<div class="F7nice"><span aria-hidden="true">%(rating)s</span>
  <span role="img" aria-label="%(rating)s stars"></span><span aria-label="%(reviews)s reviews">(%(reviews)s)</span></div>
<button class="DkEaL" jsaction="pane.rating.category">%(category)s</button>
<button aria-label="%(photos)s photos">See photos</button>
<button role="tab" aria-label="Overview">Overview</button>
<button role="tab" aria-label="Reviews for %(name)s" id="revtab">Reviews</button>
<button data-item-id="address" aria-label="Address: %(street)s, Dallas, TX 75201">%(street)s</button>
%(website_html)s %(phone_html)s %(claim_html)s
<div id="reviews" style="display:none;height:300px;overflow-y:auto">
  <button aria-label="Sort reviews" id="sort">Sort</button>
  <div id="menu" style="display:none">
    <div role="menuitemradio" aria-label="Most relevant">Most relevant</div>
    <div role="menuitemradio" aria-label="Newest" id="newest">Newest</div></div>
  <div id="list"></div>
</div></div>
<script>
window.APP_INITIALIZATION_STATE = [[1], null, null, [null, null, null, null, null, null, %(state_json)s]];
const relevant = %(relevant)s, newest = %(newest)s;
function show(list) { document.getElementById('list').innerHTML = list.map((r, i) =>
  '<div class="jftiEf" data-review-id="r' + i + '"><div class="d4r55">Maria Gonzalez ' + i + '</div>' +
  '<span role="img" aria-label="' + (r[2] || 5) + ' stars"></span><span class="rsqaWe">' + r[0] + '</span>' +
  '<span class="wiI7pd">' + (r[3] || 'Great service') + '</span>' +
  (r[1] ? '<div class="CDe7pd">Response from the owner</div>' : '') + '</div>').join(''); }
document.getElementById('revtab').onclick = () => { document.getElementById('reviews').style.display = 'block'; show(relevant); };
document.getElementById('sort').onclick = () => { document.getElementById('menu').style.display = 'block'; };
document.getElementById('newest').onclick = () => { document.getElementById('menu').style.display = 'none'; show(newest); };
</script></body></html>"""

SERP_HTML = """<!doctype html><html><body><div id="search">
<div data-text-ad="1"><span>Sponsored</span>
  <a href="https://www.googleadservices.com/pagead/aclk?sa=L&adurl=https://pros%(ad_domain)s/landing">
  <div role="heading">Same-Day Dumpster Rental - Free Delivery</div></a>
  <span data-dtld="pros%(ad_domain)s">pros%(ad_domain)s</span> Call (214) 555-1002</div>
<div data-text-ad="1"><span>Sponsored</span><a href="https://www.googleadservices.com/pagead/aclk?adurl=https://bigchain.com/">
  <div role="heading">BigChain Dumpsters</div></a><span data-dtld="bigchain.com">bigchain.com</span></div>
<div class="lsa"><div>Sponsored</div>
  <div data-lsa-card="1"><div role="heading">%(lsa_name)s</div><span>Google Guaranteed</span> 4.8 (31)</div>
  <div data-lsa-card="1"><div role="heading">Other Guaranteed Pro</div><span>Google Screened</span></div>
</div>
<div class="local"><span>Places</span><span>Sponsored</span>
  <div data-cid="111"><span role="heading">Herman's Recycling</span> 4.5 (13) <a href="https://hermans.example.com/">Website</a> (732) 617-0000</div>
  <div data-cid="222"><span role="heading">Organic Pack Business</span> 5.0 (799)</div>
</div>
<div class="g"><a href="https://organic.example.com"><h3>Organic result</h3></a></div>
</div></body></html>"""

SORRY_HTML = "<html><body>Our systems have detected unusual traffic from your computer network.</body></html>"


# business #2 (default seed) runs a search ad, business #3 a Local Services Ad
_b2, _b3 = business(2), business(3)
SERP_HTML = SERP_HTML % {"ad_domain": _b2["website"].split("pros", 1)[1].split("/")[0], "lsa_name": _b3["name"]}


class FakeMaps:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.serp_queries: list[str] = []
        self.blocked_types: list[str] = []
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> "FakeMaps":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # silence
                pass

            def send(self, body: str, status: int = 200, ctype: str = "text/html") -> None:
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                fake.requests.append(self.path)
                url = urlparse(self.path)
                path = unquote(url.path)
                if path.startswith("/maps/search/"):
                    return self.search(path)
                if path == "/search" and "tbm=map" not in url.query:
                    fake.serp_queries.append(url.query)
                    return self.send(SERP_HTML)
                if path == "/search":
                    qs = parse_qs(url.query)
                    start, total = int(qs["start"][0]), int(qs["total"][0])
                    lat, lng = (float(x) for x in qs["seed"][0].split(","))
                    items = [business(i, lat, lng) for i in range(start + 1, min(start + PAGE, total) + 1)]
                    return self.send(search_payload(items), ctype="application/json")
                if path.startswith("/maps/place/"):
                    return self.place(path)
                if path.startswith("/sorry/"):
                    return self.send(SORRY_HTML)
                if path.endswith(".png"):
                    return self.send("png", ctype="image/png")
                return self.send("not found", 404)

            def search(self, path: str) -> None:
                kw = path.split("/")[3]
                if "blockme" in kw:
                    self.send_response(302)
                    self.send_header("Location", "/sorry/index?continue=x")
                    self.end_headers()
                    return
                if "nothing" in kw:
                    return self.send("<html><body><div role='main'>No results</div></body></html>")
                m = re.search(r"@(-?[\d.]+),(-?[\d.]+),(\d+)z", path)
                lat, lng, zoom = (float(m.group(1)), float(m.group(2)), int(m.group(3))) if m else (32.78, -96.80, 13)
                if m and zoom < 15:
                    total, ends = 130, False
                elif m:
                    total, ends = 12, True
                else:
                    total, ends = 23, True
                first = [business(i, lat, lng) for i in range(1, min(PAGE, total) + 1)]
                self.send(SEARCH_HTML % {
                    "initial_json": json.dumps(search_payload(first)), "total": total,
                    "ends": "true" if ends else "false", "q": kw, "seed": f"{lat},{lng}",
                })

            def place(self, path: str) -> None:
                m = re.search(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)!8m2!3d(-?[\d.]+)!4d(-?[\d.]+)", path)
                name = path.split("/")[3].replace("+", " ")
                i = int(name.rsplit("#", 1)[1])
                lat0, lng0 = float(m.group(2)) - i * 0.001, float(m.group(3)) + i * 0.001
                b = business(i, round(lat0, 6), round(lng0, 6))
                b["data_id"] = m.group(1)
                website = (f'<a data-item-id="authority" href="https://www.google.com/url?q={b["website"]}&sa=U">site</a>'
                           if b["website"] else "")
                phone = (f'<button data-item-id="phone:tel:+1214555{1000 + i:04d}">{b["phone"]}</button>'
                         if b["phone"] else "")
                claim = "" if b["claimed"] else '<a href="/claim">Claim this business</a>'
                state = XSSI + json.dumps([None, None, None, None, None, darray(b)])
                self.send(PLACE_HTML % {
                    "name": b["name"], "rating": b["rating"], "reviews": b["reviews"], "category": b["category"],
                    "street": b["street"], "photos": 40 + i, "website_html": website, "phone_html": phone,
                    "claim_html": claim, "state_json": json.dumps(state),
                    "relevant": json.dumps([["3 years ago", False], ["a year ago", True]]),
                    "newest": json.dumps([
                        ["2 days ago", True, 5, "Showed up the same day, dropped the dumpster exactly where we asked and picked it up on time."],
                        ["a week ago", False, 2, "Late pickup and hard to reach by phone this time around."],
                        ["3 weeks ago", True, 5, "Fair price and the driver was careful with our new driveway."],
                        ["2 months ago", True, 4, "ok"]]),
                })

        return Handler
