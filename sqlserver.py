"""SQL Server data layer for the Absenteeism / WFO tool.

All persistence lives in SQL Server (``[Bogota_GBS_NS]``) and uses Windows
Authentication (Trusted Connection) through pyodbc. Because the connection is
trusted, the SQL Server login is the Windows user running the app.

Authorization: before allowing a write, the app checks whether the current
Windows user belongs to a configurable Active Directory group, using the native
``IS_MEMBER('DOMAIN\\Group')`` function over the same trusted connection.

Configuration (priority order):
  1. Arguments passed to ``get_connection``.
  2. Streamlit secrets ``[sqlserver] server`` / ``database`` and ``[auth] write_group``.
  3. Environment variables ``SQLSERVER_HOST`` / ``SQLSERVER_DATABASE`` / ``AD_WRITE_GROUP``.
  4. Defaults (``localhost`` / ``Bogota_GBS_NS``).
"""

import getpass
import os

import pandas as pd

DEFAULT_DATABASE = "Bogota_GBS_NS"
DEFAULT_SERVER = "localhost"


def current_windows_user() -> str:
    """Return the Windows account name of the person running the app."""
    return os.environ.get("USERNAME") or getpass.getuser()


def current_windows_domain_user() -> str:
    """Return ``DOMAIN\\user`` when the domain is known, else just the user."""
    user = current_windows_user()
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{user}" if domain else user


def _read_secret(section: str, key: str) -> str | None:
    """Read a value from Streamlit secrets if available, without hard dependency."""
    try:
        import streamlit as st

        return st.secrets.get(section, {}).get(key)  # type: ignore[no-any-return]
    except Exception:
        return None


def get_write_group() -> str | None:
    """Return the configured AD group allowed to write, or None if not set."""
    return _read_secret("auth", "write_group") or os.environ.get("AD_WRITE_GROUP")


def _pick_driver() -> str:
    """Return the best available SQL Server ODBC driver installed on the machine."""
    import pyodbc

    installed = list(pyodbc.drivers())
    for preferred in (
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server",
    ):
        if preferred in installed:
            return preferred
    if installed:
        return installed[-1]
    raise RuntimeError(
        "No SQL Server ODBC driver found. Install 'ODBC Driver 18 for SQL Server'."
    )


def get_connection(server: str | None = None, database: str | None = None):
    """Open a Windows-authenticated (Trusted Connection) pyodbc connection."""
    import pyodbc

    server = server or _read_secret("sqlserver", "server") or os.environ.get("SQLSERVER_HOST") or DEFAULT_SERVER
    database = (
        database
        or _read_secret("sqlserver", "database")
        or os.environ.get("SQLSERVER_DATABASE")
        or DEFAULT_DATABASE
    )
    driver = _pick_driver()

    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Trusted_Connection=yes;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

def can_write(server: str | None = None, database: str | None = None) -> bool:
    """Return True only if the current trusted login is a member of the
    configured AD write group. Returns False when the group is not configured
    or the membership check does not resolve to a member.
    """
    group = get_write_group()
    if not group:
        return False
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT IS_MEMBER(?)", group)
        row = cursor.fetchone()
        # IS_MEMBER returns 1 (member), 0 (not), or NULL (group not valid).
        return bool(row and row[0] == 1)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
#
# Single unified table provided by the business:
#   dbo.Attendance_Absenteeism_Report
# It holds both the Absenteeism and the WFO information. Absenteeism numeric
# columns are NOT NULL; WFO columns are nullable. Because we keep two separate
# save actions, a WFO-only row stores 0 in the required absenteeism numeric
# columns, and an absenteeism-only row leaves the WFO columns NULL.

REPORT_TABLE = "dbo.Attendance_Absenteeism_Report"

_CREATE_REPORT = """
IF OBJECT_ID('dbo.Attendance_Absenteeism_Report', 'U') IS NULL
CREATE TABLE dbo.Attendance_Absenteeism_Report (
    Id                             INT IDENTITY(1,1) PRIMARY KEY,
    Report_Week                    DATE            NULL,
    Leader                         VARCHAR(150)    NOT NULL,
    Location                       VARCHAR(100)    NOT NULL,
    Team                           VARCHAR(150)    NOT NULL,
    Total_Headcount                INT             NOT NULL,
    Headcount_Alignment_Pct        DECIMAL(6, 2)   NULL,
    Headcount_Comments             VARCHAR(1000)   NULL,
    Days_Impacted_Planned          DECIMAL(8, 2)   NOT NULL,
    Num_Employees_Planned_Leave    INT             NOT NULL,
    Days_Impacted_Unplanned        DECIMAL(8, 2)   NOT NULL,
    Num_Employees_Unplanned_Leave  INT             NOT NULL,
    Absenteeism_Comments           VARCHAR(1000)   NULL,
    WFO_All_Attended_Flag          BIT             NULL,
    Num_WFO_Unattended             INT             NULL,
    WFO_Comment1                   VARCHAR(500)    NULL,
    WFO_Comment2                   VARCHAR(500)    NULL,
    WFO_Comment3                   VARCHAR(500)    NULL,
    WFO_Comment4                   VARCHAR(500)    NULL,
    WFO_Comment5                   VARCHAR(500)    NULL,
    CreatedAt                      DATETIME        NOT NULL DEFAULT GETDATE()
);
"""

