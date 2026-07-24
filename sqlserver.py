"""SQL Server data layer for the Absenteeism / WFO tool.

Persistence lives in SQL Server (``[Bogota_GBS_NS]``).

Authentication model (VM + service account):
  1. People sign in with Scotia ID + app password against ``dbo.App_Users``
     (bcrypt hash, IsActive, CanWrite).
  2. All SQL access uses **Trusted Connection** as the Windows identity of the
     process — the dedicated **service account** that runs Streamlit on the VM.
  3. The login Scotia ID is stored in ``Created_By`` / session context ``AppUser``
     and used for GlobalWorkforceHR (Canada / headcount).

Per-person Windows SQL grants are NOT used for INSERT; write access is controlled
by ``App_Users.CanWrite`` (and the service account must have INSERT).

Configuration (priority order):
  1. Arguments passed to ``get_connection``.
  2. Streamlit secrets ``[sqlserver] server`` / ``database``.
  3. Environment variables ``SQLSERVER_HOST`` / ``SQLSERVER_DATABASE``.
  4. Defaults (``localhost`` / ``Bogota_GBS_NS``).
"""

from __future__ import annotations

import os

import bcrypt
import pandas as pd

DEFAULT_DATABASE = "Bogota_GBS_NS"
DEFAULT_SERVER = "localhost"

USERS_TABLE = "dbo.App_Users"
REPORT_TABLE = "dbo.Attendance_Absenteeism_Report"
HR_TABLE = "[EDDU_ID].[dbo].[GlobalWorkforceHR]"
WFO_COUNTRY = "Canada"


def _read_secret(section: str, key: str) -> str | None:
    """Read a value from Streamlit secrets if available, without hard dependency."""
    try:
        import streamlit as st

        return st.secrets.get(section, {}).get(key)  # type: ignore[no-any-return]
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


def _resolve_server_database(
    server: str | None = None,
    database: str | None = None,
) -> tuple[str, str]:
    server = server or _read_secret("sqlserver", "server") or os.environ.get("SQLSERVER_HOST") or DEFAULT_SERVER
    database = (
        database
        or _read_secret("sqlserver", "database")
        or os.environ.get("SQLSERVER_DATABASE")
        or DEFAULT_DATABASE
    )
    return server, database


def get_connection(
    server: str | None = None,
    database: str | None = None,
    app_user: str | None = None,
):
    """Open a Trusted Connection as the process identity (service account).

    When ``app_user`` is set, store it in SQL session context as ``AppUser`` for
    auditing (who signed into the Streamlit app).
    """
    import pyodbc

    server, database = _resolve_server_database(server, database)
    driver = _pick_driver()

    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Trusted_Connection=yes;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )
    conn = pyodbc.connect(conn_str)
    if app_user:
        cursor = conn.cursor()
        cursor.execute(
            "EXEC sp_set_session_context @key = N'AppUser', @value = ?",
            app_user.strip(),
        )
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_REPORT = """
IF OBJECT_ID('dbo.Attendance_Absenteeism_Report', 'U') IS NULL
CREATE TABLE dbo.Attendance_Absenteeism_Report (
    Id                             INT IDENTITY(1,1) PRIMARY KEY,
    Report_Date                    DATE            NOT NULL,
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
    Created_By                     VARCHAR(200)    NULL,
    CreatedAt                      DATETIME        NOT NULL DEFAULT GETDATE()
);
"""

_ENSURE_CREATED_BY = """
IF OBJECT_ID('dbo.Attendance_Absenteeism_Report', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.Attendance_Absenteeism_Report', 'Created_By') IS NULL
    ALTER TABLE dbo.Attendance_Absenteeism_Report ADD Created_By VARCHAR(200) NULL;
"""

_CREATE_APP_USERS = """
IF OBJECT_ID('dbo.App_Users', 'U') IS NULL
CREATE TABLE dbo.App_Users (
    Id              INT IDENTITY(1,1) PRIMARY KEY,
    Username        VARCHAR(100)  NOT NULL,
    PasswordHash    VARCHAR(255)  NOT NULL,
    DisplayName     VARCHAR(200)  NULL,
    CanWrite        BIT           NOT NULL DEFAULT 1,
    IsActive        BIT           NOT NULL DEFAULT 1,
    CreatedAt       DATETIME      NOT NULL DEFAULT GETDATE(),
    CONSTRAINT UQ_App_Users_Username UNIQUE (Username)
);
"""

