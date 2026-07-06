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

_CREATE_ABSENTEEISM = """
IF OBJECT_ID('dbo.Absenteeism_Records', 'U') IS NULL
CREATE TABLE dbo.Absenteeism_Records (
    Id                     INT IDENTITY(1,1) PRIMARY KEY,
    RecordDate             DATE           NOT NULL,
    Vicepresident          NVARCHAR(200)  NULL,
    Unit                   NVARCHAR(200)  NULL,
    PeopleInUnit           INT            NULL,
    PlannedLeave           INT            NULL,
    PlannedDaysAffected    INT            NULL,
    UnplannedLeave         INT            NULL,
    UnplannedDaysAffected  INT            NULL,
    TotalDaysAffected      INT            NULL,
    UnplannedPct           FLOAT          NULL,
    Comment                NVARCHAR(1000) NULL,
    CreatedBy              NVARCHAR(200)  NULL,
    CreatedAt              DATETIME       NOT NULL DEFAULT GETDATE()
);
"""

_CREATE_WFO = """
IF OBJECT_ID('dbo.WFO_Records', 'U') IS NULL
CREATE TABLE dbo.WFO_Records (
    Id             INT IDENTITY(1,1) PRIMARY KEY,
    RecordDate     DATE           NOT NULL,
    Expected       INT            NULL,
    Actual         INT            NULL,
    AttendancePct  FLOAT          NULL,
    Comment        NVARCHAR(1000) NULL,
    CreatedBy      NVARCHAR(200)  NULL,
    CreatedAt      DATETIME       NOT NULL DEFAULT GETDATE()
);
"""


def init_db(server: str | None = None, database: str | None = None) -> None:
    """Create the Absenteeism and WFO tables if they do not exist yet."""
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute(_CREATE_ABSENTEEISM)
        cursor.execute(_CREATE_WFO)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Absenteeism
# ---------------------------------------------------------------------------

def insert_absenteeism(
    record_date,
    vicepresident: str,
    unit: str,
    people_in_unit: int,
    planned_leave: int,
    planned_days_affected: int,
    unplanned_leave: int,
    unplanned_days_affected: int,
    total_days_affected: int,
    unplanned_pct: float,
    comment: str,
    created_by: str,
    server: str | None = None,
    database: str | None = None,
) -> None:
    """Insert one Absenteeism record into SQL Server."""
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO dbo.Absenteeism_Records (
                RecordDate, Vicepresident, Unit, PeopleInUnit,
                PlannedLeave, PlannedDaysAffected, UnplannedLeave, UnplannedDaysAffected,
                TotalDaysAffected, UnplannedPct, Comment, CreatedBy
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            record_date,
            vicepresident,
            unit,
            people_in_unit,
            planned_leave,
            planned_days_affected,
            unplanned_leave,
            unplanned_days_affected,
            total_days_affected,
            unplanned_pct,
            comment,
            created_by,
        )
        conn.commit()
    finally:
        conn.close()


def fetch_absenteeism(server: str | None = None, database: str | None = None) -> pd.DataFrame:
    """Return all Absenteeism records, newest first."""
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM dbo.Absenteeism_Records ORDER BY Id DESC")
        return _result_to_df(cursor)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Work From Office (WFO)
# ---------------------------------------------------------------------------

def insert_wfo(
    record_date,
    expected: int,
    actual: int,
    attendance_pct: float,
    comment: str,
    created_by: str,
    server: str | None = None,
    database: str | None = None,
) -> None:
    """Insert one WFO record into SQL Server."""
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO dbo.WFO_Records (
                RecordDate, Expected, Actual, AttendancePct, Comment, CreatedBy
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            record_date,
            expected,
            actual,
            attendance_pct,
            comment,
            created_by,
        )
        conn.commit()
    finally:
        conn.close()


def fetch_wfo(server: str | None = None, database: str | None = None) -> pd.DataFrame:
    """Return all WFO records, newest first."""
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM dbo.WFO_Records ORDER BY Id DESC")
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
