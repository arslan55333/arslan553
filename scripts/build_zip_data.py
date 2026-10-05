"""Build the bundled ZIP dataset ``leadengine/data/us_zips.csv.gz``.

Inputs (download once, not committed):
  1. Ready APIs curated-us-zips (CC BY 4.0):
     https://raw.githubusercontent.com/ReadyAPIs-com/curated-us-zips/main/data/us-zips.csv
  2. US Census ZCTA Gazetteer (public domain), tab-separated, for land area:
     https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html
     (any year's ``*_Gaz_zcta_national.txt``)

Usage:  python scripts/build_zip_data.py us-zips.csv Gaz_zcta_national.txt
"""

from __future__ import annotations

import csv
import gzip
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "leadengine" / "data" / "us_zips.csv.gz"
FIELDS = [
    "zip", "lat", "lng", "land_sqmi", "city", "state", "county", "metro_area", "timezone",
    "population", "households", "median_household_income", "median_home_value",
]


def main(zips_csv: str, gazetteer_txt: str) -> None:
    land: dict[str, str] = {}
    with open(gazetteer_txt, encoding="utf-8") as f:
        header = [h.strip() for h in f.readline().split("\t")]
        geoid, aland = header.index("GEOID"), header.index("ALAND_SQMI")
        for line in f:
            parts = [p.strip() for p in line.split("\t")]
            land[parts[geoid]] = parts[aland]

    rows = 0
    with open(zips_csv, newline="", encoding="utf-8") as src, gzip.open(OUT, "wt", newline="", encoding="utf-8") as dst:
        writer = csv.DictWriter(dst, fieldnames=FIELDS)
        writer.writeheader()
        for r in csv.DictReader(src):
            if not r["latitude"] or not r["longitude"]:
                continue
            writer.writerow({
                "zip": r["zip_code"], "lat": r["latitude"], "lng": r["longitude"],
                "land_sqmi": land.get(r["zip_code"], ""), "city": r["city"], "state": r["state"],
                "county": r["county"], "metro_area": r["metro_area"], "timezone": r["timezone"],
                "population": r["population"], "households": r["households"],
                "median_household_income": r["median_household_income"],
                "median_home_value": r["median_home_value"],
            })
            rows += 1
    print(f"wrote {rows} ZIPs -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(*sys.argv[1:3])
