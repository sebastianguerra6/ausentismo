# Absenteeism & WFO Tracker

A Streamlit app with two manual-entry modules, styled in Scotiabank red and white.
All data is stored in **SQL Server** (`[Bogota_GBS_NS]`) using **Windows
Authentication (Trusted Connection)**. Read/write permissions are enforced by
SQL Server for the connected Windows user; there is no application-level
authorization.

Both modules write to a single table: **`dbo.Attendance_Absenteeism_Report`**.

## Modules

### 1. Absenteeism

Inputs:
- Report week (date)
- Leader
- Location
- Team
- Total Headcount
- Headcount comments (required only when the alignment % is below 90%)
- Number of employees on planned leave and Days Impacted (planned)
- Number of employees on unplanned leave and Days Impacted (unplanned)
- Comments (required only when % Unplanned is above 3%)

Calculations:
- `Total days affected = planned days impacted + unplanned days impacted`
- `% Unplanned (week) = (Days Impacted Unplanned / 5) / (Total Headcount - Days Impacted Planned) * 100`
  (0 when the denominator is not positive)
- `Headcount Alignment % = Total Headcount / official unit headcount * 100`
  — computed from a separate source base matched by unit id. Access to that
  base is pending, so this is left empty (NULL) for now.

### 2. Work From Office (WFO)

Flow:
- Identity fields (report week, Leader, Location, Team, Total Headcount).
- Question: "Did all required employees attend the office on every mandatory day
  during the reported week?"
  - **Yes** → 0 non-compliant employees are reported.
  - **No** → capture how many were non-compliant and up to 5 comments
    (`WFO_Comment1..5`).

## Requirements

- Python 3.10+
- An ODBC driver (e.g. **ODBC Driver 18 for SQL Server**).
- Network access to the SQL Server using Windows Authentication.

## Setup

```bash
pip install -r requirements.txt
```

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and set the
SQL Server host and database.

## Run

```bash
streamlit run app.py
```

## Configuration priority

Server / database are read in this order:

1. The "SQL Server host" field in the app (for the host).
2. `.streamlit/secrets.toml` (`[sqlserver] server` / `database`).
3. Environment variables `SQLSERVER_HOST` / `SQLSERVER_DATABASE`.

## Files

- `app.py` — Streamlit UI: the two modules and calculations.
- `sqlserver.py` — SQL Server data layer: connection, table bootstrap, and
  insert/fetch for both modules.
- `requirements.txt` — Python dependencies.

## Database table

`dbo.Attendance_Absenteeism_Report` is auto-created if missing (requires
table-creation permission). The date column is `Report_Date` (NOT NULL) and is
always stored as the Monday of the reported week.

Absenteeism and WFO are saved as separate rows (separate tabs). An absenteeism
row leaves the WFO columns NULL; a WFO row stores 0 in the required absenteeism
numeric columns. The WFO "Calculate & Save" button is disabled for people/
locations not allowed to report WFO (driven by the by-country headcount source,
access pending).
