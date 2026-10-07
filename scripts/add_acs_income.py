"""Fill median household income + median home value for every ZIP in data/us_zips.csv.gz from the free
Census ACS 5-year table-based summary files (no API key):

  https://www2.census.gov/programs-surveys/acs/summary_file/2023/table-based-SF/data/5YRData/acsdt5y2023-b19013.dat
  https://www2.census.gov/programs-surveys/acs/summary_file/2023/table-based-SF/data/5YRData/acsdt5y2023-b25077.dat

Usage: python scripts/add_acs_income.py acsdt5y2023-b19013.dat acsdt5y2023-b25077.dat
"""

from __future__ import annotations

import csv
import gzip
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "leadengine" / "data" / "us_zips.csv.gz"


def read_table(path: str) -> dict[str, int]:
    out = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="|"):
            if row and row[0].startswith("860Z200US"):
                try:
                    v = int(float(row[1]))
                except (ValueError, IndexError):
                    continue
                if v > 0:                          # negative values are Census "not available" codes
                    out[row[0][-5:]] = v
    return out


def main(income_file: str, home_file: str) -> None:
    income, home = read_table(income_file), read_table(home_file)
    with gzip.open(DATA, "rt", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    for r in rows:
        r["median_household_income"] = income.get(r["zip"], r["median_household_income"])
        r["median_home_value"] = home.get(r["zip"], r["median_home_value"])
    with gzip.open(DATA, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    filled = sum(1 for r in rows if r["median_household_income"])
    print(f"{filled}/{len(rows)} ZIPs have income now")


if __name__ == "__main__":
    main(*sys.argv[1:3])
