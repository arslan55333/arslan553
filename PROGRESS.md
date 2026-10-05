# PROGRESS

## Current Status
- Current phase: Phase 1 — Foundation (built, waiting for owner test)
- Last completed step: storage + cache + config + logging + credits + provider interface + CLI; 48 tests passing
- Waiting on: owner testing Phase 1 on their machine with a real SerpAPI key, then saying "next"

## Phase Checklist
- [x] Phase 0 — Audit & plan (approved: Path B)
- [ ] Phase 1 — Foundation: storage, cache, config, provider interface (built, awaiting owner test)
- [ ] Phase 2 — Scraping engine v2 (Playwright, ZIP grid, proxies)
- [ ] Phase 3 — Email extraction v2 (+ verification, confidence)
- [ ] Phase 4 — Website Score (0–100)
- [ ] Phase 5 — Google Ads detection
- [ ] Phase 6 — Opportunity Score & filtering
- [ ] Phase 7 — Dashboard & mini CRM
- [ ] Phase 8 — Preview landing page generator
- [ ] Phase 9 — AI personalised outreach drafts
- [ ] Phase 10 — Hardening & polish

## Phase Log (newest first)
### Phase 1 — Foundation: storage, cache, config (2026-10-05)
- What was done:
  - `legacy/LeadHunterPro_v3.py` — original tool copied byte-for-byte (untouched, still runs on its own).
  - New package `leadengine/` with a CLI (`python -m leadengine ...`): `init`, `providers`, `search`, `leads`, `credits [--live]`, `stats`, `export`.
  - SQLite database (`data/leadengine.db`) via SQLAlchemy 2 — generic types, so Postgres = change `DATABASE_URL` only.
  - Master `businesses` table keyed by Google `place_id`; fallback dedupe on normalised phone + own domain (Facebook/Yelp etc. are ignored as "domains"). Two different place_ids are never merged.
  - Search cache: same provider + keyword (case/space-insensitive) + ZIP within 14 days → served from DB, **0 API calls**. `--refresh` forces a paid re-fetch. Bigger `--max` than cached → re-fetch.
  - Enrichment cache (`leadengine/cache.py::cached_enrichment`) with per-kind TTL, timestamped history in `enrichments` — ready for Phases 3–5.
  - Credits tracker: every external call logged in `api_usage` (success/failed, list-price estimate, free calls left this month). `credits --live` asks SerpAPI for real remaining searches (free endpoint).
  - Shared async HTTP client: timeouts, retries with exponential backoff on 429/5xx/network errors (honours Retry-After), concurrency limit, optional delay — all from `config.toml`. Error messages never contain API keys; httpx URL logging is silenced.
  - Providers behind one interface (`leadengine/providers/base.py`): `serpapi` (Google Maps engine, pagination to ~120), `google_places` (Places API **New**, up to 60), `osm` (Overpass, free), `selenium` (v3 scraper ported: dedupe by place id, captures place_id/lat/lng, no webdriver-manager).
  - ZIP → lat/lng via Nominatim, cached 365 days (temporary; Phase 2 uses bundled Census ZCTA data).
  - Structured logging: readable console + JSON lines in `logs/leadengine.jsonl`.
- How it was done (key files, libraries, design decisions & why):
  - `config.py` (config.toml + .env, real env vars win), `db/models.py`, `db/repo.py` (all storage rules), `service.py` (cache → geocode → provider → merge → record), `http.py`, `credits.py`, `providers/*`, `cli.py` (typer + rich).
  - Sync SQLAlchemy inside async code: fine for SQLite; each record is stored inside a SAVEPOINT so one bad record cannot break a run.
  - Raw HTTP provider from v3 dropped (it did not work). Places switched to the New API because the legacy API cannot be enabled on new Google Cloud projects.
- How to test: see "How to test Phase 1" below.
- Test result (filled after I test): (pending)
- Known issues / limitations:
  - Not tested against live SerpAPI / Google / OSM from the build machine (its network blocks them); all providers tested with recorded-format mock responses, and the whole CLI was run end-to-end against a fake SerpAPI.
  - Selenium provider not run here (no Chrome in the build container); only its URL parsing is unit-tested.
  - OSM coverage for US service businesses is low; it is a free fallback, not a main source.
  - Cost figures are list-price estimates from `config.toml`; SerpAPI's own count (`credits --live`) is the source of truth.
  - No DB migrations tool yet (tables are created automatically); Alembic can be added when the schema starts changing.

#### How to test Phase 1
1. Python 3.11+ installed. In the project folder:
   `python -m venv .venv` → activate (`.venv\Scripts\activate` on Windows) → `pip install -e ".[dev]"`
2. `copy .env.example .env`, open `.env`, paste `SERPAPI_API_KEY=...` (free key from serpapi.com).
3. `pytest` → expect `48 passed`.
4. `python -m leadengine providers` → serpapi shows **ready**.
5. `python -m leadengine search "dumpster rental" --zip 75201 --max 20` → table of ~20 businesses, last line `LIVE - 1 API call(s)`.
6. Run the exact same command again → same table, last line `From CACHE - 0 API calls`.
7. `python -m leadengine credits --live` → serpapi 1 call this month; SerpAPI's own "searches left" number.
8. `python -m leadengine leads --min-reviews 50` and `python -m leadengine export leads.csv` → open leads.csv in Excel.
9. Optional: `python -m leadengine search "lawn care" --zip 75201 -p osm` (free source).
10. Old tool still works: `python legacy/LeadHunterPro_v3.py`.

