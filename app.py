"""Absenteeism & Work From Office (WFO) tracker.

Two manual-entry modules that both write to a single SQL Server table
(``dbo.Attendance_Absenteeism_Report``) over a Trusted Connection:
  1. Absenteeism: Leader, Location, Team, Total Headcount, planned / unplanned
     approved leave and days impacted, plus comments. Computes the unplanned
     percentage of the week and (when the source base is available) the
     headcount alignment percentage.
  2. Work From Office: whether all required employees attended, how many were
     non-compliant, and up to five comments.

Read/write permissions are enforced by SQL Server for the trusted Windows user
(the account running the app); there is no application-level authorization.
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import sqlserver

# Example values only - replace with the real ones once confirmed.
LOCATION_OPTIONS = [
    "Bogota",
    "Medellin",
    "Cali",
    "Barranquilla",
    "Remote",
    "Other",
]
LEADER_OPTIONS = [
    "VP Operations",
    "VP Compliance",
    "VP Technology",
    "VP Financial Crimes",
    "Other",
]
TEAM_OPTIONS = [
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


def unplanned_percentage(
    total_headcount: int, planned_days: int, unplanned_days: int
) -> tuple[int, float]:
    """Return (total_days_affected, unplanned_pct).

    total_days_affected = planned + unplanned
    unplanned_pct = (Days Affected (Unplanned) / 5) / (Total Headcount - Days Affected (Planned)) * 100
    (Days Impacted are divided by 5 to convert them into person-week equivalents.
    Returns 0 when the denominator is not positive.)
    """
    total = (planned_days or 0) + (unplanned_days or 0)
    denominator = (total_headcount or 0) - (planned_days or 0)
    unplanned_equivalent = (unplanned_days or 0) / 5
    pct = round(unplanned_equivalent / denominator * 100, 1) if denominator > 0 else 0.0
    return total, pct


ABSENCE_REFERENCE = [
    {"Absence reason": "Absent (No present, no Call)", "When to use it?": "Absent without excuse", "Available in me@Scotiabank": "No", "Type of Absence": "Unplanned"},
    {"Absence reason": "Called in Sick", "When to use it?": "Absent with medical excuse but without license", "Available in me@Scotiabank": "Yes", "Type of Absence": "Unplanned"},
    {"Absence reason": "Permission", "When to use it?": "Permission given to the employee", "Available in me@Scotiabank": "No", "Type of Absence": "Unplanned"},
    {"Absence reason": "Special day or personal day", "When to use it?": "Free day given", "Available in me@Scotiabank": "Yes", "Type of Absence": "Unplanned"},
    {"Absence reason": "Medical License", "When to use it?": "License given by a doctor due to illness (less than 6 days)", "Available in me@Scotiabank": "Yes", "Type of Absence": "Unplanned"},
    {"Absence reason": "Bereavement Leave", "When to use it?": "License due to bereavement", "Available in me@Scotiabank": "Yes", "Type of Absence": "Unplanned"},
    {"Absence reason": "BCP", "When to use it?": "Time off due to BCP protocol activated", "Available in me@Scotiabank": "No", "Type of Absence": "Unplanned"},
    {"Absence reason": "Vacation", "When to use it?": "Authorized Vacation: forecast and executed", "Available in me@Scotiabank": "Yes", "Type of Absence": "Planned"},
    {"Absence reason": "Paternity Leave", "When to use it?": "License due to paternity", "Available in me@Scotiabank": "Yes", "Type of Absence": "Planned"},
    {"Absence reason": "Marriage Leave", "When to use it?": "License due to marriage", "Available in me@Scotiabank": "Yes", "Type of Absence": "Planned"},
    {"Absence reason": "Lactation", "When to use it?": "Time off due to lactation", "Available in me@Scotiabank": "No", "Type of Absence": "Planned"},
    {"Absence reason": "Flex Summer", "When to use it?": "Free 2.5 hours on Friday due to flex summer", "Available in me@Scotiabank": "No", "Type of Absence": "Planned"},
    {"Absence reason": "Authorized", "When to use it?": "Planned Absence that does not fit prior reason descriptions", "Available in me@Scotiabank": "No", "Type of Absence": "Planned"},
    {"Absence reason": "Birthday", "When to use it?": "Free day due to birthday", "Available in me@Scotiabank": "Yes", "Type of Absence": "Planned"},
]


def monday_of_week(d: date) -> date:
    """Return the Monday of the week that contains ``d``."""
    return d - timedelta(days=d.weekday())


def can_use_wfo(location: str) -> bool:
    """Whether the current person/location is allowed to report WFO.

    This will be driven by the by-country headcount source (access pending).
    Until then everyone is allowed, so the WFO Save button stays enabled.
    Return False here (per user/location) to disable the WFO Save button.
    """
    return True


def render_identity(prefix: str) -> tuple[date, str, str, str, int]:
    """Render the shared identity fields and return their values.

    Returns (report_date, leader, location, team, total_headcount). The date is
    always the Monday of the reported week.
    """
    report_date = st.date_input(
        "Report date (Monday of the reported week)",
        value=monday_of_week(date.today()),
        key=f"{prefix}_date",
    )
    leader = st.selectbox("Leader", options=LEADER_OPTIONS, key=f"{prefix}_leader")
    location = st.selectbox("Location", options=LOCATION_OPTIONS, key=f"{prefix}_location")
    team = st.selectbox("Team", options=TEAM_OPTIONS, key=f"{prefix}_team")
    total_headcount = st.number_input(
        "Total Headcount", min_value=0, step=1, key=f"{prefix}_headcount"
    )
    return monday_of_week(report_date), leader, location, team, int(total_headcount)


def render_absenteeism(server: str | None) -> None:
    st.subheader("Absenteeism")
    st.caption(
        "Register planned and unplanned approved leave for a team. The unplanned "
        "percentage of the week is calculated over the total days affected."
    )

    form_col, ref_col = st.columns([2, 1])

    with ref_col:
        st.markdown("**Absence reason reference**")
        st.caption("Use it to know whether a reason is Planned or Unplanned.")
        st.dataframe(
            pd.DataFrame(ABSENCE_REFERENCE),
            hide_index=True,
            use_container_width=True,
        )

    with form_col:
        report_date, leader, location, team, total_headcount = render_identity("abs")

        # Headcount alignment vs the official unit headcount (from the source
        # base). Access is pending, so this is None for now.
        registered = None
        try:
            registered = sqlserver.registered_headcount_for_unit(team, server=server)
        except Exception:  # noqa: BLE001
            registered = None

        alignment_pct = None
        if registered:
            alignment_pct = round(total_headcount / registered * 100, 2)

        low_alignment = alignment_pct is not None and alignment_pct < 90
        headcount_comment = ""
        if alignment_pct is None:
            st.caption(
                "Headcount alignment % is pending: no access to the source base yet."
            )
            headcount_comment = st.text_area(
                "Headcount comments (optional)",
                key="headcount_comment",
            )
        else:
            st.metric("Headcount alignment %", f"{alignment_pct:.2f}%")
            if low_alignment:
                st.warning(
                    "Please add a comment explaining why the reported people do not "
                    "reach 90% of the registered unit."
                )
            headcount_comment = st.text_area(
                "Headcount comments"
                + (" (required)" if low_alignment else " (optional)"),
                key="headcount_comment",
            )

        p1, p2 = st.columns(2)
        with p1:
            planned_leave = st.number_input(
                "Number of employees on planned leave",
                min_value=0,
                step=1,
                key="planned_leave",
                help="Number of employees who were on planned leave during the reported week.",
            )
        with p2:
            planned_days = st.number_input(
                "Days Impacted in total",
                min_value=0,
                step=1,
                key="planned_days",
                help="Total number of workdays impacted by planned employee absences during the previous week.",
            )
        st.caption("Days during the reported week")

        u1, u2 = st.columns(2)
        with u1:
            unplanned_leave = st.number_input(
                "Number of employees on unplanned leave",
                min_value=0,
                step=1,
                key="unplanned_leave",
                help="Number of employees who were on unplanned leave during the reported week.",
            )
        with u2:
            unplanned_days = st.number_input(
                "Days Impacted in total",
                min_value=0,
                step=1,
                key="unplanned_days",
                help="Total number of workdays impacted by unplanned employee absences during the previous week.",
            )
        st.caption("Days during the reported week")

        total_days, unplanned_pct = unplanned_percentage(
            int(total_headcount), int(planned_days), int(unplanned_days)
        )

        high_unplanned = unplanned_pct > 3
        unplanned_comment = ""
        if high_unplanned:
            st.warning("Unplanned is above 3%. Please add a comment explaining why.")
            unplanned_comment = st.text_area(
                "Comments",
                key="unplanned_comment",
                help="Required only when the number of unplanned % is greater than 3%.",
            )

        m1, m2 = st.columns(2)
        m1.metric("Total days affected", total_days)
        m2.metric("% Unplanned (week)", f"{unplanned_pct:.1f}%")

        submitted = st.button("Calculate & Save", key="absenteeism_submit")

    if submitted:
        if low_alignment and not headcount_comment.strip():
            st.error(
                "Please add a comment explaining why the reported people do not "
                "reach 90% of the registered unit."
            )
        elif high_unplanned and not unplanned_comment.strip():
            st.error("Please add a comment explaining why the unplanned percentage is above 3%.")
        else:
            try:
                sqlserver.insert_absenteeism(
                    report_date=report_date,
                    leader=leader.strip(),
                    location=location.strip(),
                    team=team.strip(),
                    total_headcount=int(total_headcount),
                    headcount_alignment_pct=alignment_pct,
                    headcount_comments=headcount_comment.strip(),
                    days_impacted_planned=int(planned_days),
                    num_employees_planned_leave=int(planned_leave),
                    days_impacted_unplanned=int(unplanned_days),
                    num_employees_unplanned_leave=int(unplanned_leave),
                    absenteeism_comments=unplanned_comment.strip(),
                    server=server,
                )
                st.success("Absenteeism record saved to SQL Server.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not save the record: {exc}")

    st.divider()
    st.subheader("Saved absenteeism records")
    _render_history(sqlserver.fetch_absenteeism, server)


def render_wfo(server: str | None) -> None:
    st.subheader("Work From Office (WFO)")

    report_date, leader, location, team, total_headcount = render_identity("wfo")

    # Only people/locations allowed by the by-country headcount source may save
    # WFO. Access is pending, so the button is enabled by default for now.
    allowed = can_use_wfo(location)
    if not allowed:
        st.info("You are not part of the group allowed to report WFO for this location.")

    all_attended = st.radio(
        "Did **all** required employees attend the office on every mandatory day during the reported week?",
        options=["Yes", "No"],
        index=None,
        horizontal=True,
        key="wfo_all_attended",
    )
    st.caption(
        '"Yes" is selected when the required WFO requirement is met for the '
        "entire week with no exceptions."
    )

    num_unattended = 0
    comments: list[str] = []

    if all_attended == "Yes":
        st.success("All required employees complied. 0 non-compliant employees will be reported.")
    elif all_attended == "No":
        st.info(
            "Please note: Select \"No\" only for employees who were expected to comply "
            "with the WFO requirement. Employees on vacation, sick leave, or other exempt "
            "absences should not be included."
        )
        st.caption("If \"No\" is selected, the system will require the following information:")
        non_compliant = st.number_input(
            "How many employees were non-compliant with the WFO requirement this week?",
            min_value=1,
            step=1,
            key="wfo_non_compliant",
        )
        num_unattended = int(non_compliant)
        num_boxes = min(num_unattended, 5)
        st.caption(
            "Add a comment/reason per non-compliant employee (up to 5)."
            if num_unattended <= 5
            else "More than 5 non-compliant employees: only the first 5 comments are stored."
        )
        for i in range(num_boxes):
            value = st.text_input(f"WFO comment {i + 1}", key=f"wfo_comment_{i + 1}")
            if value.strip():
                comments.append(value.strip())

    submitted = st.button("Calculate & Save", disabled=not allowed, key="wfo_submit")

    if submitted:
        if not allowed:
            st.error("You are not allowed to report WFO for this location.")
        elif all_attended is None:
            st.error("Please answer the WFO question before saving.")
        elif all_attended == "No" and num_unattended < 1:
            st.error("Please enter how many employees were non-compliant.")
        elif all_attended == "No" and not comments:
            st.error("Please add at least one comment explaining the non-compliance.")
        else:
            try:
                sqlserver.insert_wfo(
                    report_date=report_date,
                    leader=leader.strip(),
                    location=location.strip(),
                    team=team.strip(),
                    total_headcount=int(total_headcount),
                    all_attended=all_attended == "Yes",
                    num_unattended=num_unattended,
                    comments=comments,
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

    # Ensure the report table exists. SQL Server enforces the write permission
    # of the trusted Windows user, so no application-level gating is needed.
    init_error = None
    try:
        sqlserver.init_db(server=server)
    except Exception as exc:  # noqa: BLE001
        init_error = str(exc)

    st.info(f"Signed in as (Windows user): **{windows_user}**")
    if init_error:
        st.warning(f"Could not verify/create the table in SQL Server: {init_error}")

    absenteeism_tab, wfo_tab = st.tabs(["Absenteeism", "Work From Office"])
    with absenteeism_tab:
        render_absenteeism(server)
    with wfo_tab:
        render_wfo(server)


if __name__ == "__main__":
    main()
