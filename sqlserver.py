"""SQL Server data source for Workforce / Attendance.

Connects to the corporate SQL Server database ``[Bogota_GBS_NS]`` using Windows
Authentication (Trusted Connection) and runs the weekly Attendance calculation
based on ``vwAttendance`` and ``Holidays`` (same logic as the manual SSMS query).

It can also be scoped to the signed-in leader's team: the app detects the
Windows username and filters ``vwAttendance`` by a configurable manager column,
so each leader only sees their own people and locations.

Connection settings are read, in order of priority, from:
  1. The ``server`` / ``database`` arguments.
  2. Streamlit secrets ``[sqlserver] server`` / ``database``.
  3. Environment variables ``SQLSERVER_HOST`` / ``SQLSERVER_DATABASE``.
  4. Defaults (``localhost`` / ``Bogota_GBS_NS``).
"""

import getpass
import os
import re

import pandas as pd

DEFAULT_DATABASE = "Bogota_GBS_NS"
DEFAULT_SERVER = "localhost"
ATTENDANCE_VIEW = "vwAttendance"

# Column names can include letters, digits, underscore and spaces (bracket-quoted).
# Anything else (especially ']') is rejected to avoid SQL injection.
_IDENT_RE = re.compile(r"^[A-Za-z0-9_ ]+$")


def current_windows_user() -> str:
    """Return the Windows account name of the person running the app."""
    return os.getenv("USERNAME") or getpass.getuser()


def _safe_ident(name: str) -> str:
    """Validate a column name so it can be safely bracket-quoted in SQL."""
    if not name or not _IDENT_RE.match(name):
        raise ValueError(f"Invalid column name: {name!r}")
    return name


