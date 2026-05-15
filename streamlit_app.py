from __future__ import annotations

from datetime import date, datetime, time, timedelta

import altair as alt
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh


API_URL = "https://api.neso.energy/api/3/action/datastore_search_sql"
RESOURCE_ID = "6a928369-bed3-445f-af8a-69cdb2cc5089"
DEFAULT_START_DATE = date(2025, 5, 1)
DEFAULT_END_DATE = date(2026, 12, 31)
DEFAULT_REFRESH_SECONDS = 60
LOCAL_TIMEZONE = "Europe/London"

IC_VOLUME_COLUMNS = {
    "VKL": "VKL Volume",
    "EL": "EL Volume",
    "IFA1": "IFA1 Volume",
    "IFA2": "IFA2 Volume",
    "NEMO": "NEMO Volume",
    "BN": "BN Volume",
}
IC_ORDER = list(IC_VOLUME_COLUMNS.keys())

HIDDEN_COLUMNS = {
    "Auction ID",
    "Default Price",
    "Auction Lot ID",
    "_count",
    "_full_text",
    "_id",
}

COLUMN_ORDER = [
    "Bid Deadline",
    "Buy Sell",
    "Start Time",
    "End Time",
    "Settlement Period",
    "Volume Required",
    "Cleared Volume",
    "Clearing Price",
    "VWA Price",
    "Best Price",
    "Total Bid Volume",
    "Published DateTime",
    "Qualified IC",
    "Notes",
    "VKL Volume",
    "EL Volume",
    "IFA1 Volume",
    "NEMO Volume",
    "BN Volume",
    "IFA2 Volume",
    "IsBST",
    "Adjusted Start Time",
]

DATE_COLUMNS = ["Bid Deadline", "Start Time", "End Time", "Published DateTime"]
NUMERIC_COLUMNS = [
    "Volume Required",
    "Cleared Volume",
    "Clearing Price",
    "VWA Price",
    "Best Price",
    "Total Bid Volume",
    "VKL Volume",
    "EL Volume",
    "IFA1 Volume",
    "Default Price",
    "NEMO Volume",
    "BN Volume",
    "IFA2 Volume",
]


def _end_of_day(value: date) -> datetime:
    return datetime.combine(value, time(23, 59, 59, 999000))


def _last_sunday(year: int, month: int) -> date:
    current = date(year, month, 31)
    return current - timedelta(days=(current.weekday() + 1) % 7)


def _is_bst(timestamp: pd.Timestamp) -> bool:
    if pd.isna(timestamp):
        return False

    year = timestamp.year
    bst_start = pd.Timestamp(
        datetime.combine(_last_sunday(year, 3), time(1, 0)),
        tz="UTC",
    )
    bst_end = pd.Timestamp(
        datetime.combine(_last_sunday(year, 10), time(1, 0)),
        tz="UTC",
    )
    return bool(bst_start <= timestamp < bst_end)


def _format_date_for_sql(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:23] + "Z"


def _build_sql(start_date: date, end_date: date, limit: int) -> str:
    start = _format_date_for_sql(datetime.combine(start_date, time.min))
    end = _format_date_for_sql(_end_of_day(end_date))

    return f"""
        SELECT COUNT(*) OVER () AS "_count", *
        FROM "{RESOURCE_ID}"
        WHERE "Published DateTime" >= '{start}'
          AND "Published DateTime" <= '{end}'
        ORDER BY "Published DateTime" DESC
        LIMIT {limit}
    """