_ENSURE_CAN_WRITE = """
IF OBJECT_ID('dbo.App_Users', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.App_Users', 'CanWrite') IS NULL
    ALTER TABLE dbo.App_Users ADD CanWrite BIT NOT NULL CONSTRAINT DF_App_Users_CanWrite DEFAULT 1;
"""


def init_db(server: str | None = None, database: str | None = None) -> None:
    """Ensure report + App_Users tables exist (service account Trusted Connection)."""
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute(_CREATE_REPORT)
        cursor.execute(_ENSURE_CREATED_BY)
        cursor.execute(_CREATE_APP_USERS)
        cursor.execute(_ENSURE_CAN_WRITE)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Authentication (app table only; SQL uses service account)
# ---------------------------------------------------------------------------

def service_account_can_insert(server: str | None = None, database: str | None = None) -> bool:
    """Return True if the Trusted Connection (service account) can INSERT."""
    conn = get_connection(server=server, database=database)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT HAS_PERMS_BY_NAME(
                'dbo.Attendance_Absenteeism_Report',
                'OBJECT',
                'INSERT'
            )
            """
        )
        row = cursor.fetchone()
        return bool(row and row[0] == 1)
    finally:
        conn.close()


def verify_app_user(
    username: str,
    password: str,
    server: str | None = None,
    database: str | None = None,
) -> dict:
    """Verify username/password against dbo.App_Users (service account connection)."""
    username = (username or "").strip()
    if not username or password is None or password == "":
        raise ValueError("Username and password are required.")

    conn = get_connection(server=server, database=database, app_user=username)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT Username, PasswordHash, DisplayName, IsActive, CanWrite
            FROM {USERS_TABLE}
            WHERE Username = ?
            """,
            username,
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError("Invalid username or password.")

        stored_user, password_hash, display_name, is_active, can_write_flag = row
        if not is_active:
            raise ValueError("This account is inactive. Contact an administrator.")

        hash_bytes = (password_hash or "").encode("utf-8")
        if not bcrypt.checkpw(password.encode("utf-8"), hash_bytes):
            raise ValueError("Invalid username or password.")

        return {
            "ok": True,
            "username": stored_user,
            "display_name": display_name,
            "can_write": bool(can_write_flag),
        }
    finally:
        conn.close()


def authenticate_user(
    username: str,
    password: str,
    server: str | None = None,
    database: str | None = None,
) -> dict:
    """Validate App_Users and whether this person may save (CanWrite + service INSERT).

    Returns::
        {
          "username": str,
          "display_name": str | None,
          "can_write": bool,
        }
    """
    app_user = verify_app_user(username, password, server=server, database=database)
    svc_ok = service_account_can_insert(server=server, database=database)
    return {
        "username": app_user["username"],
        "display_name": app_user.get("display_name"),
        "can_write": bool(app_user.get("can_write")) and svc_ok,
    }


# ---------------------------------------------------------------------------
# Headcount / WFO access source: [EDDU_ID].[dbo].[GlobalWorkforceHR]
# ---------------------------------------------------------------------------

_MANAGER_PROFILE_SQL = f"""
SELECT TOP 1
    m.[Scotia ID Confidential] AS ScotiaId,
    m.[Position Title]         AS PositionTitle,
    m.[Position Code]          AS PositionCode,
    m.[Country Name]           AS CountryName,
    m.[Preferred First Name]   AS PreferredFirstName,
    m.[Employee Last Name]     AS EmployeeLastName,
    CASE
        WHEN m.[Position Title] LIKE '%Manager%' THEN 1
        ELSE 0
    END AS IsManager,
    CASE
        WHEN m.[Position Title] LIKE '%Manager%' THEN (
            SELECT COUNT(*)
            FROM {HR_TABLE} r
            WHERE r.[Manager Position Code] = m.[Position Code]
        )
        ELSE 0
    END AS TeamHeadcount
FROM {HR_TABLE} m
WHERE m.[Scotia ID Confidential] = ?
"""


