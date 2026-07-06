"""Absenteeism & Work From Office (WFO) tracker.

Two manual-entry modules whose data is stored in SQL Server (Trusted Connection):
  1. Absenteeism: Vicepresident, Unit, People in unit, planned / unplanned
     approved leave and days affected, plus a comment. Computes the unplanned
     percentage of the week.
  2. Work From Office: expected vs actual attendance and a comment. Computes the
     attendance percentage.

Writing is gated by Active Directory group membership, checked via SQL Server's
IS_MEMBER over the trusted connection.
"""

from datetime import date

import pandas as pd
import streamlit as st

import sqlserver

ABSENTEEISM_COMMENTS = ["Vacation", "Medical Leave", "Personal Day", "Training", "Other"]
WFO_COMMENTS = ["On site", "Remote - approved", "Transport issue", "Health", "Other"]

# Example values only - replace with the real ones once confirmed.
VICEPRESIDENT_OPTIONS = [
    "VP Operations",
    "VP Compliance",
    "VP Technology",
    "VP Financial Crimes",
    "Other",
]
UNIT_OPTIONS = [
    "EDDU Regular",
    "ADT",
    "BI & Automations",
    "NS Bog",
    "Swat",
    "Triage",
    "Payment",
    "MST",
    "Others",
    "Other",
]

SCOTIA_RED = "#EC111A"

BRAND_CSS = f"""
<style>
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
    hr {{
        border-color: {SCOTIA_RED};
    }}
</style>
"""


def unplanned_percentage(planned_days: int, unplanned_days: int) -> tuple[int, float]:
    """Return (total_days_affected, unplanned_pct).

    total_days_affected = planned + unplanned
    unplanned_pct = unplanned / total * 100 (0 when there are no affected days).
    """
    total = (planned_days or 0) + (unplanned_days or 0)
    pct = round((unplanned_days or 0) / total * 100, 1) if total > 0 else 0.0
    return total, pct


def attendance_percentage(expected: int, actual: int) -> float:
    """Return attendance_pct = actual / expected * 100 (0 when expected is 0)."""
    return round((actual or 0) / expected * 100, 1) if expected and expected > 0 else 0.0


def render_absenteeism(server: str | None, can_write: bool, created_by: str) -> None:
    st.subheader("Absenteeism")
    st.caption(
        "Register planned and unplanned approved leave for a unit. The unplanned "
        "percentage of the week is calculated over the total days affected."
    )

    with st.form("absenteeism_form", clear_on_submit=False):
        record_date = st.date_input("Date", value=date.today())
        vicepresident = st.selectbox("Vicepresident", options=VICEPRESIDENT_OPTIONS)
        unit = st.selectbox("Unit", options=UNIT_OPTIONS)
        people_in_unit = st.number_input("People in unit", min_value=0, step=1)

        st.markdown("**Planned approved leave**")
        p1, p2 = st.columns(2)
        with p1:
            planned_leave = st.number_input("Planned approved leave (people)", min_value=0, step=1, key="planned_leave")
        with p2:
            planned_days = st.number_input("Days affected (planned)", min_value=0, step=1, key="planned_days")

        st.markdown("**Unplanned approved leave**")
        u1, u2 = st.columns(2)
        with u1:
            unplanned_leave = st.number_input("Unplanned approved leave (people)", min_value=0, step=1, key="unplanned_leave")
        with u2:
            unplanned_days = st.number_input("Days affected (unplanned)", min_value=0, step=1, key="unplanned_days")

        comment = st.selectbox("Comment", options=ABSENTEEISM_COMMENTS)

        submitted = st.form_submit_button("Calculate & Save", disabled=not can_write)

    total_days, unplanned_pct = unplanned_percentage(int(planned_days), int(unplanned_days))
    m1, m2 = st.columns(2)
    m1.metric("Total days affected", total_days)
    m2.metric("% Unplanned (week)", f"{unplanned_pct:.1f}%")

    if submitted:
        if not can_write:
            st.error("You do not have permission to write.")
        elif total_days == 0:
            st.error("Please enter at least some days affected before saving.")
        else:
            try:
                sqlserver.insert_absenteeism(
                    record_date=record_date,
                    vicepresident=vicepresident.strip(),
                    unit=unit.strip(),
                    people_in_unit=int(people_in_unit),
                    planned_leave=int(planned_leave),
                    planned_days_affected=int(planned_days),
                    unplanned_leave=int(unplanned_leave),
                    unplanned_days_affected=int(unplanned_days),
                    total_days_affected=total_days,
                    unplanned_pct=unplanned_pct,
                    comment=comment.strip(),
                    created_by=created_by,
                    server=server,
                )
                st.success("Absenteeism record saved to SQL Server.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not save the record: {exc}")

    st.divider()
    st.subheader("Saved absenteeism records")
    _render_history(sqlserver.fetch_absenteeism, server)


