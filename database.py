"""SQLite data-access layer for the Absenteeism Hours Tracker.

SQLite is a self-contained SQL engine bundled with Python, so the app runs with
no external database server. To switch to MySQL/PostgreSQL later, only the
``get_connection`` helper and the SQL placeholder style need to change.
"""

import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent / "absenteeism.db"


def get_connection() -> sqlite3.Connection:
    """Open a connection with row access by column name."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the tables if they do not exist yet."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hours_records (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_name    TEXT    NOT NULL,
                record_date      TEXT    NOT NULL,
                approved_hours   REAL    NOT NULL,
                disapproved_hours REAL   NOT NULL,
                total_hours      REAL    NOT NULL,
                approval_rate    REAL    NOT NULL,
                absenteeism_rate REAL    NOT NULL,
                created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_team_report (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                week_monday          TEXT    NOT NULL,
                leader               TEXT,
                unit                 TEXT,
                subunit              TEXT,
                subunit_description  TEXT,
                location             TEXT,
                business_days        REAL,
                people_in_unit       INTEGER,
                attended_count       INTEGER,
                attended_hours       REAL,
                approved_count       INTEGER,
                approved_hours       REAL,
                approved_semana      REAL,
                not_approved_count   INTEGER,
                not_approved_hours   REAL,
                not_approved_semana  REAL,
                saved_at             TEXT    NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        _ensure_columns(
            conn,
            "weekly_team_report",
            {"leader": "TEXT", "location": "TEXT"},
        )


def _ensure_columns(conn, table: str, columns: dict[str, str]) -> None:
    """Add any missing columns to an existing table (lightweight migration)."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, col_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")


def insert_record(
    employee_name: str,
    record_date: date,
    approved_hours: float,
    disapproved_hours: float,
    total_hours: float,
    approval_rate: float,
    absenteeism_rate: float,
) -> int:
    """Persist one record and return its new row id."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO hours_records (
                employee_name, record_date, approved_hours, disapproved_hours,
                total_hours, approval_rate, absenteeism_rate
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                employee_name,
                record_date.isoformat(),
                approved_hours,
                disapproved_hours,
                total_hours,
                approval_rate,
                absenteeism_rate,
            ),
        )
        return cursor.lastrowid


def fetch_records() -> list[sqlite3.Row]:
    """Return all stored records, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM hours_records ORDER BY id DESC"
        ).fetchall()
        return rows


_REPORT_FIELDS = (
    "leader",
    "unit",
    "subunit",
    "subunit_description",
    "location",
    "business_days",
    "people_in_unit",
    "attended_count",
    "attended_hours",
    "approved_count",
    "approved_hours",
    "approved_semana",
    "not_approved_count",
    "not_approved_hours",
    "not_approved_semana",
)


def _native(value):
    """Convert numpy/pandas scalars to native Python types so SQLite stores them
    as numbers/text instead of opaque byte blobs."""
    if value is None:
        return None
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return value.item()
        except Exception:
            return value
    return value


def save_weekly_report(week_monday: str, rows: list[dict], leader: str | None = None) -> int:
    """Persist one weekly team report (replacing any previous one for the same
    week and leader). ``rows`` is a list of per Unit/SubUnit dictionaries.
    Returns the number of rows inserted.
    """
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM weekly_team_report WHERE week_monday = ? AND IFNULL(leader, '') = IFNULL(?, '')",
            (week_monday, leader),
        )
        payload = []
        for row in rows:
            record = {field: _native(row.get(field)) for field in _REPORT_FIELDS}
            record["week_monday"] = week_monday
            if leader is not None:
                record["leader"] = leader
            payload.append(record)

        conn.executemany(
            """
            INSERT INTO weekly_team_report (
                week_monday, leader, unit, subunit, subunit_description, location,
                business_days, people_in_unit, attended_count, attended_hours,
                approved_count, approved_hours, approved_semana,
                not_approved_count, not_approved_hours, not_approved_semana
            ) VALUES (
                :week_monday, :leader, :unit, :subunit, :subunit_description, :location,
                :business_days, :people_in_unit, :attended_count, :attended_hours,
                :approved_count, :approved_hours, :approved_semana,
                :not_approved_count, :not_approved_hours, :not_approved_semana
            )
            """,
            payload,
        )
        return len(rows)


def fetch_weekly_reports() -> list[sqlite3.Row]:
    """Return all saved weekly report rows, newest week first."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM weekly_team_report ORDER BY week_monday DESC, id ASC"
        ).fetchall()
