# NESO BSAD Requirements Streamlit App

This Streamlit app replaces the Excel Power Query in `bsad_requirements-8_traderNESO - Copy.xlsx`.

The Excel query was pulling from the NESO CKAN SQL endpoint:

`https://api.neso.energy/api/3/action/datastore_search_sql`

Resource:

`6a928369-bed3-445f-af8a-69cdb2cc5089`

## What It Does

- Fetches published BSAD requirement rows from the NESO API.
- Caches API results between refreshes to avoid repeated slow requests.
- Checks for new records every 60 seconds and shows an in-app alert when a newer
  `Published DateTime` appears.
- Recreates the Excel Power Query transformations:
  - parses date/time columns
  - calculates `IsBST`
  - adds `Adjusted Start Time`
  - calculates `Settlement Period`
  - sorts by `Start Time` descending
- Adds sidebar filters for date range, row limit, buy/sell, and settlement period.
- Hides unrelated workbook columns from the table and CSV export.
- Keeps the chart focused on current-day cleared volume by settlement period,
  split by buy/sell.
- Provides a CSV download of the filtered data.

## Run Locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

The default date range matches the workbook query: `2025-05-01` to `2026-12-31`, with a default API limit of `100` rows.
