# Constraint Query Streamlit App

This app converts the workbook's live NESO `Constraint_boundary` Power Query
and `DA` `MINIFS` view into Streamlit. It refreshes from the live NESO CSV used
by the workbook query and caches that result for 15 minutes.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy

For Streamlit Community Cloud, deploy this repository with:

- Main file path: `Constraint Query/app.py`
- Python dependencies: `Constraint Query/requirements.txt`

The app reads the live NESO CSV directly. The extractor is only kept for
regenerating optional local support files from the workbook, such as the default
group list and map image:

```bash
python scripts/extract_workbook.py --workbook "/Users/ervin/Downloads/Constraint_Query (MB).xlsx"
```
