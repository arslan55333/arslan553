# LeadEngine

Lead intelligence for US local service businesses: find businesses by **keyword + ZIP**,
store them once, never pay for the same data twice.

> Work in progress — built phase by phase. See `PROGRESS.md` for status.
> The original desktop tool is kept unchanged in `legacy/LeadHunterPro_v3.py`.

## Quick start (Windows / macOS / Linux, Python 3.11+)

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium   # free browser used by the scraper
copy .env.example .env                  # macOS/Linux: cp .env.example .env
# optional: put SERPAPI_API_KEY in .env (only used to fill gaps for shortlisted leads)

python -m leadengine init
python -m leadengine zip 75201                                  # offline ZIP facts + planned grid
python -m leadengine discover "dumpster rental" --zip 75201     # whole-ZIP scan (free)
python -m leadengine emails --zip 75201                         # find + verify emails for saved leads
python -m leadengine find-email acme-roofing.com bestplumber.com  # any domains (old "Bulk Email Finder")
python -m leadengine leads --min-reviews 50
python -m leadengine export leads.csv
python -m leadengine credits
pytest
```

## How `discover` works (hybrid, cost-saving)

1. ZIP → centre + land area from bundled Census-based data (offline, no API).
2. The area is covered by a grid of map cells. Each cell is searched with a real headless
   browser (Playwright): Google Maps' own JSON responses are captured, the DOM is used as a
   fallback, images/fonts are blocked for speed, several browser contexts run in parallel.
3. A cell that is still "full" (Google caps results at ~120) is split into 4 smaller cells
   and searched again — so dense areas are not under-counted.
4. Results are merged and de-duplicated by Google place id / feature id / phone+domain.
   "Sponsored" listings are recorded (an early "runs ads" signal).
5. Shortlisted businesses (enough reviews + rating) get their place page opened for
   activity signals: newest review dates, owner reply rate, claimed status, photo count.
6. Only shortlisted leads still missing phone/website use the paid API (SerpAPI), capped
   per run. Everything is cached — repeating a run costs nothing.
7. Shortlisted websites are crawled for emails (see below).

Options: `--towns` (also search each town), `--cell-km`, `--max-depth`, `--max-cells`,
`--no-activity`, `--no-fill`, `--no-emails`, `--refresh`, `-p serpapi|google_places|osm|selenium`.

## Email finder (Phase 3)

Per website: homepage → linked contact / about / team / privacy pages (footer links too) → standard
paths only if not linked → the business's Facebook page if linked. Extraction covers mailto, visible
text, Cloudflare-protected emails, schema.org JSON-LD, data attributes, JS-assembled addresses,
HTML entities and "name [at] domain [dot] com" styles. Junk (Sentry/Wix ids, image names,
placeholders, no-reply, platform addresses) is rejected.

Every email gets a **confidence (0–100)** and a **source** ("mailto on contact page (/contact-us)").
Emails on another company's domain (usually the web designer) score low. Owner/manager names are
read from schema.org and about pages; pattern guesses (`first@`, `first.last@`, `info@`) are added
and clearly marked **guessed** (capped at 35 unless verified).

Verification levels (`[emails] verify` in config.toml): `mx` (default: syntax + domain accepts mail,
null-MX aware), `smtp` (mailbox check + catch-all detection — needs outbound port 25, so run it on a
VPS), or `reacher` (your self-hosted [Reacher](https://github.com/reacherhq/check-if-email-exists)).

Benchmark v3 vs v2: `python scripts/benchmark_emails.py` (built-in sites) or
`python scripts/benchmark_emails.py --live my_sites.txt` (your real sites). Latest result:
`benchmarks/email_benchmark.md`.

## Website Score (Phase 4)

`python -m leadengine website --zip 75201` (also runs inside `discover` for shortlisted leads).
Score 0–100 (higher = more modern) from separately stored signals, each with a plain-English reason:
HTTPS/certificate expiry, real mobile rendering (Playwright phone emulation), freshness (copyright
year, sitemap lastmod, Last-Modified), outdated tech (old jQuery/WordPress, Flash, FrontPage, table
layouts, frames, pre-HTML5), Google PageSpeed mobile score + Core Web Vitals, conversion basics
(click-to-call, quote/booking form, CTA, reviews), SEO basics, and an optional AI design review of the
screenshot (`--vision`, provider in `[llm]`). Flags: `no_website`, `facebook_only`, `broken`, `parked`,
`ssl_invalid`, `builder_subdomain`, `redirects_elsewhere`. Desktop + mobile screenshots are saved in
`data/screenshots/`. Wider tech detection: `python -m leadengine update-fingerprints` downloads the
open-source webappanalyzer set (GPL-3.0, kept local).

## Google Ads detection (Phase 5)

`python -m leadengine ads --zip 75201 -k "dumpster rental"` (also inside `discover`). Combines:
1. **Live Google search** for "keyword + city" (localised with Google's `uule` location): Sponsored
   text ads and Local Services Ads (Google Guaranteed / Screened), matched to businesses by domain,
   phone or name. Free via the headless browser, or `serp_provider = "serpapi"` (1 credit per
   keyword+city). One results page is cached and shared by every business in that city.
2. **Website code** (free): Google Ads conversion/remarketing tags (`AW-...`), tags hidden inside the
   site's Google Tag Manager container, gclid handling, call tracking (CallRail, CTM, WhatConverts,
   Invoca, Marchex), Meta Pixel (reported separately as Meta ads), Microsoft Ads.
3. **Sponsored on Google Maps** (seen during discovery) and, optionally, the **Ads Transparency
   Center** via SerpAPI (`transparency = true`, 1 credit per business).

Result per business: `ads_status` Active / Likely / Past / None, `lsa` yes/no, confidence and evidence.

## Opportunity Score (Phase 6)

`python -m leadengine score` (runs automatically after `discover`). 0–100 from reputation (rating +
reviews), activity (review recency/velocity, owner replies), ads (Active/Likely/Past/LSA), website
weakness (low score, no site, Facebook-only, broken/parked) and reachability (verified email, phone).
Weights and thresholds live in `[opportunity]` in config.toml. Labels: **Hot / Warm / Cold / Skip**
(Skip = closed, already contacted, low rating, modern site, or no way to contact), each with a one-line
reason. Filters: `python -m leadengine leads --label hot --ads active --max-site-score 40 --email verified`.
Which ZIPs next: `python -m leadengine zips --near 75201 --radius-km 30 -k "dumpster rental"`.

## Dashboard & mini CRM (Phase 7)

`python -m leadengine ui` opens a local web dashboard (http://127.0.0.1:8765):
start scans (keyword + many ZIPs + options) that run in the background with a live log, browse and
filter leads (label, ads, website score, rating, reviews, verified email, search), open a lead to see
ads evidence, website weaknesses with desktop + phone screenshots, emails with confidence/source, and
move it through **New → Preview Built → Emailed → Replied → Won / Lost** with notes. The same business is
never marked Emailed twice (also across duplicate records sharing a domain, email or phone).
Export CSV / Excel from the leads page or `python -m leadengine export leads.xlsx`; Google Sheets with a
service account (`[export] google_credentials_file`, `pip install gspread`). Jobs survive crashes and
resume where they stopped; `python -m leadengine worker` runs the queue without the UI.

![Leads](docs/img/dashboard-leads.jpg)
![Lead detail](docs/img/dashboard-lead1.jpg)

## Preview landing pages (Phase 8)

`python -m leadengine preview 12` (or `--label hot --limit 10`, or the button on a lead page) builds a
modern, fast, mobile-first one-page site for the business: name, click-to-call, services, service area
(nearby towns from the ZIP data), hours, up to 3 real Google review quotes (4–5★, first name + initial),
FAQ, quote form and strong calls to action, in one of three styles (`clean`, `bold`, `warm`).
Copy is written by the AI provider from verified facts only (a filter also removes unverifiable claims
such as years in business, licences, awards, prices, guarantees), or by safe templates (`--no-ai`).

Every preview is clearly a concept: a banner "Website redesign preview prepared for <Business> by <your
brand>", a footer disclaimer, `noindex, nofollow` (meta, `X-Robots-Tag` header, robots.txt), and a demo
form that sends nothing. No Google Maps photos are used. Desktop + phone screenshots are saved for the
outreach email. Publish with `--deploy` to Netlify (`NETLIFY_TOKEN`) or Cloudflare Pages (wrangler),
optionally as `business-slug.previews.yourdomain.com` (`[preview] base_domain`, wildcard DNS).
Set your brand in `[preview]` in config.toml.

![Preview](docs/img/preview-bold.jpg)

## Outreach drafts (Phase 9) - drafts only

`python -m leadengine draft 12 --show` (or `--label hot`, or "Write email drafts" on a lead page) writes
three first-email angles per lead plus a 3-step follow-up sequence (day 3 / 7 / 14):

* **short** - one real problem + the preview + a soft question
* **detailed** - up to 3 measured problems as a list, the Google Ads angle (if they run ads), the preview
* **competitor** - a stronger competitor from the same Maps search (better site / ads / LSA; unnamed unless
  `name_competitors = true`), and their own review lead if they have more reviews

Everything comes from what LeadEngine measured (Website Score reasons translated into plain English,
PageSpeed, ads evidence, reviews, preview link or attached preview screenshot). The AI (`[llm]`) only sees
those facts; a filter drops invented numbers/percentages/guarantees/rankings, foreign links and model
sign-offs, and fake "Re:" subjects. Without an AI key, good template drafts are used (`--no-ai`).
Your signature and a **CAN-SPAM footer** (your postal address + an unsubscribe line) are added by code.

Getting drafts out (nothing is sent):
* `outreach export drafts.csv` - mail-merge CSV for Instantly / Smartlead / lemlist / GMass
  (columns for each follow-up), or `outreach export eml/` - `.eml` files that open as drafts
* `outreach push --imap` - saves them into your mailbox's Drafts folder (Gmail/Outlook app password)
* `outreach push --webhook` - POSTs JSON to n8n / Make / Zapier (`OUTREACH_WEBHOOK_URL`)

**Optional sending (off by default).** Only if you set `[outreach.sending] enabled = true`, fill in
`sender_email` (use a separate outreach domain; `allowed_from_domains` locks it), `physical_address`,
`smtp_host` and the mailbox login in `.env`, **and approve each email yourself**
(`outreach approve 12 --angle short` or the "Approve this one" button). `outreach send` / the Outbox
page then sends approved emails throttled (`max_per_day`, `min_delay_seconds` + jitter), with a
`List-Unsubscribe` header, and refuses leads already contacted (never-contact-twice), on the
do-not-contact list, or with invalid emails. Follow-ups go out in the same thread only while the lead
is still "Emailed". `outreach replies` reads your inbox: replies stop the sequence, "unsubscribe" adds
the address to the do-not-contact list, bounces mark the email invalid.

![Drafts](docs/img/outreach-drafts.jpg)

## AI providers

`[llm]` in config.toml: `claude` (default, `claude-opus-5-5`, official Anthropic SDK, refusal fallback on),
`gemini`, `groq` (text only) or `ollama` (local). Keys in `.env`.

## Providers

| name | cost | notes |
|---|---|---|
| `playwright` (discovery default) | free | headless Chromium; supports proxies |
| `serpapi` (`search` default) | paid, 1 credit per 20 results | free plan 250 searches/month |
| `google_places` | paid (~$35 / 1,000 pages), 1,000 free/month | Places API (New) |
| `osm` | free | OpenStreetMap; low coverage, no ratings |
| `selenium` | free | legacy Chrome scraper from v3 |

## Proxies

Optional. Put `PROXIES=http://user:pass@host:port,...` or `PROXY_FILE=proxies.txt` in `.env`
(residential proxies recommended for big runs). Each browser context gets the next healthy
proxy; failing or captcha'd proxies are benched automatically. `python -m leadengine proxies --check`.

Settings (grid, cache days, retries, concurrency, prices) are in `config.toml`. Secrets only in `.env`.

ZIP data: [Ready APIs curated-us-zips](https://github.com/ReadyAPIs-com/curated-us-zips)
(CC BY 4.0, from U.S. Census Bureau, USPS, SimpleMaps) + U.S. Census ZCTA Gazetteer.