### Phase 0 — Audit & plan (2026-10-05)
- What was done: read the whole existing tool (`LeadHunterPro_v3.py`) and the master prompt; produced the audit below.
- How it was done: manual line-by-line review; two findings verified by running small Python snippets (email page-queue order, `Pillow` auto-install check).
- How to test: nothing to run yet — read the audit and approve / correct it.
- Test result: approved by owner — go with Path B, keep the good parts, start fresh.
- Known issues / limitations: the existing tool's source is NOT in this repo yet (repo was empty). It was reviewed from an uploaded copy.

#### Audit summary
**Stack:** Python, CustomTkinter desktop GUI, Selenium + webdriver-manager, requests + BeautifulSoup, dnspython, pandas, openpyxl, folium. Everything in one 3.8k-line file. No database, no tests, no logging framework.

**Actual scraping sources in code:** Selenium (Google Maps DOM), "Raw HTTP" (regex over Maps HTML), Google Places API (legacy Nearby + Details), SerpAPI (google_maps engine). OpenStreetMap is only used for city lookup / geocoding / heatmap tiles — there is no OSM business-discovery provider.

**Good parts (keep / port):**
- Email extractor ideas: Cloudflare `cfemail` decode, 8 obfuscation patterns, mailto, entity decode, JSON/comments scan, hash/token filter, junk-domain + junk-prefix blacklists, quality ranking.
- Selenium consent-dismiss + stealth flags; SerpAPI pagination; RDAP domain-age chain; folium rank heatmap; CSV/Excel/Mailwizz export.

**Critical bugs:**
1. Email crawler never visits `/contact` or `/about`. Guessed paths are inserted at index 0 one by one, so the queue is reversed; with the 10-page cap it visits `/who-we-are, /company, /en/about, /en/contact, /pages/about, /pages/contact, /hello, /help, /people, /staff` and never reaches `/contact`, `/about` or real links found on the homepage. (Verified.)
2. Lead scoring is the opposite of the business goal: it rewards no website, few reviews, low rating and low rank. Target is strong reviews + running ads + weak website.
3. Results table: double-click / right-click map the tree row index to `self._results`, so with any filter or search active they show/copy the WRONG business's data.
4. Places API path has no try/except; a network error kills the worker thread, `_on_done` never runs, UI stays stuck in "running".
5. Results live only in memory — closing the app loses them; no dedupe across runs, so paid SerpAPI/Places calls are repeated.
6. No `place_id` captured by any provider; Selenium dedupes by lowercase name (drops different branches with the same name).

**Other bugs / weaknesses:**
- `REQUIRED["Pillow"]` → `__import__("Pillow")` always fails (module is `PIL`), so pip runs on every launch. Auto-pip-install at import is also a supply-chain / reliability risk.
- Email enrichment is sequential, up to ~11 pages × (https + http retry) × 8–10 s timeout per site; `bulk_find_email` then repeats the www variant the crawler already tried.
- Only ONE email kept per business; no preference for emails on the business's own domain (old sites often show the web designer's email in the footer).
- `verify_email`: "MX exists" is reported as valid/90; unknown DNS errors are reported as valid/50; no catch-all / SMTP; syntax regex caps TLD at 7 chars (rejects `.photography`, `.consulting`) while the extractor allows 10.
- `_email_quality` prefix rules: `auto*` hard-rejected (bad for auto-repair niches), `pr*` gets a bonus (`preston@`).
- Places API: legacy endpoints (not enable-able on new Google Cloud projects since Mar 2025); one paid Details call per result; `hours` stored as "Open" only if open at scrape time; category is raw `types`.
- "Raw HTTP" provider is effectively non-functional (regex over minified Maps HTML; several variables computed and unused).
- Selenium: obfuscated CSS classes (`hfpxzc`, `DUwDvf`, `F7nice`) break often; fixed sleeps; clicks every card (~4–7 s each); Google caps a search at ~120 results, so "500" max results is not reachable; no phone/lat-lng/place URL from list view; webdriver-manager redundant with Selenium Manager.
- Pause only works during enrichment, not during scraping.
- Bulk-email and heatmap Stop buttons re-enable Start while worker threads are still running → two runs can write to the same lists.
- Heatmap: launches a fresh Chrome per grid point (49 launches for 7×7); "Target Business" marker is placed at the city centre, not at the business; loose name matching (`"dental" in name`); `rank-1` label shows "Not found (>-2)" when no data.
- `HIST_FILE` uses `"__file__" in dir()` inside the class body (always False) → history JSON is written to the current working directory, while settings go next to the script.
- `verify=False` on most requests + global `warnings.filterwarnings("ignore")`; ~50 bare `except:` blocks hide real errors.
- No ZIP-code input at all (country + city dropdown; default country Pakistan). Target workflow is keyword + US ZIP.
- Outdated pricing text in Settings tab ("$200 credit/month", "5000 free requests", SerpAPI "$50/month").

