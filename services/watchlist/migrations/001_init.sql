-- The first schema of the watchlist service, ported from the SQLite schema of
-- Assignment 1. Never edit this file: a change to the schema is a new file.
--
-- Timestamps stay ISO 8601 text in UTC, as before, so they also sort as text.
-- Every text column is NVARCHAR or NCHAR. The driver sends text as Unicode, and
-- SQL Server cannot use an index on a CHAR column to look up a Unicode value.

CREATE TABLE accounts (
    id            INT IDENTITY(1,1) PRIMARY KEY,
    username      NVARCHAR(30)  NOT NULL UNIQUE,
    password_hash NVARCHAR(255) NOT NULL
);

-- Sign-in and registration attempts. [key] is the SHA-256 of who is trying.
CREATE TABLE rate_limits (
    [key]    NCHAR(64) NOT NULL PRIMARY KEY,
    attempts INT       NOT NULL,
    reset_at FLOAT     NOT NULL
);

-- owner_id is the account ID. An account can track a ticker once.
CREATE TABLE watchlist_items (
    id       INT IDENTITY(1,1) PRIMARY KEY,
    owner_id INT           NOT NULL DEFAULT 0,
    ticker   NVARCHAR(10)  NOT NULL,
    name     NVARCHAR(100) NOT NULL,
    notes    NVARCHAR(500) NOT NULL DEFAULT '',
    added_at NVARCHAR(32)  NOT NULL,
    CONSTRAINT watchlist_one_ticker_per_owner UNIQUE (owner_id, ticker)
);

-- One row that GET /health writes and reads back.
CREATE TABLE health_check (
    id         INT          NOT NULL PRIMARY KEY CHECK (id = 1),
    checked_at DATETIME2(0) NOT NULL
);
INSERT INTO health_check (id, checked_at) VALUES (1, SYSUTCDATETIME());
