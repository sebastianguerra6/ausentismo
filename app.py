"""Absenteeism Hours Tracker.

A Streamlit app with two tools:
  1. Manual Entry: a user enters approved and disapproved hours; the app computes
     metrics and stores each record in a SQL (SQLite) database.
  2. Weekly Team Report: pulls last week's Attendance from SQL Server
     ([Bogota_GBS_NS].[dbo].[vwAttendance] + Holidays), computes approved /
     not-approved hours by Unit & SubUnit, lets the user adjust the approved
     values, and saves it as the team's weekly report.
"""

from datetime import date

import pandas as pd
import streamlit as st

import database as db

# Maps the SQL Server query columns to the weekly_team_report table columns.
WEEKLY_COLUMN_MAP = {
    "Unit": "unit",
    "SubUnit": "subunit",
    "SubUnit_Description": "subunit_description",
    "Location": "location",
    "Business_Days": "business_days",
    "People_In_Unit": "people_in_unit",
    "Attended_Count": "attended_count",
    "Attended_Hours": "attended_hours",
    "Approved_Count": "approved_count",
    "Approved_Hours": "approved_hours",
    "Approved_Semana": "approved_semana",
    "Not_Approved_Count": "not_approved_count",
    "Not_Approved_Hours": "not_approved_hours",
    "Not_Approved_Semana": "not_approved_semana",
}

HOURS_PER_DAY = 8

SCOTIA_RED = "#EC111A"

BRAND_CSS = f"""
<style>
    /* Scotiabank red top bar */
    .scotia-header {{
        background-color: {SCOTIA_RED};
        padding: 18px 24px;
        border-radius: 8px;
        margin-bottom: 18px;
    }}
    .scotia-header h1 {{
        color: #FFFFFF;
        margin: 0;
        font-size: 1.6rem;
        font-weight: 700;
    }}
    .scotia-header p {{
        color: #FFFFFF;
        margin: 4px 0 0 0;
        font-size: 0.9rem;
        opacity: 0.95;
    }}
    /* Buttons in Scotiabank red */
    div.stButton > button, div.stFormSubmitButton > button {{
        background-color: {SCOTIA_RED};
        color: #FFFFFF;
        border: none;
        font-weight: 600;
    }}
    div.stButton > button:hover, div.stFormSubmitButton > button:hover {{
        background-color: #C40E15;
        color: #FFFFFF;
    }}
    /* Section divider accent */
    hr {{
        border-color: {SCOTIA_RED};
    }}
</style>
"""


def compute_metrics(approved: float, disapproved: float) -> dict:
    """Return the derived metrics for a pair of hour inputs."""
    total = approved + disapproved
    approval_rate = (approved / total * 100) if total > 0 else 0.0
    absenteeism_rate = (disapproved / total * 100) if total > 0 else 0.0
    return {
        "total_hours": round(total, 2),
        "approval_rate": round(approval_rate, 2),
        "absenteeism_rate": round(absenteeism_rate, 2),
    }


def render_manual_entry() -> None:
    """Tab 1: a single user enters approved / disapproved hours."""
    with st.form("hours_form", clear_on_submit=False):
        employee_name = st.text_input("Employee name", placeholder="e.g. John Doe")
        record_date = st.date_input("Date", value=date.today())

        col1, col2 = st.columns(2)
        with col1:
            approved_hours = st.number_input(
                "Approved hours",
                min_value=0.0,
                step=0.5,
                format="%.2f",
            )
        with col2:
            disapproved_hours = st.number_input(
                "Disapproved hours",
                min_value=0.0,
                step=0.5,
                format="%.2f",
            )

        submitted = st.form_submit_button("Calculate & Save")

    if submitted:
        if not employee_name.strip():
            st.error("Please enter the employee name.")
        elif approved_hours == 0 and disapproved_hours == 0:
            st.error("Please enter at least some hours before saving.")
        else:
            metrics = compute_metrics(approved_hours, disapproved_hours)

            m1, m2, m3 = st.columns(3)
            m1.metric("Total hours", f"{metrics['total_hours']:.2f}")
            m2.metric("Approval rate", f"{metrics['approval_rate']:.2f}%")
            m3.metric("Absenteeism rate", f"{metrics['absenteeism_rate']:.2f}%")

            new_id = db.insert_record(
                employee_name=employee_name.strip(),
                record_date=record_date,
                approved_hours=approved_hours,
                disapproved_hours=disapproved_hours,
                total_hours=metrics["total_hours"],
                approval_rate=metrics["approval_rate"],
                absenteeism_rate=metrics["absenteeism_rate"],
            )
            st.success(f"Record saved to the database (ID #{new_id}).")

    st.divider()
    st.subheader("Saved records")

    rows = db.fetch_records()
    if rows:
        df = pd.DataFrame([dict(row) for row in rows])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No records yet. Submit the form above to create the first one.")


