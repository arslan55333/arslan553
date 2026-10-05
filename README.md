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

Options: `--towns` (also search each town), `--cell-km`, `--max-depth`, `--max-cells`,
`--no-activity`, `--no-fill`, `--refresh`, `-p serpapi|google_places|osm|selenium`.

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
