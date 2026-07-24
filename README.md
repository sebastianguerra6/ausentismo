# Absenteeism & WFO Tracker

A Streamlit app styled in Scotiabank red and white. Data is stored in **SQL
Server** (`[Bogota_GBS_NS]`).

## Authentication (VM / service account)

Users sign in with **Scotia ID + app password** (`dbo.App_Users`):

1. Credentials are checked against `App_Users` (bcrypt, `IsActive`).
2. SQL Server uses **Trusted Connection** as the Windows **service account**
   that runs Streamlit on the VM (no per-user Windows password / impersonation).
3. Write access = `App_Users.CanWrite` **and** the service account has `INSERT`.
4. Scotia ID → `Created_By`, session context `AppUser`, and `GlobalWorkforceHR`
   (Canada → WFO required; team headcount for alignment %).

Grant `SELECT`/`INSERT` to the service account only. See
`scripts/create_app_users.sql`.

## Modules

Both modules write to **`dbo.Attendance_Absenteeism_Report`** as separate rows.

### 1. Absenteeism

Inputs: Report date (Monday), Leader, Location, Team, Total Headcount, planned /
unplanned leave counts and days impacted, comments when required.

### 2. Work From Office (WFO)

Shown and **mandatory** when `Country Name` in HR is **Canada**.

## Requirements

- Windows OS (service account to run Streamlit)
- Python 3.10+
- ODBC Driver 18 for SQL Server (or 17)
- Service account with SQL permissions (not each end-user Windows login)

## Setup

```bash
pip install -r requirements.txt
```

1. Run Streamlit as the service account (Windows service / Task Scheduler / etc.).
2. Run `scripts/create_app_users.sql` and grant that service account.
3. Hash an app password and insert into `App_Users`:

```bash
python scripts/hash_password.py
```

4. Copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` and set
   the SQL Server host/database if needed.

## Run

```bash
streamlit run app.py
```

## Files

- `app.py` — Login UI + weekly report form.
- `sqlserver.py` — Trusted Connection (service account) + App_Users auth + HR/inserts.
- `scripts/create_app_users.sql` — `App_Users` DDL + service account grants.
- `scripts/hash_password.py` — bcrypt helper for app passwords.
