# LeadEngine

Lead intelligence for US local service businesses: find businesses by **keyword + ZIP**,
store them once, never pay for the same data twice.

> Work in progress — built phase by phase. See `PROGRESS.md` for status.
> The original desktop tool is kept unchanged in `legacy/LeadHunterPro_v3.py`.

## Quick start (Windows / macOS / Linux, Python 3.11+)

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -e ".[dev]"                 # add ,selenium  to use the legacy browser provider
copy .env.example .env                  # macOS/Linux: cp .env.example .env
# open .env and paste your SERPAPI_API_KEY (and/or GOOGLE_PLACES_API_KEY)

python -m leadengine init
python -m leadengine providers
python -m leadengine search "dumpster rental" --zip 75201 --max 20
python -m leadengine search "dumpster rental" --zip 75201      # 2nd time: from cache, 0 credits
python -m leadengine leads --min-reviews 50
python -m leadengine credits --live
python -m leadengine export leads.csv
pytest
```

## Providers

| name | cost | notes |
|---|---|---|
| `serpapi` (default) | paid, 1 credit per 20 results | best coverage; free plan 250 searches/month |
| `google_places` | paid (~$35 / 1,000 pages), 1,000 free/month | Places API (New) |
| `osm` | free | OpenStreetMap; low coverage, no ratings |
| `selenium` | free | legacy Chrome scraper, slow; replaced by Playwright in Phase 2 |

Settings (cache days, retries, concurrency, prices) are in `config.toml`. Secrets only in `.env`.
