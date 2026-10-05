"""Local HTTP server with an old, a modern and a parked small-business website."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OLD_SITE = """<HTML><HEAD><TITLE>Bob's Septic Service</TITLE>
<META NAME="GENERATOR" CONTENT="Microsoft FrontPage 5.0">
<script src="/js/jquery-1.7.2.min.js"></script>
<script>window.jQuery = {fn: {jquery: "1.7.2"}};</script></HEAD>
<BODY BGCOLOR="#FFFFFF"><CENTER><TABLE WIDTH="1000" BORDER="0"><TR><TD VALIGN="top">
<FONT FACE="Arial" SIZE="2"><B>Bob's Septic Service</B> - Serving Dallas since 1985.
Call 214-555-0199. We pump tanks, fix drain fields and more.</FONT>
<TABLE><TR><TD><IMG SRC="/truck.gif"></TD></TR></TABLE>
<object type="application/x-shockwave-flash" data="/intro.swf"></object>
<MARQUEE>Spring special!</MARQUEE>
<P><FONT SIZE="1">Copyright &copy; 2009 Bob's Septic. All rights reserved.</FONT></P>
</TD></TR></TABLE></CENTER></BODY></HTML>"""

MODERN_SITE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clear Flow Septic | Dallas Septic Pumping</title>
<meta name="description" content="Fast septic pumping and repair in Dallas.">
<script async src="https://www.googletagmanager.com/gtag/js?id=AW-123456789"></script>
<script>gtag('config', 'AW-123456789'); gtag('config', 'G-ABC123');</script>
<script src="https://cdn.callrail.com/companies/123/abc/12/swap.js"></script>
<style>body{font-family:sans-serif;margin:0;font-size:17px} .wrap{max-width:960px;margin:auto;padding:16px}
.hero{padding:40px 16px} img{max-width:100%}</style></head>
<body><div class="wrap"><header><a href="tel:+12145550123">Call (214) 555-0123</a></header>
<section class="hero"><h1>Septic pumping in Dallas - same-day service</h1>
<p>Licensed, insured and family owned. Get a free estimate in minutes.</p>
<a href="#quote">Get a Free Quote</a></section>
<section><h2>What our customers say</h2><p>"Fast and honest!" - Maria</p></section>
<form id="quote"><input name="name" placeholder="Name"><input name="phone" placeholder="Phone">
<textarea name="message"></textarea><button>Request Service</button></form>
""" + "<p>We serve Dallas, Plano, Irving and nearby towns with pumping, inspections and repairs.</p>" * 20 + """
<footer>© 2018-2026 Clear Flow Septic LLC</footer></div></body></html>"""

PARKED = """<html><head><title>bobsplumbing.com</title></head><body>
<h1>bobsplumbing.com</h1><p>This domain may be for sale. Buy this domain today!</p>
<script src="https://sedoparking.com/frmpark.js"></script></body></html>"""

ROUTES = {"/old/": OLD_SITE, "/modern/": MODERN_SITE, "/parked/": PARKED}


class FakeSites:
    def __init__(self) -> None:
        sites = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):  # noqa: N802
                path = self.path.split("?")[0]
                key = next((k for k in ROUTES if path.startswith(k)), None)
                if key and path in (key, key.rstrip("/")):
                    body, status = ROUTES[key].encode(), 200
                else:
                    body, status = b"not found", 404
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                if key == "/old/":
                    self.send_header("Server", "Microsoft-IIS/6.0")
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def url(self, key: str) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/{key}/"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