def _read_secret(key: str) -> str | None:
    """Read a value from Streamlit secrets if available, without hard dependency."""
    try:
        import streamlit as st

        return st.secrets.get("sqlserver", {}).get(key)  # type: ignore[no-any-return]
    except Exception:
        return None


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

    server = server or _read_secret("server") or os.getenv("SQLSERVER_HOST") or DEFAULT_SERVER
    database = (
        database
        or _read_secret("database")
        or os.getenv("SQLSERVER_DATABASE")
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


def _result_to_df(cursor) -> pd.DataFrame:
    """Advance to the first row-returning result set and build a DataFrame."""
    while cursor.description is None:
        if not cursor.nextset():
            return pd.DataFrame()
    columns = [col[0] for col in cursor.description]
    rows = [tuple(row) for row in cursor.fetchall()]
    return pd.DataFrame(rows, columns=columns)


def list_view_columns(
    view: str = ATTENDANCE_VIEW, server: str | None = None, database: str | None = None
) -> list[str]:
    """Return the column names of a view/table (for schema discovery in the UI)."""
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
            view,
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def preview_view(
    view: str = ATTENDANCE_VIEW,
    top: int = 10,
    server: str | None = None,
    database: str | None = None,
) -> pd.DataFrame:
    """Return the top N rows of a view/table to help identify columns."""
    safe_view = _safe_ident(view)
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT TOP ({int(top)}) * FROM [Bogota_GBS_NS].[dbo].[{safe_view}]")
        return _result_to_df(cursor)
    finally:
        conn.close()


def build_weekly_sql(
    manager_column: str | None = None, location_column: str | None = None
) -> str:
    """Build the weekly Attendance query, optionally scoped to a leader's team.

    Mirrors the manual SSMS query: last full week (Mon-Fri), Colombian holidays
    excluded, normalized by real business days (@dias), grouped by Unit/SubUnit
    (and Location when provided). When ``manager_column`` is set, a parameterized
    ``AND a.[manager_column] = ?`` filter is added to scope to the leader's team.
    """
    loc_inner = loc_outer = loc_group = ""
    if location_column:
        col = _safe_ident(location_column)
        loc_inner = f"        a.[{col}] AS Location,\n"
        loc_outer = "    q.Location,\n"
        loc_group = ",\n    q.Location"

    manager_filter = ""
    if manager_column:
        mcol = _safe_ident(manager_column)
        manager_filter = f"        AND a.[{mcol}] = ?\n"

    return f"""
SET NOCOUNT ON;

DECLARE @lunesSemanaPasada DATE =
    DATEADD(WEEK, DATEDIFF(WEEK, 0, GETDATE()) - 1, 0);

DECLARE @dias FLOAT;

SELECT @dias = COUNT(*)
FROM (
    SELECT DATEADD(DAY, v.n, @lunesSemanaPasada) AS d
    FROM (VALUES (0),(1),(2),(3),(4)) v(n)
) d
LEFT JOIN [Bogota_GBS_NS].[dbo].[Holidays] h
    ON h.[Date] = d.d
    AND h.Country_D = 'Colombia'
WHERE h.[Date] IS NULL;

SELECT
    q.[Unit],
    q.SubUnit_Code AS SubUnit,
    q.SubUnit_Description,
{loc_outer}    @lunesSemanaPasada AS Lunes_Semana,
    @dias AS Business_Days,

    COUNT(DISTINCT q.[Snumber]) AS People_In_Unit,

    COUNT(CASE WHEN q.[Reason] = 'Attended' THEN 1 END) AS Attended_Count,
    COUNT(CASE WHEN q.[Reason] = 'Attended' THEN 1 END) * 8 AS Attended_Hours,

    SUM(CASE WHEN q.[Reason] = 'Vacation' THEN 1 ELSE 0 END) AS Approved_Count,
    SUM(CASE WHEN q.[Reason] = 'Vacation' THEN 1 ELSE 0 END) * 8 AS Approved_Hours,

    ROUND(
        SUM(CASE WHEN q.[Reason] = 'Vacation' THEN 1 ELSE 0 END)
        / NULLIF(@dias, 0),
        1
    ) AS Approved_Semana,

    SUM(CASE WHEN q.[Reason] IN ('Medical Leave', 'Personal Day') THEN 1 ELSE 0 END) AS Not_Approved_Count,
    SUM(CASE WHEN q.[Reason] IN ('Medical Leave', 'Personal Day') THEN 1 ELSE 0 END) * 8 AS Not_Approved_Hours,

    ROUND(
        SUM(CASE WHEN q.[Reason] IN ('Medical Leave', 'Personal Day') THEN 1 ELSE 0 END)
        / NULLIF(@dias, 0),
        1
    ) AS Not_Approved_Semana

FROM (
    SELECT
        a.[Unit],
        a.[Reason],
        a.[Snumber],
        a.[Date],
{loc_inner}
        CASE
            WHEN a.[SubUnit] IN ('Swat', 'Triage') THEN 'TM'
            ELSE a.[SubUnit]
        END AS SubUnit_Code,

        CASE
            WHEN a.[SubUnit] IN ('Swat', 'Triage') THEN 'Transaction Monitoring'
            WHEN a.[SubUnit] = 'EDDU Regular' THEN 'EDDU Retail & SB'
            WHEN a.[SubUnit] = 'ADT' THEN 'AML Demarket'
            WHEN a.[SubUnit] = 'BI & Automations' THEN 'AML Ops-Business Intelligence'
            WHEN a.[SubUnit] = 'NS Bog' THEN 'Name Screening'
            WHEN a.[SubUnit] = 'Payment' THEN 'Payment Screening L1'
            WHEN a.[SubUnit] = 'MST' THEN 'Media Search Team'
            WHEN a.[SubUnit] = 'Others' THEN 'Strategy, Governance & Learning'
            ELSE a.[SubUnit]
        END AS SubUnit_Description

    FROM [Bogota_GBS_NS].[dbo].[vwAttendance] a
    LEFT JOIN [Bogota_GBS_NS].[dbo].[Holidays] h
        ON h.[Date] = a.[Date]
        AND h.Country_D = 'Colombia'

    WHERE a.[Date] >= @lunesSemanaPasada
        AND a.[Date] < DATEADD(WEEK, DATEDIFF(WEEK, 0, GETDATE()), 0)
        AND DATEPART(WEEKDAY, a.[Date]) BETWEEN 2 AND 6
        AND h.[Date] IS NULL
        AND a.[Unit] <> 'Enhanced Monitoring'
        AND a.[Country] = 'Colombia'
{manager_filter}        AND a.[SubUnit] IN (
            'EDDU Regular',
            'ADT',
            'BI & Automations',
            'NS Bog',
            'Swat',
            'Triage',
            'Payment',
            'MST',
            'Others'
        )
) q

GROUP BY
    q.[Unit],
    q.SubUnit_Code,
    q.SubUnit_Description{loc_group}

ORDER BY
    CASE
        WHEN q.SubUnit_Code = 'EDDU Regular' THEN 1
        WHEN q.SubUnit_Code = 'BI & Automations' THEN 2
        WHEN q.SubUnit_Code = 'ADT' THEN 3
        WHEN q.SubUnit_Code = 'NS Bog' THEN 4
        WHEN q.SubUnit_Code = 'TM' THEN 5
        WHEN q.SubUnit_Code = 'Payment' THEN 6
        WHEN q.SubUnit_Code = 'MST' THEN 7
        WHEN q.SubUnit_Code = 'Others' THEN 8
    END;
"""


def fetch_weekly_team_report(
    server: str | None = None,
    database: str | None = None,
    manager_column: str | None = None,
    manager_value: str | None = None,
    location_column: str | None = None,
) -> pd.DataFrame:
    """Run the weekly Attendance query and return the result as a DataFrame.

    When ``manager_column`` and ``manager_value`` are provided, results are
    scoped to that leader's team only.
    """
    sql = build_weekly_sql(manager_column=manager_column, location_column=location_column)
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        if manager_column and manager_value is not None:
            cursor.execute(sql, manager_value)
        else:
            cursor.execute(sql)
        return _result_to_df(cursor)
    finally:
        conn.close()
