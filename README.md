# Absenteeism Hours Tracker

A Streamlit app with two tools, styled in Scotiabank red and white:

1. **Manual Entry** — record approved/disapproved hours, compute metrics, and
   store every entry in a local SQL (SQLite) database.
2. **Weekly Team Report** — pull last week's Attendance from the corporate
   SQL Server database `[Bogota_GBS_NS]`, compute approved / not-approved hours
   by Unit and SubUnit, let the user adjust the approved values, and save it as
   the team's weekly report.

## Features

### Manual Entry

- Two main inputs: **Approved hours** and **Disapproved hours** (entered by the user).
- Extra inputs: employee name and date.
- Calculated metrics:
  - Total hours = approved + disapproved
  - Approval rate (%) = approved / total
  - Absenteeism rate (%) = disapproved / total
- Each submission is saved to a SQLite database (`absenteeism.db`).

### Weekly Team Report

- Detects the signed-in **Windows user** and scopes the data to that leader's team.
- Source: `[Bogota_GBS_NS].[dbo].[vwAttendance]` + `[Bogota_GBS_NS].[dbo].[Holidays]`.
- Range: last full week (Monday to Friday), Colombian holidays excluded, normalized
  by real business days (`@dias`).
- Shows team headcount and locations (from Attendance), per Unit and SubUnit:
  people in unit, attended / approved (Vacation) / not-approved (Medical Leave,
  Personal Day) counts, hours (x8), and weekly normalized values.
- The leader enters **Approved Leave** and **Unplanned** hours per row; counts and
  weekly values recalculate automatically (1 day = 8 hours).
- Saved into the local SQLite table `weekly_team_report` (one report per week per leader).

#### One-time schema mapping

Because `vwAttendance` column names vary, the app includes a **Team & schema setup**
panel:

1. Click **Discover vwAttendance columns** to list the available columns.
2. Pick the **Manager / leader column** (used to filter your team) and, optionally,
   the **Location column**.
3. The **leader identifier value** defaults to your Windows user; change it if your
   manager column stores a different identifier (e.g. an Snumber).

## Requirements

- Python 3.10+
- For the Weekly Team Report: an ODBC driver (e.g. **ODBC Driver 18 for SQL Server**)
  and network access to the SQL Server using **Windows Authentication**.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

The app opens in your browser. The local database file `absenteeism.db` is
created automatically on first run.

## SQL Server configuration (Weekly Team Report)

The Weekly Team Report connects to `[Bogota_GBS_NS]` with Windows Authentication.
Set the server host in any of these ways (highest priority first):

1. Type it in the app's "SQL Server host" field.
2. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and set
   `[sqlserver] server`.
3. Environment variables `SQLSERVER_HOST` / `SQLSERVER_DATABASE`.

## Files

- `app.py` — Streamlit UI, tabs, and calculations.
- `database.py` — local SQLite data-access layer (tables, insert, fetch).
- `sqlserver.py` — SQL Server connection + the weekly Attendance query.
- `requirements.txt` — Python dependencies.

## Switching the local database

The app uses SQLite locally so it works with no server. To move to MySQL or
PostgreSQL, update `get_connection()` in `database.py` and adjust the SQL
placeholders.