def render_wfo(server: str | None, can_write: bool, created_by: str) -> None:
    st.subheader("Work From Office (WFO)")
    st.caption("Register how many people were expected and how many came. Attendance % is calculated.")

    with st.form("wfo_form", clear_on_submit=False):
        record_date = st.date_input("Date", value=date.today(), key="wfo_date")
        c1, c2 = st.columns(2)
        with c1:
            expected = st.number_input("Expected (had to come)", min_value=0, step=1)
        with c2:
            actual = st.number_input("Actual (came)", min_value=0, step=1)
        comment = st.selectbox("Comment", options=WFO_COMMENTS, key="wfo_comment")

        submitted = st.form_submit_button("Calculate & Save", disabled=not can_write)

    attendance_pct = attendance_percentage(int(expected), int(actual))
    st.metric("Attendance %", f"{attendance_pct:.1f}%")

    if submitted:
        if not can_write:
            st.error("You do not have permission to write.")
        elif expected == 0:
            st.error("Please enter the expected number of people before saving.")
        else:
            try:
                sqlserver.insert_wfo(
                    record_date=record_date,
                    expected=int(expected),
                    actual=int(actual),
                    attendance_pct=attendance_pct,
                    comment=comment.strip(),
                    created_by=created_by,
                    server=server,
                )
                st.success("WFO record saved to SQL Server.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not save the record: {exc}")

    st.divider()
    st.subheader("Saved WFO records")
    _render_history(sqlserver.fetch_wfo, server)


def _render_history(fetch_fn, server: str | None) -> None:
    """Show a fetched history table, handling connection errors gracefully."""
    try:
        df = fetch_fn(server=server)
    except Exception as exc:  # noqa: BLE001
        st.info(f"Could not load saved records (check the SQL Server connection): {exc}")
        return
    if df is None or df.empty:
        st.info("No records yet.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="Absenteeism & WFO Tracker", page_icon="⏱️", layout="wide")

    st.markdown(BRAND_CSS, unsafe_allow_html=True)
    st.markdown(
        """
        <div class="scotia-header">
            <h1>Scotiabank | Absenteeism & WFO Tracker</h1>
            <p>Register absenteeism and work-from-office attendance. Data is stored in SQL Server.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    windows_user = sqlserver.current_windows_domain_user()
    server = st.text_input(
        "SQL Server host",
        value=st.session_state.get("sqlserver_host", ""),
        placeholder="e.g. SQLPRD01 or SQLPRD01\\INSTANCE",
        help="Windows Authentication is used. Leave empty to use the configured default.",
    )
    server = server or None
    if server:
        st.session_state["sqlserver_host"] = server

    # Ensure the tables exist and evaluate write permission (AD group membership).
    init_error = None
    try:
        sqlserver.init_db(server=server)
    except Exception as exc:  # noqa: BLE001
        init_error = str(exc)

    can_write = False
    perm_error = None
    write_group = sqlserver.get_write_group()
    try:
        can_write = sqlserver.can_write(server=server)
    except Exception as exc:  # noqa: BLE001
        perm_error = str(exc)

    st.info(f"Signed in as (Windows user): **{windows_user}**")
    if init_error:
        st.warning(f"Could not verify/create the tables in SQL Server: {init_error}")
    if not write_group:
        st.warning(
            "No AD write group is configured (`[auth] write_group` in secrets or "
            "`AD_WRITE_GROUP`). The app is in read-only mode until it is set."
        )
    elif perm_error:
        st.warning(f"Could not check AD group membership: {perm_error}. Read-only mode.")
    elif can_write:
        st.success(f"You are a member of **{write_group}**: you can save records.")
    else:
        st.warning(f"You are not a member of **{write_group}**: read-only mode.")

    absenteeism_tab, wfo_tab = st.tabs(["Absenteeism", "Work From Office"])
    with absenteeism_tab:
        render_absenteeism(server, can_write, windows_user)
    with wfo_tab:
        render_wfo(server, can_write, windows_user)


if __name__ == "__main__":
    main()