# The business table already exists without a week/date column, so make sure a
# Report_Week column is present (added only if the login has ALTER permission).
_ENSURE_REPORT_WEEK = """
IF OBJECT_ID('dbo.Attendance_Absenteeism_Report', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.Attendance_Absenteeism_Report', 'Report_Week') IS NULL
    ALTER TABLE dbo.Attendance_Absenteeism_Report ADD Report_Week DATE NULL;
"""


def init_db(server: str | None = None, database: str | None = None) -> None:
    """Ensure the report table (and its Report_Week column) exists."""
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute(_CREATE_REPORT)
        cursor.execute(_ENSURE_REPORT_WEEK)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Headcount alignment source (pending access)
# ---------------------------------------------------------------------------

def registered_headcount_for_unit(
    team: str,
    unit_id: str | int | None = None,
    server: str | None = None,
    database: str | None = None,
) -> int | None:
    """Return the official registered headcount for a unit.

    The alignment % compares the reported ``Total_Headcount`` against the number
    of people that belong to the unit (matched by its id) in another database.
    Access to that source base is not granted yet, so this returns ``None`` and
    the alignment % cannot be computed automatically for now.

    TODO(when access is granted): replace the body with something like::

        SELECT COUNT(*)
        FROM [OtherDatabase].[dbo].[SomeUnitPeopleTable]
        WHERE UnitId = ?
    """
    return None


# ---------------------------------------------------------------------------
# Absenteeism
# ---------------------------------------------------------------------------

def insert_absenteeism(
    report_week,
    leader: str,
    location: str,
    team: str,
    total_headcount: int,
    headcount_alignment_pct: float | None,
    headcount_comments: str,
    days_impacted_planned: float,
    num_employees_planned_leave: int,
    days_impacted_unplanned: float,
    num_employees_unplanned_leave: int,
    absenteeism_comments: str,
    server: str | None = None,
    database: str | None = None,
) -> None:
    """Insert one Absenteeism row (WFO columns left NULL)."""
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            INSERT INTO {REPORT_TABLE} (
                Report_Week, Leader, Location, Team, Total_Headcount,
                Headcount_Alignment_Pct, Headcount_Comments,
                Days_Impacted_Planned, Num_Employees_Planned_Leave,
                Days_Impacted_Unplanned, Num_Employees_Unplanned_Leave,
                Absenteeism_Comments
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            report_week,
            leader,
            location,
            team,
            total_headcount,
            headcount_alignment_pct,
            headcount_comments,
            days_impacted_planned,
            num_employees_planned_leave,
            days_impacted_unplanned,
            num_employees_unplanned_leave,
            absenteeism_comments,
        )
        conn.commit()
    finally:
        conn.close()


def fetch_absenteeism(server: str | None = None, database: str | None = None) -> pd.DataFrame:
    """Return absenteeism rows (those without WFO data), newest first."""
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM {REPORT_TABLE} WHERE WFO_All_Attended_Flag IS NULL ORDER BY Id DESC"
        )
        return _result_to_df(cursor)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Work From Office (WFO)
# ---------------------------------------------------------------------------

def insert_wfo(
    report_week,
    leader: str,
    location: str,
    team: str,
    total_headcount: int,
    all_attended: bool,
    num_unattended: int,
    comments: list[str] | None = None,
    server: str | None = None,
    database: str | None = None,
) -> None:
    """Insert one WFO row.

    The absenteeism numeric columns are NOT NULL in the shared table, so they are
    stored as 0 for a WFO-only row. Up to five comments map to WFO_Comment1..5.
    """
    comments = comments or []
    padded = [(comments[i] if i < len(comments) else None) for i in range(5)]
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            INSERT INTO {REPORT_TABLE} (
                Report_Week, Leader, Location, Team, Total_Headcount,
                Days_Impacted_Planned, Num_Employees_Planned_Leave,
                Days_Impacted_Unplanned, Num_Employees_Unplanned_Leave,
                WFO_All_Attended_Flag, Num_WFO_Unattended,
                WFO_Comment1, WFO_Comment2, WFO_Comment3, WFO_Comment4, WFO_Comment5
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            report_week,
            leader,
            location,
            team,
            total_headcount,
            0,
            0,
            0,
            0,
            1 if all_attended else 0,
            num_unattended,
            padded[0],
            padded[1],
            padded[2],
            padded[3],
            padded[4],
        )
        conn.commit()
    finally:
        conn.close()


def fetch_wfo(server: str | None = None, database: str | None = None) -> pd.DataFrame:
    """Return WFO rows (those with a WFO flag set), newest first."""
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM {REPORT_TABLE} WHERE WFO_All_Attended_Flag IS NOT NULL ORDER BY Id DESC"
        )
        return _result_to_df(cursor)
    finally:
        conn.close()


def _result_to_df(cursor) -> pd.DataFrame:
    """Advance to the first row-returning result set and build a DataFrame."""
    while cursor.description is None:
        if not cursor.nextset():
            return pd.DataFrame()
    columns = [col[0] for col in cursor.description]
    rows = [tuple(row) for row in cursor.fetchall()]
    return pd.DataFrame(rows, columns=columns)
