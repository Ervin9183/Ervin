# Constraint Query Streamlit App

This app converts the workbook's live NESO `Constraint_boundary` Power Query
and `DA` `MINIFS` view into Streamlit. It refreshes from the live NESO CSV used
by the workbook query and caches that result for 15 minutes.

## Run Locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Deploy

For Streamlit Community Cloud, deploy this repository with:

- Branch: `codex/BSAD`
- Main file path: `streamlit_app.py`
- Python dependencies: `requirements.txt`

The app reads the live NESO CSV directly. The `Constraint Query/` folder keeps
the source app copy, default group list, map image, and workbook extractor.
