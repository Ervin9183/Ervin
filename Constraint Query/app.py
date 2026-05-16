from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st


APP_DIR = Path(__file__).parent
GROUPS_PATH = APP_DIR / "data" / "da_groups.txt"
MAP_PATH = APP_DIR / "data" / "constraint_map.jpeg"
NESO_CSV_URL = (
    "https://api.neso.energy/dataset/cf3cbc92-2d5d-4c2b-bd29-e11a21070b26/"
    "resource/38a18ec1-9e40-465d-93fb-301e80fd1352/download/"
    "day-ahead-constraints-limits-and-flow-output-v1.5.csv"
)
LIVE_REFRESH_TTL_SECONDS = 15 * 60

QUERY_COLUMNS = [
    "Constraint Group",
    "Date (GMT/BST)",
    "Limit (MW)",
    "Flow (MW)",
    "Column1",
    "Column2",
]
NUMERIC_COLUMNS = ["Limit (MW)", "Flow (MW)", "Headroom (MW)"]


st.set_page_config(
    page_title="Constraint Query",
    page_icon=":material/electric_bolt:",
    layout="wide",
)


st.markdown(
    """
    <style>
    .block-container { padding-top: 1.4rem; }
    [data-testid="stMetricValue"] { font-size: 1.65rem; }
    div[data-testid="stDataFrame"] { border: 1px solid #e5e7eb; border-radius: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def load_da_groups() -> list[str]:
    if GROUPS_PATH.exists():
        return [
            line.strip()
            for line in GROUPS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    return [
        "ESTEX",
        "SCOTEX",
        "SEIMP",
        "SSE-SP",
        "SSHARN",
        "SWALEX",
        "BRASIZEX",
        "FLOWSTH",
        "GALLEX",
        "SSEN-S",
        "SPANOREX",
        "SHARN",
        "ERROEX",
        "GM+SNOW5A",
        "HARSPNBLY",
        "NKILGRMO",
        "SEIMPPR23",
        "SSE-SP2",
        "SSHARN3",
    ]


def normalise_query_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    missing = [column for column in QUERY_COLUMNS[:4] if column not in df.columns]
    if missing:
        raise ValueError(f"Missing expected columns: {', '.join(missing)}")

    df = df.rename(columns={"Column1": "Headroom (MW)", "Column2": "Date"})

    df["Date (GMT/BST)"] = pd.to_datetime(
        df["Date (GMT/BST)"],
        errors="coerce",
        format="mixed",
    )
    df["Date"] = df["Date (GMT/BST)"].dt.date

    for column in ["Limit (MW)", "Flow (MW)"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["Headroom (MW)"] = df["Limit (MW)"] - df["Flow (MW)"]
    df = df.dropna(subset=["Constraint Group", "Date (GMT/BST)", "Headroom (MW)"])
    return df.sort_values("Date (GMT/BST)", ascending=False)


@st.cache_data(show_spinner="Refreshing NESO query data...", ttl=LIVE_REFRESH_TTL_SECONDS)
def load_live_csv(url: str) -> pd.DataFrame:
    df = pd.read_csv(
        url,
        dtype={"Constraint Group": "string", "Date (GMT/BST)": "string"},
        low_memory=False,
    )
    return normalise_query_frame(df)


def minifs_table(df: pd.DataFrame, groups: list[str], days: list[date]) -> pd.DataFrame:
    filtered = df[df["Constraint Group"].isin(groups) & df["Date"].isin(days)]
    if filtered.empty:
        return pd.DataFrame(index=groups, columns=[day.isoformat() for day in days])

    summary = (
        filtered.groupby(["Constraint Group", "Date"], as_index=False)["Headroom (MW)"]
        .min()
        .pivot(index="Constraint Group", columns="Date", values="Headroom (MW)")
        .reindex(index=groups, columns=days)
    )
    summary.columns = [column.isoformat() for column in summary.columns]
    return summary


def styled_headroom(frame: pd.DataFrame):
    def headroom_style(value):
        if pd.isna(value):
            return ""
        if value < 0:
            return "background-color: #fee2e2; color: #7f1d1d;"
        if value < 500:
            return "background-color: #fef3c7; color: #713f12;"
        return "background-color: #dcfce7; color: #14532d;"

    return frame.style.format("{:,.0f}", na_rep="-").map(headroom_style)


st.title("Constraint Query")

with st.sidebar:
    st.caption("Live NESO query")
    if st.button("Refresh now", width="stretch"):
        load_live_csv.clear()
        st.rerun()

    default_groups = load_da_groups()

try:
    data = load_live_csv(NESO_CSV_URL)
except Exception as exc:
    st.error(str(exc))
    st.stop()

available_groups = sorted(data["Constraint Group"].dropna().unique().tolist())
selected_defaults = [group for group in default_groups if group in available_groups]

min_date = data["Date"].min()
max_date = data["Date"].max()
today = date.today()
if min_date <= today <= max_date:
    default_start = today
    default_end = min(today + timedelta(days=1), max_date)
elif today > max_date:
    default_start = max(min_date, max_date - timedelta(days=1))
    default_end = max_date
else:
    default_start = min_date
    default_end = min(min_date + timedelta(days=1), max_date)

with st.sidebar:
    selected_groups = st.multiselect(
        "Constraint groups",
        options=available_groups,
        default=selected_defaults or available_groups[:10],
    )
    date_range = st.date_input(
        "Dates",
        value=(default_start, default_end),
        min_value=min_date,
        max_value=max_date,
    )
    top_n = st.slider("Rows", min_value=25, max_value=500, value=100, step=25)

if isinstance(date_range, tuple):
    start_date = date_range[0]
    end_date = date_range[-1]
else:
    start_date = date_range
    end_date = date_range

selected_days = [
    start_date + timedelta(days=offset)
    for offset in range((end_date - start_date).days + 1)
]

scope = data[
    data["Constraint Group"].isin(selected_groups)
    & data["Date"].between(start_date, end_date)
]

metric_cols = st.columns(4)
metric_cols[0].metric("Rows", f"{len(scope):,}")
metric_cols[1].metric("Groups", f"{len(selected_groups):,}")
metric_cols[2].metric("Minimum headroom", f"{scope['Headroom (MW)'].min():,.0f} MW" if not scope.empty else "-")
metric_cols[3].metric("Latest interval", scope["Date (GMT/BST)"].max().strftime("%Y-%m-%d %H:%M") if not scope.empty else "-")

tab_summary, tab_rows, tab_map = st.tabs(["Daily minima", "Half-hourly rows", "Constraint map"])

with tab_summary:
    daily = minifs_table(data, selected_groups, selected_days)
    st.dataframe(styled_headroom(daily), width="stretch", height=520)

    chart_data = (
        scope.groupby(["Date (GMT/BST)", "Constraint Group"], as_index=False)["Headroom (MW)"]
        .min()
        .sort_values("Date (GMT/BST)")
    )
    if not chart_data.empty:
        st.line_chart(
            chart_data,
            x="Date (GMT/BST)",
            y="Headroom (MW)",
            color="Constraint Group",
            height=320,
        )

with tab_rows:
    display = scope.sort_values(["Date (GMT/BST)", "Constraint Group"], ascending=[False, True])
    display = display[
        [
            "Constraint Group",
            "Date (GMT/BST)",
            "Date",
            "Limit (MW)",
            "Flow (MW)",
            "Headroom (MW)",
        ]
    ].head(top_n)
    st.dataframe(
        display.style.format({column: "{:,.0f}" for column in NUMERIC_COLUMNS}),
        width="stretch",
        height=560,
    )

with tab_map:
    if MAP_PATH.exists():
        st.image(str(MAP_PATH), width="stretch")
    else:
        st.warning("Constraint map image not found.")
