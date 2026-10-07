# Bundled data

`us_zips.csv.gz` — one row per US ZIP code (centroid, land area, city, county, metro, population,
households, median income, median home value). Population is filled for ~99.6% of ZIPs, income and
home value for ~92% (Census ACS 5-year 2023 bulk tables, merged with `scripts/add_acs_income.py`).
Rebuild with `scripts/build_zip_data.py`, then `scripts/add_acs_income.py`.

Attribution (required by CC BY 4.0):

> ZIP data from [Ready APIs curated-us-zips](https://github.com/ReadyAPIs-com/curated-us-zips),
> sourced from U.S. Census Bureau (ACS 5-year 2022, Gazetteer), USPS, and SimpleMaps. Licensed CC BY 4.0.
> Median household income and home value: U.S. Census Bureau, ACS 5-year 2023 (public domain).
> Land area from the U.S. Census Bureau ZCTA Gazetteer (public domain).