**Security:** no API keys hardcoded in source (good), but keys are saved in plaintext `leadhunter_settings.json` next to the script (easy to commit/share by mistake). TLS verification disabled on most calls. Business names are injected unescaped into heatmap HTML (local-only, low risk).

**Scalability:** single process, threads + Tk `after()`, no queue/resume, no cache, no rate-limit config, no credit tracking, desktop-only UI. Fine for 100 leads at a time; not for multi-ZIP runs.

#### Recommendation: Path (B) — new clean version alongside v3
v3 mixes UI, scraping, enrichment and scoring in one file, has no storage layer and no ids, and its scoring model is inverted. Phases 1–10 need a DB keyed by `place_id`, cache/TTL, a provider interface, async Playwright and a web dashboard — retrofitting all that into the Tk file would be a rewrite anyway. Build `leadengine/` next to v3, port the good parts (email extractor with the queue bug fixed, cfemail, obfuscation, filters, RDAP, SerpAPI mapping, Selenium consent handling), and leave v3 untouched and runnable.

#### Proposed structure
```
legacy/LeadHunterPro_v3.py        # untouched original
leadengine/
  config.py  logging.py  credits.py
  db/        models.py repo.py cache.py migrations/
  providers/ base.py serpapi.py places.py osm.py playwright_maps.py selenium_legacy.py
  geo/       zcta.py grid.py
  enrich/    emails/ (crawl, extract, verify, guess)  website_score/  ads/  domain_age.py
  scoring/   opportunity.py
  outreach/  preview_pages/  drafts/
  ui/        (Phase 7)
  cli.py
data/zcta_gazetteer.txt
tests/
.env.example  pyproject.toml  PROGRESS.md
```

#### Proposed data model (core tables)
- `businesses` — place_id (PK), dedupe_key (norm phone + domain), name, categories, phone, address, city, state, zip, lat, lng, website, domain, rating, review_count, last_review_at, photo_count, claimed, hours_json, first_seen, last_seen
- `business_sources` — place_id, provider, raw_json, fetched_at
- `searches` — id, keyword, zip, provider, grid_cell, result_count, ran_at
- `search_results` — search_id, place_id, rank
- `enrichments` — place_id, kind (emails / website_score / ads / domain_age / pagespeed …), payload_json, fetched_at, expires_at
- `emails` — place_id, email, source, is_guess, verification, confidence, checked_at
- `lead_status` — place_id, status (New → Preview Built → Emailed → Replied → Won/Lost), updated_at, notes
- `api_usage` — provider, endpoint, units, cost_estimate, at

## Decisions Log
- (2026-10-05) Path B approved: new `leadengine/` package, v3 kept untouched in `legacy/`.
- (2026-10-05) SQLite + SQLAlchemy 2 (Postgres-ready). Internal integer id, `place_id` unique; fallback dedupe = phone + own domain.
- (2026-10-05) Google Places → Places API (New); legacy API can't be enabled on new projects since Mar 2025.
- (2026-10-05) v3 "Raw HTTP" provider dropped (non-functional). Selenium kept as legacy provider until Playwright (Phase 2).
- (2026-10-05) CLI first (typer + rich); web dashboard decided in Phase 7.
- (2026-10-05) Free-tier check: SerpAPI free = 250 searches/month; Places Text Search Enterprise ≈ $35/1,000 with 1,000 free/month.

## Next Steps
- Owner: test Phase 1 (steps above) and reply "next" or report problems.
- Then Phase 2 — Scraping engine v2: async Playwright Maps scraper (network-JSON interception, DOM fallback), bundled Census ZCTA gazetteer (replaces Nominatim), adaptive grid with 4-way split on saturation, proxy rotation, extra fields (photo count, latest review dates, owner responses, claimed).

### Open questions for owner
1. Which OS and Python version do you use? (Instructions assume Windows + Python 3.11+.)
2. The brief mentions a "Google Search API" source — the uploaded v3 has Places API + Raw HTTP instead. Is there a newer version of the file?
3. GitHub push from the build session is blocked (Claude GitHub App has no access to this repo) — needs reconnecting before commits can be pushed.

## Setup & Keys
`.env` (copy from `.env.example`; never committed — in `.gitignore`):
- `SERPAPI_API_KEY` — SerpAPI Google Maps searches (default provider).
- `GOOGLE_PLACES_API_KEY` — Google Places API (New); enable "Places API (New)" in Google Cloud.
- `CONTACT_EMAIL` — sent in the User-Agent to free OpenStreetMap services (their policy asks for it).
- `DATABASE_URL` — optional; default SQLite `data/leadengine.db`.
- `LOG_LEVEL` — DEBUG / INFO / WARNING.
Non-secret settings (cache days, retries, concurrency, price estimates) live in `config.toml`.
The legacy v3 tool still keeps its own keys in `leadhunter_settings.json` (ignored by git).
