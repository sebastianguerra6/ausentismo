# Absenteeism & WFO Tracker

A Streamlit app with two manual-entry modules, styled in Scotiabank red and white.
All data is stored in **SQL Server** (`[Bogota_GBS_NS]`) using **Windows
Authentication (Trusted Connection)**. Writing is gated by **Active Directory
group membership**.

## Modules

### 1. Absenteeism

Inputs:
- Vicepresident
- Unit
- People in unit
- Planned approved leave (people) and Days affected (planned)
- Unplanned approved leave (people) and Days affected (unplanned)
- Comment

Calculation:
- `Total days affected = planned days affected + unplanned days affected`
- `% Unplanned (week) = unplanned days affected / total days affected * 100`
  (0 when there are no affected days)

Records are saved in `dbo.Absenteeism_Records`.

### 2. Work From Office (WFO)

Inputs:
- Expected (how many had to come)
- Actual (how many came)
- Comment

Calculation:
- `Attendance % = actual / expected * 100` (0 when expected is 0)

Records are saved in `dbo.WFO_Records`.

## Authorization (Active Directory group)

- The app detects the Windows user via `os.environ["USERNAME"]` (with
  `USERDOMAIN` for the `DOMAIN\user` form used in `CreatedBy`).
- Because the connection is trusted, SQL Server knows the connected login.
  Before allowing a write, the app runs `SELECT IS_MEMBER('DOMAIN\Group')`.
- If the user is a member of the configured group, saving is enabled; otherwise
  the app stays in read-only mode.
- The group is configurable (not hardcoded): `[auth] write_group` in
  `.streamlit/secrets.toml`, or the environment variable `AD_WRITE_GROUP`.

## Requirements

- Python 3.10+
- An ODBC driver (e.g. **ODBC Driver 18 for SQL Server**).
- Network access to the SQL Server using Windows Authentication.

## Setup

```bash
pip install -r requirements.txt
```

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and set the
SQL Server host and the AD write group.

## Run

```bash
streamlit run app.py
```

## Configuration priority

Server / database / write group are read in this order:

1. The "SQL Server host" field in the app (for the host).
2. `.streamlit/secrets.toml` (`[sqlserver] server`/`database`, `[auth] write_group`).
3. Environment variables `SQLSERVER_HOST` / `SQLSERVER_DATABASE` / `AD_WRITE_GROUP`.

## Files

- `app.py` — Streamlit UI: the two modules, permission banner, calculations.
- `sqlserver.py` — SQL Server data layer: connection, `IS_MEMBER` check, table
  creation, insert/fetch for both modules.
- `requirements.txt` — Python dependencies.

## Database tables

Auto-created if missing (requires table-creation permission):

- `dbo.Absenteeism_Records`
- `dbo.WFO_Records`

Both include `CreatedBy` (Windows user) and `CreatedAt` (server timestamp).