def fetch_manager_profile(
    scotia_id: str,
    server: str | None = None,
    database: str | None = None,
) -> dict:
    """Look up the logged-in Scotia ID in GlobalWorkforceHR and derive team info."""
    scotia_id = (scotia_id or "").strip()
    empty = {
        "scotia_id": scotia_id,
        "found": False,
        "is_manager": False,
        "position_code": None,
        "position_title": None,
        "country_name": None,
        "team_headcount": None,
        "wfo_allowed": False,
        "display_name": None,
    }
    if not scotia_id:
        return empty

    conn = get_connection(server=server, database=database, app_user=scotia_id)
    try:
        cursor = conn.cursor()
        cursor.execute(_MANAGER_PROFILE_SQL, scotia_id)
        row = cursor.fetchone()
        if not row:
            return empty

        columns = [col[0] for col in cursor.description]
        data = dict(zip(columns, row))
        is_manager = bool(data.get("IsManager"))
        country = (data.get("CountryName") or "").strip()
        first = (data.get("PreferredFirstName") or "").strip()
        last = (data.get("EmployeeLastName") or "").strip()
        display = " ".join(p for p in (first, last) if p) or None
        team_count = int(data.get("TeamHeadcount") or 0) if is_manager else None

        return {
            "scotia_id": data.get("ScotiaId") or scotia_id,
            "found": True,
            "is_manager": is_manager,
            "position_code": data.get("PositionCode"),
            "position_title": data.get("PositionTitle"),
            "country_name": country or None,
            "team_headcount": team_count,
            "wfo_allowed": country.casefold() == WFO_COUNTRY.casefold(),
            "display_name": display,
        }
    finally:
        conn.close()


def registered_headcount_for_manager(
    scotia_id: str,
    server: str | None = None,
    database: str | None = None,
) -> int | None:
    """Return the manager's official team headcount, or None if not available."""
    profile = fetch_manager_profile(scotia_id=scotia_id, server=server, database=database)
    return profile.get("team_headcount")


# ---------------------------------------------------------------------------
# Absenteeism (own row; WFO columns left NULL)
# ---------------------------------------------------------------------------

def insert_absenteeism(
    report_date,
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
    created_by: str,
    server: str | None = None,
    database: str | None = None,
) -> None:
    """Insert one Absenteeism row (WFO columns left NULL)."""
    conn = get_connection(server=server, database=database, app_user=created_by)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            INSERT INTO {REPORT_TABLE} (
                Report_Date, Leader, Location, Team, Total_Headcount,
                Headcount_Alignment_Pct, Headcount_Comments,
                Days_Impacted_Planned, Num_Employees_Planned_Leave,
                Days_Impacted_Unplanned, Num_Employees_Unplanned_Leave,
                Absenteeism_Comments, Created_By
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            report_date,
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
            created_by,
        )
        conn.commit()
    finally:
        conn.close()


def fetch_absenteeism(
    server: str | None = None,
    database: str | None = None,
    app_user: str | None = None,
) -> pd.DataFrame:
    """Return absenteeism rows (those without WFO data), newest first."""
    conn = get_connection(server=server, database=database, app_user=app_user)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM {REPORT_TABLE} WHERE WFO_All_Attended_Flag IS NULL ORDER BY Id DESC"
        )
        return _result_to_df(cursor)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Work From Office (WFO) (own row; absenteeism numeric columns stored as 0)
# ---------------------------------------------------------------------------

def insert_wfo(
    report_date,
    leader: str,
    location: str,
    team: str,
    total_headcount: int,
    all_attended: bool,
    num_unattended: int,
    created_by: str,
    comments: list[str] | None = None,
    server: str | None = None,
    database: str | None = None,
) -> None:
    """Insert one WFO row."""
    comments = comments or []
    padded = [(comments[i] if i < len(comments) else None) for i in range(5)]
    conn = get_connection(server=server, database=database, app_user=created_by)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            INSERT INTO {REPORT_TABLE} (
                Report_Date, Leader, Location, Team, Total_Headcount,
                Days_Impacted_Planned, Num_Employees_Planned_Leave,
                Days_Impacted_Unplanned, Num_Employees_Unplanned_Leave,
                WFO_All_Attended_Flag, Num_WFO_Unattended,
                WFO_Comment1, WFO_Comment2, WFO_Comment3, WFO_Comment4, WFO_Comment5,
                Created_By
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            report_date,
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
            created_by,
        )
        conn.commit()
    finally:
        conn.close()


def fetch_wfo(
    server: str | None = None,
    database: str | None = None,
    app_user: str | None = None,
) -> pd.DataFrame:
    """Return WFO rows (those with a WFO flag set), newest first."""
    conn = get_connection(server=server, database=database, app_user=app_user)
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
