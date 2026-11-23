-- The first schema of the alerts service, ported from the SQLite schema of
-- Assignment 1. Never edit this file: a change to the schema is a new file.
--
-- ticker is plain text. The watchlist lives in the other service's database,
-- so there is no foreign key to it. owner_id is the account ID from the token.
-- Timestamps stay ISO 8601 text in UTC, as before, so they also sort as text.
-- Every text column is NVARCHAR or NCHAR. The driver sends text as Unicode, and
-- SQL Server cannot use an index on a CHAR column to look up a Unicode value.

CREATE TABLE alert_rules (
    id         INT IDENTITY(1,1) PRIMARY KEY,
    owner_id   INT          NOT NULL DEFAULT 0,
    rule_token NCHAR(32)    NOT NULL,
    ticker     NVARCHAR(10) NOT NULL,
    condition  NVARCHAR(5)  NOT NULL CHECK (condition IN ('above', 'below')),
    threshold  FLOAT        NOT NULL CHECK (threshold > 0),
    is_active  BIT          NOT NULL DEFAULT 1,
    created_at NVARCHAR(32) NOT NULL
);

-- One row per fired rule. ticker, condition and threshold are copied from the
-- rule so the history still reads correctly after the rule is deleted.
CREATE TABLE alert_events (
    id             INT IDENTITY(1,1) PRIMARY KEY,
    owner_id       INT          NOT NULL DEFAULT 0,
    rule_id        INT          NULL REFERENCES alert_rules(id) ON DELETE SET NULL,
    ticker         NVARCHAR(10) NOT NULL,
    condition      NVARCHAR(5)  NOT NULL,
    threshold      FLOAT        NOT NULL,
    observed_price FLOAT        NOT NULL,
    triggered_at   NVARCHAR(32) NOT NULL
);

CREATE INDEX rules_by_owner ON alert_rules (owner_id, is_active);
CREATE INDEX events_by_owner ON alert_events (owner_id, triggered_at);

-- One row that GET /health writes and reads back.
CREATE TABLE health_check (
    id         INT          NOT NULL PRIMARY KEY CHECK (id = 1),
    checked_at DATETIME2(0) NOT NULL
);
INSERT INTO health_check (id, checked_at) VALUES (1, SYSUTCDATETIME());
