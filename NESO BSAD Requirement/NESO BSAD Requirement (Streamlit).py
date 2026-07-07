from __future__ import annotations

from datetime import date, datetime, time

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
DEFAULT_ROW_LIMIT = 1000
LOCAL_TIMEZONE = "Europe/London"

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

PRICE_COLUMNS = ["VWA Price", "Clearing Price"]

INTERCONNECTOR_VOLUME_COLUMNS = {
    "VKL Volume": "VKL",
    "EL Volume": "EL",
    "IFA1 Volume": "IFA1",
    "NEMO Volume": "NEMO",
    "BN Volume": "BN",
    "IFA2 Volume": "IFA2",
}


def _end_of_day(value: date) -> datetime:
    return datetime.combine(value, time(23, 59, 59, 999000))


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


def _is_bst(timestamp: pd.Timestamp) -> bool:
    if pd.isna(timestamp):
        return False
    return bool(timestamp.dst() and timestamp.dst().total_seconds() > 0)


def _format_local_timestamp(timestamp: pd.Timestamp) -> str:
    if pd.isna(timestamp):
        return ""
    return timestamp.tz_convert(LOCAL_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S %Z")


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

    if "Start Time" not in frame.columns:
        return frame.reset_index(drop=True)

    frame["Adjusted Start Time"] = frame["Start Time"].dt.tz_convert(LOCAL_TIMEZONE)
    frame["IsBST"] = frame["Adjusted Start Time"].apply(_is_bst)

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


def latest_records(frame: pd.DataFrame, latest_timestamp: pd.Timestamp) -> pd.DataFrame:
    if "Published DateTime" not in frame.columns or frame.empty:
        return pd.DataFrame()
    return frame[frame["Published DateTime"] == latest_timestamp]


def volume_price_confirmation(records: pd.DataFrame) -> tuple[bool, str, pd.DataFrame]:
    empty_summary = pd.DataFrame(
        columns=["Settlement Period", "Buy Sell", "Cleared Volume", "VWAP"]
    )

    required_columns = {"Settlement Period", "Cleared Volume"}
    if records.empty or not required_columns.issubset(records.columns):
        return False, "Volume and price are not confirmed yet.", empty_summary

    price_column = next(
        (
            column
            for column in PRICE_COLUMNS
            if column in records.columns and records[column].notna().any()
        ),
        None,
    )
    if price_column is None:
        return False, "Volume and price are not confirmed yet.", empty_summary

    confirmed_rows = records["Cleared Volume"].notna() & records[price_column].notna()
    if not confirmed_rows.all():
        return False, "Volume and price are not confirmed yet.", empty_summary

    summary_columns = ["Settlement Period", "Cleared Volume", price_column]
    if "Buy Sell" in records.columns:
        summary_columns.insert(1, "Buy Sell")

    summary_source = records[summary_columns].copy()
    if "Buy Sell" not in summary_source.columns:
        summary_source["Buy Sell"] = "Unknown"

    summary_source["Cleared Volume"] = pd.to_numeric(
        summary_source["Cleared Volume"],
        errors="coerce",
    )
    summary_source[price_column] = pd.to_numeric(summary_source[price_column], errors="coerce")
    summary_source = summary_source.dropna(
        subset=["Settlement Period", "Cleared Volume", price_column]
    )

    positive_volume = summary_source["Cleared Volume"] > 0
    weighted_source = summary_source[positive_volume].copy()
    group_columns = ["Settlement Period", "Buy Sell"]
    if weighted_source.empty:
        summary = (
            summary_source.groupby(group_columns, as_index=False)
            .agg(**{"Cleared Volume": ("Cleared Volume", "sum"), "VWAP": (price_column, "mean")})
        )
    else:
        weighted_source["Weighted Price"] = (
            weighted_source["Cleared Volume"] * weighted_source[price_column]
        )
        summary = weighted_source.groupby(group_columns, as_index=False).agg(
            **{
                "Cleared Volume": ("Cleared Volume", "sum"),
                "Weighted Price": ("Weighted Price", "sum"),
            }
        )
        summary["VWAP"] = summary["Weighted Price"] / summary["Cleared Volume"]
        summary = summary[group_columns + ["Cleared Volume", "VWAP"]]

    summary = summary.sort_values(["Settlement Period", "Buy Sell"]).reset_index(drop=True)
    return True, "Volume and price are confirmed by settlement period and direction.", summary


def interconnector_procurement_message(records: pd.DataFrame) -> str:
    if records.empty:
        return "No interconnector procurement has been confirmed yet."

    available_columns = [
        column for column in INTERCONNECTOR_VOLUME_COLUMNS if column in records.columns
    ]
    if not available_columns:
        return "No interconnector procurement has been confirmed yet."

    direction_source = records.copy()
    if "Buy Sell" not in direction_source.columns:
        direction_source["Buy Sell"] = "Unknown"

    interconnector_totals = {}
    for column in available_columns:
        direction_source[column] = pd.to_numeric(
            direction_source[column],
            errors="coerce",
        ).fillna(0)
        grouped_volumes = direction_source.groupby("Buy Sell")[column].sum()
        for direction, volume in grouped_volumes.items():
            if volume != 0:
                interconnector_totals.setdefault(direction, {})[
                    INTERCONNECTOR_VOLUME_COLUMNS[column]
                ] = volume

    if not interconnector_totals:
        return "No interconnector procurement has been confirmed yet."

    direction_details = []
    for direction in sorted(interconnector_totals):
        interconnector_details = ", ".join(
            f"{interconnector}: {volume:,.0f}"
            for interconnector, volume in interconnector_totals[direction].items()
        )
        direction_details.append(f"{direction} - {interconnector_details}")

    return (
        "Procurement by direction and interconnector: "
        + "; ".join(direction_details)
        + "."
    )


def visible_columns(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.drop(columns=[column for column in HIDDEN_COLUMNS if column in frame.columns])


def _current_day_vwap_source(current_day: pd.DataFrame) -> pd.DataFrame:
    group_columns = ["Settlement Period"]
    if "VWA Price" not in current_day.columns:
        return current_day[group_columns].drop_duplicates().assign(VWAP=pd.NA)

    price_source = current_day.dropna(subset=["VWA Price"]).copy()
    if price_source.empty:
        return current_day[group_columns].drop_duplicates().assign(VWAP=pd.NA)

    positive_volume = price_source["Cleared Volume"].fillna(0) > 0
    weighted_source = price_source[positive_volume].copy()

    if weighted_source.empty:
        return (
            price_source.groupby(group_columns, as_index=False)["VWA Price"]
            .mean()
            .rename(columns={"VWA Price": "VWAP"})
        )

    weighted_source["Weighted VWA Price"] = (
        weighted_source["VWA Price"] * weighted_source["Cleared Volume"]
    )
    weighted_totals = weighted_source.groupby(group_columns, as_index=False).agg(
        **{
            "Weighted VWA Price": ("Weighted VWA Price", "sum"),
            "VWAP Weight": ("Cleared Volume", "sum"),
        }
    )
    weighted_totals["VWAP"] = (
        weighted_totals["Weighted VWA Price"] / weighted_totals["VWAP Weight"]
    )

    return weighted_totals[group_columns + ["VWAP"]]


def current_day_chart_source(frame: pd.DataFrame) -> pd.DataFrame:
    required_columns = {"Adjusted Start Time", "Settlement Period", "Buy Sell", "Cleared Volume"}
    if frame.empty or not required_columns.issubset(frame.columns):
        return pd.DataFrame()

    today = pd.Timestamp.now(tz=LOCAL_TIMEZONE).date()
    current_day = frame[frame["Adjusted Start Time"].dt.date == today]
    current_day = current_day.dropna(subset=["Settlement Period", "Buy Sell", "Cleared Volume"])
    if current_day.empty:
        return current_day

    bar_group_columns = ["Settlement Period", "Buy Sell"]
    settlement_period_group_columns = ["Settlement Period"]
    interconnector_columns = [
        column for column in INTERCONNECTOR_VOLUME_COLUMNS if column in current_day.columns
    ]
    if not interconnector_columns:
        return pd.DataFrame()

    vwap_source = _current_day_vwap_source(current_day)
    total_volume = (
        current_day.groupby(settlement_period_group_columns, as_index=False)["Cleared Volume"]
        .sum()
        .rename(columns={"Cleared Volume": "SP Cleared Volume"})
    )

    chart_source = current_day.melt(
        id_vars=bar_group_columns,
        value_vars=interconnector_columns,
        var_name="Interconnector Column",
        value_name="Interconnector Volume",
    )
    chart_source["Interconnector"] = chart_source["Interconnector Column"].map(
        INTERCONNECTOR_VOLUME_COLUMNS
    )
    chart_source["Interconnector Volume"] = pd.to_numeric(
        chart_source["Interconnector Volume"],
        errors="coerce",
    ).fillna(0)
    chart_source = chart_source[chart_source["Interconnector Volume"] != 0]
    if chart_source.empty:
        return chart_source

    return (
        chart_source.groupby(bar_group_columns + ["Interconnector"], as_index=False)[
            "Interconnector Volume"
        ]
        .sum()
        .merge(total_volume, on=settlement_period_group_columns, how="left")
        .merge(vwap_source, on=settlement_period_group_columns, how="left")
        .sort_values(["Settlement Period", "Buy Sell", "Interconnector"])
    )


def current_day_volume_chart(chart_source: pd.DataFrame) -> alt.Chart:
    return (
        alt.Chart(chart_source)
        .mark_bar()
        .encode(
            x=alt.X("Settlement Period:N", title="Settlement Period", sort=None),
            xOffset=alt.XOffset("Buy Sell:N", sort=["Sell", "Buy"]),
            y=alt.Y(
                "Interconnector Volume:Q",
                title="Cleared Volume",
                stack="zero",
            ),
            color=alt.Color(
                "Interconnector:N",
                title="Interconnector",
                scale=alt.Scale(
                    domain=["VKL", "EL", "IFA1", "NEMO", "BN", "IFA2"],
                    range=["#4269D0", "#EF7C8E", "#59A14F", "#F28E2B", "#8CD17D", "#B07AA1"],
                ),
            ),
            tooltip=[
                alt.Tooltip("Settlement Period:N"),
                alt.Tooltip("Buy Sell:N", title="Buy/Sell"),
                alt.Tooltip("Interconnector:N"),
                alt.Tooltip("Interconnector Volume:Q", title="IC Volume", format=",.0f"),
                alt.Tooltip("SP Cleared Volume:Q", title="SP Cleared Volume", format=",.0f"),
                alt.Tooltip("VWAP:Q", title="SP VWAP", format=",.2f"),
            ],
        )
        .properties(height=300)
    )


st.set_page_config(page_title="NESO BSAD Requirements", layout="wide")


@st.dialog("New NESO Data")
def show_new_data_popup(
    latest_timestamp_text: str,
    confirmation_message: str,
    settlement_period_summary: pd.DataFrame,
    interconnector_message: str,
) -> None:
    st.write("New data has been received from NESO (Requirement for BSADs).")
    st.write(confirmation_message)
    if not settlement_period_summary.empty:
        st.dataframe(
            settlement_period_summary,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Cleared Volume": st.column_config.NumberColumn(
                    "Cleared Volume",
                    format="%.0f",
                ),
                "VWAP": st.column_config.NumberColumn("VWAP", format="%.2f"),
            },
        )
    st.write(interconnector_message)
    st.write(f"Latest published time: {latest_timestamp_text}")


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
    limit = st.number_input(
        "API row limit",
        min_value=10,
        max_value=10000,
        value=DEFAULT_ROW_LIMIT,
        step=50,
    )
    popup_alert_enabled = st.checkbox("Show popup alert", value=True)
    alert_sound_enabled = st.checkbox("Play alert sound", value=False)

    if st.button("Refresh data", use_container_width=True):
        fetch_data.clear()
    st.caption(f"Auto-refreshing every {DEFAULT_REFRESH_SECONDS} seconds.")

if isinstance(selected_dates, tuple):
    if len(selected_dates) != 2:
        st.info("Select a start and end published date.")
        st.stop()
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
        latest_timestamp_text = _format_local_timestamp(latest_timestamp)
        new_records = latest_records(data, latest_timestamp)
        _, confirmation_message, settlement_period_summary = volume_price_confirmation(
            new_records
        )
        interconnector_message = interconnector_procurement_message(new_records)
        st.toast("New NESO data received.")
        if alert_sound_enabled:
            play_alert_sound()
        if popup_alert_enabled:
            show_new_data_popup(
                latest_timestamp_text,
                confirmation_message,
                settlement_period_summary,
                interconnector_message,
            )
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