@st.cache_data(ttl=300, show_spinner=False)
def fetch_data(start_date: date, end_date: date, limit: int) -> pd.DataFrame:
    sql = _build_sql(start_date, end_date, limit)
    response = requests.get(API_URL, params={"sql": sql}, timeout=30)
    response.raise_for_status()

    payload = response.json()
    if not payload.get("success"):
        message = payload.get("error", {}).get("message", "NESO API returned an error.")
        raise RuntimeError(message)

    records = payload.get("result", {}).get("records", [])
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return frame

    for column in DATE_COLUMNS:
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True)

    for column in NUMERIC_COLUMNS:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame["IsBST"] = frame["Start Time"].apply(_is_bst)
    frame["Adjusted Start Time"] = frame["Start Time"] + pd.to_timedelta(
        frame["IsBST"].astype(int),
        unit="h",
    )

    adjusted = frame["Adjusted Start Time"]
    settlement_number = ((adjusted.dt.hour * 60 + adjusted.dt.minute) // 30) + 1
    frame["Settlement Period"] = settlement_number.apply(
        lambda value: f"SP {int(value):02d}" if pd.notna(value) else None
    )

    available_columns = [column for column in COLUMN_ORDER if column in frame.columns]
    remaining_columns = [column for column in frame.columns if column not in available_columns]
    frame = frame[available_columns + remaining_columns]

    if "Start Time" in frame.columns:
        frame = frame.sort_values("Start Time", ascending=False)

    return frame.reset_index(drop=True)


def filter_frame(
    frame: pd.DataFrame,
    buy_sell_values: list[str],
    settlement_periods: list[str],
) -> pd.DataFrame:
    filtered = frame.copy()

    if buy_sell_values and "Buy Sell" in filtered.columns:
        filtered = filtered[filtered["Buy Sell"].isin(buy_sell_values)]

    if settlement_periods and "Settlement Period" in filtered.columns:
        filtered = filtered[filtered["Settlement Period"].isin(settlement_periods)]

    return filtered


def latest_published_timestamp(frame: pd.DataFrame) -> pd.Timestamp | None:
    if "Published DateTime" not in frame.columns or frame.empty:
        return None
    latest = frame["Published DateTime"].max()
    return None if pd.isna(latest) else latest


def new_data_rows(frame: pd.DataFrame, previous_timestamp: pd.Timestamp | None) -> pd.DataFrame:
    if previous_timestamp is None or "Published DateTime" not in frame.columns:
        return pd.DataFrame()
    return frame[frame["Published DateTime"] > previous_timestamp].copy()


def alert_summary_rows(frame: pd.DataFrame) -> list[dict[str, object]]:
    summary_rows = []
    for _, row in frame.iterrows():
        active_ics = []
        for ic_name, column in IC_VOLUME_COLUMNS.items():
            volume = row.get(column)
            if pd.notna(volume) and float(volume) != 0:
                active_ics.append(ic_name)

        if not active_ics and pd.notna(row.get("Qualified IC")):
            active_ics.append(str(row["Qualified IC"]))

        if not active_ics:
            active_ics.append("Unknown")

        summary_rows.append(
            {
                "Settlement Period": row.get("Settlement Period"),
                "Buy/Sell": row.get("Buy Sell"),
                "IC": ", ".join(active_ics),
                "Total Volume": row.get("Cleared Volume"),
                "VWA Price": row.get("VWA Price"),
                "System Flag": row.get("System Flag") if pd.notna(row.get("System Flag")) else "Unknown",
            }
        )

    return summary_rows


def visible_columns(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.drop(columns=[column for column in HIDDEN_COLUMNS if column in frame.columns])


def weighted_average(values: pd.Series, weights: pd.Series) -> float | None:
    clean_values = pd.to_numeric(values, errors="coerce")
    clean_weights = pd.to_numeric(weights, errors="coerce").fillna(0)
    valid = clean_values.notna() & clean_weights.gt(0)
    if not valid.any():
        return None
    return float((clean_values[valid] * clean_weights[valid]).sum() / clean_weights[valid].sum())


def current_day_chart_source(frame: pd.DataFrame) -> pd.DataFrame:
    required_columns = {
        "Adjusted Start Time",
        "Settlement Period",
        "Buy Sell",
        "VWA Price",
        *IC_VOLUME_COLUMNS.values(),
    }
    if frame.empty or not required_columns.issubset(frame.columns):
        return pd.DataFrame()

    today = pd.Timestamp.now(tz=LOCAL_TIMEZONE).date()
    current_day = frame[frame["Adjusted Start Time"].dt.date == today]
    current_day = current_day.dropna(subset=["Settlement Period", "Buy Sell"])
    if current_day.empty:
        return current_day
    if "System Flag" not in current_day.columns:
        current_day = current_day.assign(**{"System Flag": "Unknown"})
    current_day["System Flag"] = current_day["System Flag"].fillna("Unknown").astype(str)

    chart_source = current_day.melt(
        id_vars=["Settlement Period", "Buy Sell", "VWA Price", "System Flag"],
        value_vars=list(IC_VOLUME_COLUMNS.values()),
        var_name="IC",
        value_name="Volume",
    )
    chart_source["IC"] = chart_source["IC"].replace(
        {column: ic_name for ic_name, column in IC_VOLUME_COLUMNS.items()}
    )
    chart_source["Volume"] = pd.to_numeric(chart_source["Volume"], errors="coerce").fillna(0)
    chart_source = chart_source[chart_source["Volume"] != 0]

    return (
        chart_source.groupby(["Settlement Period", "Buy Sell", "IC", "System Flag"], as_index=False)
        .apply(
            lambda group: pd.Series(
                {
                    "Volume": group["Volume"].sum(),
                    "VWA Price": weighted_average(group["VWA Price"], group["Volume"]),
                }
            ),
            include_groups=False,
        )
        .sort_values(["Settlement Period", "Buy Sell", "IC", "System Flag"])
    )


def current_day_volume_chart(chart_source: pd.DataFrame) -> alt.Chart:
    return (
        alt.Chart(chart_source)
        .mark_bar()
        .encode(
            x=alt.X("Settlement Period:N", title="Settlement Period", sort=None),
            xOffset=alt.XOffset("Buy Sell:N", sort=["Sell", "Buy"]),
            y=alt.Y("Volume:Q", title="Volume", stack="zero"),
            color=alt.Color(
                "IC:N",
                title="IC",
                scale=alt.Scale(
                    domain=IC_ORDER,
                    range=["#1F77B4", "#FF7F0E", "#2CA02C", "#D62728", "#9467BD", "#8C564B"],
                ),
            ),
            order=alt.Order("IC:N", sort="ascending"),
            tooltip=[
                alt.Tooltip("Settlement Period:N"),
                alt.Tooltip("Buy Sell:N", title="Buy/Sell"),
                alt.Tooltip("IC:N"),
                alt.Tooltip("Volume:Q", format=",.0f"),
                alt.Tooltip("VWA Price:Q", format=",.2f"),
                alt.Tooltip("System Flag:N"),
            ],
        )
        .properties(height=300)
    )


st.set_page_config(page_title="NESO BSAD Requirements", layout="wide")


@st.dialog("New NESO Data")
def show_new_data_popup(latest_timestamp_text: str, summary_rows: list[dict[str, object]]) -> None:
    st.write("New data has been received from NESO.")
    st.write(f"Latest published time: {latest_timestamp_text}")
    if summary_rows:
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)


def play_alert_sound() -> None:
    components.html(
        """
        <script>
        (async () => {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (!AudioContext) {
                return;
            }

            const context = new AudioContext();
            if (context.state === "suspended") {
                await context.resume();
            }

            const oscillator = context.createOscillator();
            const gain = context.createGain();

            oscillator.type = "sine";
            oscillator.frequency.setValueAtTime(880, context.currentTime);
            oscillator.frequency.setValueAtTime(660, context.currentTime + 0.18);

            gain.gain.setValueAtTime(0.001, context.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.25, context.currentTime + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.7);

            oscillator.connect(gain);
            gain.connect(context.destination);
            oscillator.start(context.currentTime);
            oscillator.stop(context.currentTime + 0.75);
        })().catch(() => {});
        </script>
        """,
        height=0,
    )


refresh_count = st_autorefresh(
    interval=DEFAULT_REFRESH_SECONDS * 1000,
    key="neso_data_refresh",
)
if refresh_count:
    fetch_data.clear()

st.title("NESO BSAD Requirements")

with st.sidebar:
    st.header("Filters")
    selected_dates = st.date_input(
        "Published date range",
        value=(DEFAULT_START_DATE, DEFAULT_END_DATE),
        min_value=date(2020, 1, 1),
        max_value=date(2035, 12, 31),
    )
    limit = st.number_input("API row limit", min_value=10, max_value=10000, value=100, step=50)

    if st.button("Refresh data", use_container_width=True):
        fetch_data.clear()
    st.caption(f"Auto-refreshing every {DEFAULT_REFRESH_SECONDS} seconds.")

if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
    start_date, end_date = selected_dates
else:
    start_date = end_date = selected_dates

if start_date > end_date:
    st.error("Start date must be before end date.")
    st.stop()

try:
    with st.spinner("Loading NESO data..."):
        data = fetch_data(start_date, end_date, int(limit))
except Exception as exc:
    st.error(f"Could not load NESO data: {exc}")
    st.stop()

latest_timestamp = latest_published_timestamp(data)
previous_timestamp = st.session_state.get("latest_published_timestamp")
if latest_timestamp is not None:
    if previous_timestamp is not None and latest_timestamp > previous_timestamp:
        latest_timestamp_text = latest_timestamp.strftime("%Y-%m-%d %H:%M:%S %Z")
        alert_rows = alert_summary_rows(new_data_rows(data, previous_timestamp))
        st.toast("New NESO data received.")
        play_alert_sound()
        show_new_data_popup(latest_timestamp_text, alert_rows)
    st.session_state["latest_published_timestamp"] = latest_timestamp

with st.sidebar:
    buy_sell_options = sorted(data["Buy Sell"].dropna().unique()) if "Buy Sell" in data else []
    buy_sell_values = st.multiselect("Buy/Sell", buy_sell_options, default=buy_sell_options)

    sp_options = (
        sorted(data["Settlement Period"].dropna().unique())
        if "Settlement Period" in data
        else []
    )
    settlement_periods = st.multiselect(
        "Settlement period",
        sp_options,
        default=sp_options,
    )

filtered_data = filter_frame(data, buy_sell_values, settlement_periods)
display_data = visible_columns(filtered_data)

chart_source = current_day_chart_source(filtered_data)
if not chart_source.empty:
    st.altair_chart(current_day_volume_chart(chart_source), use_container_width=True)
else:
    st.info("No current day data available for the chart.")

st.dataframe(display_data, use_container_width=True, hide_index=True)

csv = display_data.to_csv(index=False).encode("utf-8")
st.download_button(
    "Download CSV",
    data=csv,
    file_name="neso_bsad_requirements.csv",
    mime="text/csv",
)
