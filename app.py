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

WFO_REASONS = [
    "Medical Appointment",
    "Personal Emergency",
    "Approved by Manager for specific reasons",
    "Work-related (travel, off-site, training, etc)",
    "Operational (weather, health isolation, suspension)",
    "Unexcused Absence/Declined WFO",
    "Other",
]

# Example values only - replace with the real ones once confirmed.
LOCATION_OPTIONS = [
    "Bogota",
    "Medellin",
    "Cali",
    "Barranquilla",
    "Remote",
    "Other",
]
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


def render_absenteeism(server: str | None, can_write: bool, created_by: str) -> None:
    st.subheader("Absenteeism")
    st.caption(
        "Register planned and unplanned approved leave for a unit. The unplanned "
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
        record_date = st.date_input("Date", value=date.today())
        location = st.selectbox("Location", options=LOCATION_OPTIONS)
        unit = st.selectbox("Unit", options=UNIT_OPTIONS)
        vicepresident = st.selectbox("Vicepresident", options=VICEPRESIDENT_OPTIONS)
        people_in_unit = st.number_input("People in unit", min_value=0, step=1)

        low_headcount = people_in_unit < 20
        headcount_comment = ""
        if low_headcount:
            st.warning(
                "Please add a comment explaining why the reported people do not "
                "reach 90% of the registered unit."
            )
            headcount_comment = st.text_area(
                "Comment (why the reported people do not reach 90% of the unit)",
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
            int(people_in_unit), int(planned_days), int(unplanned_days)
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

        submitted = st.button("Calculate & Save", disabled=not can_write, key="absenteeism_submit")

    if submitted:
        if not can_write:
            st.error("You do not have permission to write.")
        elif low_headcount and not headcount_comment.strip():
            st.error(
                "Please add a comment explaining why the reported people do not "
                "reach 90% of the registered unit."
            )
        elif high_unplanned and not unplanned_comment.strip():
            st.error("Please add a comment explaining why the unplanned percentage is above 3%.")
        elif total_days == 0:
            st.error("Please enter at least some days affected before saving.")
        else:
            try:
                sqlserver.insert_absenteeism(
                    record_date=record_date,
                    location=location.strip(),
                    vicepresident=vicepresident.strip(),
                    unit=unit.strip(),
                    people_in_unit=int(people_in_unit),
                    planned_leave=int(planned_leave),
                    planned_days_affected=int(planned_days),
                    unplanned_leave=int(unplanned_leave),
                    unplanned_days_affected=int(unplanned_days),
                    total_days_affected=total_days,
                    unplanned_pct=unplanned_pct,
                    headcount_comment=headcount_comment.strip(),
                    unplanned_comment=unplanned_comment.strip(),
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

    if all_attended == "Yes":
        st.success("All required employees complied. 0 non-compliant employees will be reported.")
        record_date = st.date_input("Date", value=date.today(), key="wfo_date_yes")
        if st.button("Calculate & Save", disabled=not can_write, key="wfo_save_yes"):
            if not can_write:
                st.error("You do not have permission to write.")
            else:
                try:
                    sqlserver.insert_wfo(
                        record_date=record_date,
                        all_compliant=True,
                        non_compliant=0,
                        reason="",
                        created_by=created_by,
                        server=server,
                    )
                    st.success("WFO record saved to SQL Server (0 non-compliant).")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not save the record: {exc}")

    elif all_attended == "No":
        st.info(
            "Please note: Select \"No\" only for employees who were expected to comply "
            "with the WFO requirement. Employees on vacation, sick leave, or other exempt "
            "absences should not be included."
        )
        st.caption("If \"No\" is selected, the system will require users to provide the following information:")

        record_date = st.date_input("Date", value=date.today(), key="wfo_date_no")
        non_compliant = st.number_input(
            "How many employees were non-compliant with the WFO requirement this week?",
            min_value=1,
            step=1,
            key="wfo_non_compliant",
        )
        reason = st.selectbox("Select a Reason", options=WFO_REASONS, key="wfo_reason")

        if st.button("Calculate & Save", disabled=not can_write, key="wfo_save_no"):
            if not can_write:
                st.error("You do not have permission to write.")
            elif int(non_compliant) < 1:
                st.error("Please enter how many employees were non-compliant.")
            else:
                try:
                    sqlserver.insert_wfo(
                        record_date=record_date,
                        all_compliant=False,
                        non_compliant=int(non_compliant),
                        reason=reason.strip(),
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