def recompute_hours(df: pd.DataFrame, business_days) -> pd.DataFrame:
    """Recompute counts and weekly-normalized values from the (possibly edited)
    Approved / Not-Approved hours. Keeps the SQL logic: 1 day = 8 hours and
    *_Semana = count / business_days, rounded to 1 decimal.
    """
    out = df.copy()
    try:
        days = float(business_days) if business_days else 0.0
    except (TypeError, ValueError):
        days = 0.0

    for hours_col, count_col, semana_col in (
        ("Approved_Hours", "Approved_Count", "Approved_Semana"),
        ("Not_Approved_Hours", "Not_Approved_Count", "Not_Approved_Semana"),
    ):
        if hours_col in out.columns:
            hours = pd.to_numeric(out[hours_col], errors="coerce").fillna(0)
            counts = hours / HOURS_PER_DAY
            if count_col in out.columns:
                out[count_col] = counts
            if semana_col in out.columns:
                out[semana_col] = (counts / days).round(1) if days > 0 else 0.0
    return out


def render_weekly_team_report() -> None:
    """Tab 2: pull last week's Attendance for the signed-in leader's team, let the
    leader enter approved-leave / unplanned hours, and save the weekly report.
    """
    import sqlserver

    st.subheader("Weekly Team Report")
    st.caption(
        "Detects your Windows user, pulls last week's Attendance for your team "
        "(Mon-Fri, Colombian holidays excluded) from SQL Server, and lets you enter "
        "Approved Leave / Unplanned hours by Unit, SubUnit and location."
    )

    windows_user = sqlserver.current_windows_user()
    st.info(f"Signed in as (Windows user): **{windows_user}**")

    server = st.text_input(
        "SQL Server host",
        value=st.session_state.get("sqlserver_host", ""),
        placeholder="e.g. SQLPRD01 or SQLPRD01\\INSTANCE",
        help="Windows Authentication is used. Leave empty to use the configured default.",
    )
    if server:
        st.session_state["sqlserver_host"] = server

    with st.expander("Team & schema setup", expanded=not st.session_state.get("att_columns")):
        st.caption(
            "Map the vwAttendance columns once. 'Discover columns' lists every "
            "column so you can pick the manager and location ones."
        )
        if st.button("Discover vwAttendance columns"):
            try:
                with st.spinner("Reading vwAttendance schema ..."):
                    st.session_state["att_columns"] = sqlserver.list_view_columns(server=server or None)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not read the schema: {exc}")

        columns = st.session_state.get("att_columns", [])
        options = ["(none)"] + columns
        manager_col = st.selectbox(
            "Manager / leader column (used to filter your team)",
            options=options,
            index=options.index(st.session_state["map_manager_col"])
            if st.session_state.get("map_manager_col") in options
            else 0,
            help="The column in vwAttendance that stores each person's manager identifier.",
        )
        location_col = st.selectbox(
            "Location column (optional)",
            options=options,
            index=options.index(st.session_state["map_location_col"])
            if st.session_state.get("map_location_col") in options
            else 0,
        )
        leader_value = st.text_input(
            "My leader identifier value",
            value=st.session_state.get("leader_value", windows_user),
            help="Value to match in the manager column. Defaults to your Windows user.",
        )
        st.session_state["map_manager_col"] = manager_col
        st.session_state["map_location_col"] = location_col
        st.session_state["leader_value"] = leader_value

    manager_col = st.session_state.get("map_manager_col", "(none)")
    location_col = st.session_state.get("map_location_col", "(none)")
    leader_value = st.session_state.get("leader_value", windows_user)
    use_manager = manager_col not in (None, "(none)", "")
    use_location = location_col not in (None, "(none)", "")

    if st.button("Load last week's data from SQL Server"):
        try:
            with st.spinner("Querying [Bogota_GBS_NS] ..."):
                df = sqlserver.fetch_weekly_team_report(
                    server=server or None,
                    manager_column=manager_col if use_manager else None,
                    manager_value=leader_value if use_manager else None,
                    location_column=location_col if use_location else None,
                )
            if df.empty:
                st.warning("The query returned no rows for last week / your team.")
            st.session_state["weekly_df"] = df
        except Exception as exc:  # noqa: BLE001 - surface any connection/query error
            st.error(f"Could not load data from SQL Server: {exc}")

    df = st.session_state.get("weekly_df")
    if df is None or df.empty:
        st.info("Set up the mapping and load the data to build the weekly report.")
    else:
        week_monday = str(df["Lunes_Semana"].iloc[0]) if "Lunes_Semana" in df else ""
        business_days = df["Business_Days"].iloc[0] if "Business_Days" in df else None
        headcount = int(df["People_In_Unit"].sum()) if "People_In_Unit" in df else 0
        n_locations = df["Location"].nunique() if "Location" in df else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Week (Monday)", week_monday or "-")
        c2.metric("Business days", f"{business_days:g}" if business_days is not None else "-")
        c3.metric("People in my team", headcount)
        c4.metric("Locations", n_locations if use_location else "-")

        st.caption(
            "Enter the **Approved Leave** and **Unplanned** hours per row. Counts and "
            "weekly values recalculate automatically (1 day = 8 hours)."
        )
        editable = df.drop(columns=["Lunes_Semana"], errors="ignore")
        edited = st.data_editor(
            editable,
            use_container_width=True,
            hide_index=True,
            disabled=[c for c in editable.columns if c not in ("Approved_Hours", "Not_Approved_Hours")],
            column_config={
                "Approved_Hours": st.column_config.NumberColumn("Approved Leave (hours)", min_value=0, step=8),
                "Not_Approved_Hours": st.column_config.NumberColumn("Unplanned (hours)", min_value=0, step=8),
            },
            key="weekly_editor",
        )

        edited = recompute_hours(edited, business_days)
        st.caption("Preview with recalculated counts and weekly values:")
        st.dataframe(edited, use_container_width=True, hide_index=True)

        if st.button("Save weekly report"):
            records = []
            for _, row in edited.iterrows():
                records.append({db_col: row.get(sql_col) for sql_col, db_col in WEEKLY_COLUMN_MAP.items()})
            count = db.save_weekly_report(
                week_monday=week_monday,
                rows=records,
                leader=leader_value if use_manager else windows_user,
            )
            st.success(f"Weekly report for {week_monday} saved ({count} rows).")

    st.divider()
    st.subheader("Saved weekly reports")
    saved = db.fetch_weekly_reports()
    if saved:
        st.dataframe(
            pd.DataFrame([dict(r) for r in saved]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No weekly reports saved yet.")


def main() -> None:
    st.set_page_config(page_title="Absenteeism Hours Tracker", page_icon="⏱️", layout="wide")
    db.init_db()

    st.markdown(BRAND_CSS, unsafe_allow_html=True)
    st.markdown(
        """
        <div class="scotia-header">
            <h1>Scotiabank | Absenteeism Hours Tracker</h1>
            <p>Track approved and disapproved hours and build the weekly team report.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    manual_tab, weekly_tab = st.tabs(["Manual Entry", "Weekly Team Report"])
    with manual_tab:
        render_manual_entry()
    with weekly_tab:
        render_weekly_team_report()


if __name__ == "__main__":
    main()
