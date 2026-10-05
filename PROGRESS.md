# PROGRESS

## Current Status
- Current phase: Phase 0 — Audit & plan
- Last completed step: Full read + audit of `LeadHunterPro_v3.py` (3,801 lines, single file)
- Waiting on: owner's approval of the recommended path (B) and answers to the open questions below

## Phase Checklist
- [x] Phase 0 — Audit & plan (audit done, awaiting approval)
- [ ] Phase 1 — Foundation: storage, cache, config, provider interface
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
### Phase 0 — Audit & plan (2026-10-05)
- What was done: read the whole existing tool (`LeadHunterPro_v3.py`) and the master prompt; produced the audit below.
- How it was done: manual line-by-line review; two findings verified by running small Python snippets (email page-queue order, `Pillow` auto-install check).
- How to test: nothing to run yet — read the audit and approve / correct it.
- Test result: (pending owner review)
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
- (2026-10-05) Recommended Path B (new `leadengine/` folder, v3 kept as-is) — pending owner approval.

## Next Steps
- Owner: approve Path B (or choose A), answer open questions.
- Then Phase 1: add v3 to `legacy/`, scaffold `leadengine/`, SQLite schema + cache/TTL, config/.env, logging, credits tracker, provider interface wrapping SerpAPI + Places + (OSM) + legacy Selenium.

### Open questions for owner
1. Approve Path B?
2. OK to commit the original v3 file into this repo under `legacy/`? (Repo is currently empty.)
3. Which OS do you run it on (Windows?) and Python version?
4. The brief mentions "Google Search API" and "OpenStreetMap" sources — the uploaded v3 has Places API + Raw HTTP instead, and no OSM provider. Is there a newer version of the file?

## Setup & Keys
- None yet. v3 stores `places_api_key` and `serp_api_key` in `leadhunter_settings.json` (plaintext). Phase 1 will move these to `.env` (`GOOGLE_PLACES_API_KEY`, `SERPAPI_API_KEY`) with a `.env.example`.
