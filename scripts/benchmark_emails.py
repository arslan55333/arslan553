"""Benchmark: LeadHunter v3 email extractor vs LeadEngine v2 on the same websites.

  python scripts/benchmark_emails.py                 # 20 built-in test sites (offline)
  python scripts/benchmark_emails.py --live sites.txt  # your own list of real websites, one per line

The v3 functions are loaded straight from legacy/LeadHunterPro_v3.py (unchanged file).
Live mode has no answer key: it prints both results side by side for you to judge.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import random
import re
import sys
import time
import types
from dataclasses import replace
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from leadengine.config import Settings  # noqa: E402
from leadengine.enrich.emails import build_email_finder  # noqa: E402
from leadengine.http import HttpClient  # noqa: E402

V3_FUNCS = {"_is_hash", "decode_cloudflare_email", "_clean_emails", "_extract_emails_from_html",
            "get_email_from_site", "_email_quality"}
V3_VARS = {"_EMAIL_FIND_RE", "_BAD_EMAIL_D", "_BAD_EMAIL_P", "_UA_POOL", "_EMAIL_PAGES", "_OBFUSC_PATTERNS"}


def load_v3(requests_module) -> dict:
    tree = ast.parse((ROOT / "legacy" / "LeadHunterPro_v3.py").read_text(encoding="utf-8"))
    body = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name in V3_FUNCS)
            or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in V3_VARS for t in n.targets))]
    ns = {"re": re, "random": random, "time": types.SimpleNamespace(sleep=lambda s: None),
          "urljoin": urljoin, "urlparse": urlparse, "BeautifulSoup": BeautifulSoup, "rq": requests_module}
    exec(compile(ast.Module(body=body, type_ignores=[]), "LeadHunterPro_v3.py", "exec"), ns)
    return ns


async def run_new(sites: list[str], transport=None, insecure_transport=None, verify: str = "none") -> dict:
    settings = Settings.load(root=ROOT)
    settings = replace(settings, sections={**settings.sections,
                                           "emails": {**settings.section("emails"), "verify": verify}})
    out = {}
    async with HttpClient(settings.http, transport=transport) as http:
        finder = build_email_finder(settings, http, insecure_transport=insecure_transport)
        for site in sites:
            t = time.perf_counter()
            report = await finder.find(site)
            out[site] = (report, time.perf_counter() - t)
        await finder.crawler.aclose()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", help="file with one website per line")
    ap.add_argument("--out", default=str(ROOT / "benchmarks" / "email_benchmark.md"))
    args = ap.parse_args()

    if args.live:
        import requests
        sites = [l.strip() for l in Path(args.live).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
        v3 = load_v3(requests)
        new = asyncio.run(run_new(sites, verify="mx"))
        expected = None
    else:
        sys.path.insert(0, str(ROOT / "tests"))
        from fake_web import BAD, EXPECTED, FakeRequests, httpx_handler
        sites = list(EXPECTED)
        v3 = load_v3(FakeRequests())
        new = asyncio.run(run_new(sites, httpx.MockTransport(httpx_handler()),
                                  httpx.MockTransport(httpx_handler(insecure=True))))
        expected = EXPECTED

    rows, v3_ok, new_ok, v3_bad, new_bad, v3_t, new_t = [], 0, 0, 0, 0, 0.0, 0.0
    for site in sites:
        t = time.perf_counter()
        try:
            old = v3["get_email_from_site"](site if site.startswith("http") else "https://" + site) or ""
        except Exception as exc:  # v3 has no error isolation of its own
            old = f"ERROR {type(exc).__name__}"
        v3_t += time.perf_counter() - t
        report, secs = new[site]
        new_t += secs
        best = report.best.email if report.best else ""
        conf = report.best.confidence if report.best else 0
        others = ", ".join(e.email for e in report.emails if not e.is_guess and e.email != best)[:60]
        if expected is not None:
            want = expected[site]
            ok_old = (old in want) if want else (old == "")
            ok_new = (best in want) if want else (best == "")
            v3_ok += ok_old
            new_ok += ok_new
            v3_bad += old in BAD
            new_bad += best in BAD
            rows.append(f"| {site} | {', '.join(want) or '(none)'} | {old or '-'} | {'✅' if ok_old else '❌'} "
                        f"| {best or '-'} ({conf}) | {'✅' if ok_new else '❌'} |")
        else:
            rows.append(f"| {site} | {old or '-'} | {best or '-'} ({conf}) | {others or '-'} | "
                        f"{len(report.pages_crawled)} |")

    lines = ["# Email extractor benchmark: v3 vs v2", ""]
    if expected is not None:
        lines += [f"Built-in test sites: {len(sites)} (offline copies of common small-business site patterns).", "",
                  "| site | correct answer | v3 found | v3 | v2 best (confidence) | v2 |", "|---|---|---|---|---|---|",
                  *rows, "",
                  f"**v3: {v3_ok}/{len(sites)} correct, {v3_bad} wrong-company/junk picks.**  ",
                  f"**v2: {new_ok}/{len(sites)} correct, {new_bad} wrong-company/junk picks.**  ",
                  f"Time: v3 {v3_t:.2f}s, v2 {new_t:.2f}s (no network latency in this mode; v3 sleeps disabled).",
                  "", "v2 verification was off here (fake domains have no MX); guesses are never counted as answers."]
    else:
        lines += [f"Live sites: {len(sites)}", "", "| site | v3 found | v2 best (confidence) | v2 other emails | pages |",
                  "|---|---|---|---|---|", *rows, "", f"Time: v3 {v3_t:.1f}s, v2 {new_t:.1f}s"]
    text = "\n".join(lines)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
