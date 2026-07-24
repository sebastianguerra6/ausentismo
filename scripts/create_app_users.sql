-- ============================================================================
-- App users + service account grants for Absenteeism / WFO Tracker
-- Run against [Bogota_GBS_NS] in SSMS (or change the USE below).
-- ============================================================================
-- How access works:
--   - Streamlit runs as a Windows service account (e.g. DOMAIN\svc_ausentismo).
--   - All SQL access uses Trusted Connection as that service account.
--   - People sign in with Scotia ID + app password (dbo.App_Users, bcrypt).
--   - Created_By / HR lookups use the login Scotia ID.
--   - Who can save: App_Users.CanWrite (+ service account must have INSERT).
--
-- Onboard infra (once):
--   1. Create/use service account; run Streamlit as that account.
--   2. GRANT SELECT/INSERT to the service account (see below).
--
-- Onboard a person:
--   1. python scripts/hash_password.py  (hash their app password)
--   2. INSERT into dbo.App_Users (Username = Scotia ID, CanWrite as needed).
-- ============================================================================

USE [Bogota_GBS_NS];
GO

IF OBJECT_ID('dbo.App_Users', 'U') IS NULL
CREATE TABLE dbo.App_Users (
    Id              INT IDENTITY(1,1) PRIMARY KEY,
    Username        VARCHAR(100)  NOT NULL,   -- Scotia ID (without DOMAIN\)
    PasswordHash    VARCHAR(255)  NOT NULL,   -- bcrypt of app password
    DisplayName     VARCHAR(200)  NULL,
    CanWrite        BIT           NOT NULL DEFAULT 1,
    IsActive        BIT           NOT NULL DEFAULT 1,
    CreatedAt       DATETIME      NOT NULL DEFAULT GETDATE(),
    CONSTRAINT UQ_App_Users_Username UNIQUE (Username)
);
GO

-- Existing App_Users tables: add CanWrite if missing
IF OBJECT_ID('dbo.App_Users', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.App_Users', 'CanWrite') IS NULL
    ALTER TABLE dbo.App_Users ADD CanWrite BIT NOT NULL CONSTRAINT DF_App_Users_CanWrite DEFAULT 1;
GO

-- ---------------------------------------------------------------------------
-- Service account (replace DOMAIN\svc_ausentismo with your account)
-- ---------------------------------------------------------------------------
/*
CREATE LOGIN [DOMAIN\svc_ausentismo] FROM WINDOWS;

USE [Bogota_GBS_NS];
CREATE USER [DOMAIN\svc_ausentismo] FOR LOGIN [DOMAIN\svc_ausentismo];
GRANT SELECT, INSERT ON dbo.Attendance_Absenteeism_Report TO [DOMAIN\svc_ausentismo];
GRANT SELECT ON dbo.App_Users TO [DOMAIN\svc_ausentismo];
-- Optional if the app creates/alters tables at startup:
-- GRANT ALTER, CREATE TABLE ON SCHEMA::dbo TO [DOMAIN\svc_ausentismo];

USE [EDDU_ID];
CREATE USER [DOMAIN\svc_ausentismo] FOR LOGIN [DOMAIN\svc_ausentismo];
GRANT SELECT ON dbo.GlobalWorkforceHR TO [DOMAIN\svc_ausentismo];
*/

-- ---------------------------------------------------------------------------
-- Example app user (Scotia ID + app password hash)
-- ---------------------------------------------------------------------------
/*
USE [Bogota_GBS_NS];
INSERT INTO dbo.App_Users (Username, PasswordHash, DisplayName, CanWrite, IsActive)
VALUES ('s123456', '<bcrypt hash from scripts/hash_password.py>', 'Example User', 1, 1);

-- Read-only app user:
-- UPDATE dbo.App_Users SET CanWrite = 0 WHERE Username = 's123456';
*/
GO
