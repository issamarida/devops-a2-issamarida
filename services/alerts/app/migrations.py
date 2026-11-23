"""Applies the SQL files in migrations/ in order, each exactly once.

The schema only ever changes through a new numbered file, for example
002_add_notes.sql. The schema_migrations table records every file that has
run, so a restart or a redeploy runs only what is new and keeps the data.
"""

import hashlib
import logging
import re
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from app.db import get_connection

logger = logging.getLogger(__name__)

FILE_NAME = re.compile(r"(\d{3})_[a-z0-9_]+\.sql")
# A line that holds only GO ends a batch, as in sqlcmd. SQL Server checks a
# whole batch before it runs it, so a statement that uses a new column must
# come after a GO that follows the ALTER TABLE.
BATCH_SEPARATOR = re.compile(r"^\s*GO\s*$", re.MULTILINE | re.IGNORECASE)

CREATE_RECORD_TABLE = """
IF OBJECT_ID('schema_migrations') IS NULL
    CREATE TABLE schema_migrations (
        version    INT           NOT NULL PRIMARY KEY,
        name       NVARCHAR(200) NOT NULL,
        checksum   NCHAR(64)     NOT NULL,
        applied_at DATETIME2(0)  NOT NULL DEFAULT SYSUTCDATETIME()
    )
"""

# Held until the transaction ends. If two copies of the service start at the
# same moment, the second one waits here and then finds the work already done.
TAKE_LOCK = """
DECLARE @result INT;
EXEC @result = sp_getapplock @Resource = 'schema_migrations',
    @LockMode = 'Exclusive', @LockOwner = 'Transaction', @LockTimeout = 60000;
IF @result < 0 THROW 50000, 'Another process is still running migrations.', 1;
"""


class MigrationError(Exception):
    pass


@dataclass
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def read_migrations(folder) -> list[Migration]:
    """Every migration file in the folder, lowest number first."""
    migrations = []
    for path in sorted(Path(folder).glob("*.sql")):
        match = FILE_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(
                f"{path.name} is not named like 001_what_it_does.sql"
            )
        # Windows line endings must not change the checksum.
        sql = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        migrations.append(Migration(int(match.group(1)), path.name, sql, checksum))
    versions = [migration.version for migration in migrations]
    if len(versions) != len(set(versions)):
        raise MigrationError("Two migration files have the same number")
    return migrations


def run_file(cursor, migration) -> None:
    """Run one file and record it. The caller commits both together."""
    for batch in BATCH_SEPARATOR.split(migration.sql):
        if batch.strip():
            cursor.execute(batch)
    cursor.execute(
        "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
        (migration.version, migration.name, migration.checksum),
    )


def apply_migrations(database_url, folder) -> list[str]:
    """Run the migrations this database has not seen. Returns the names it ran.

    Each file runs in one transaction together with its row in
    schema_migrations. If a statement fails, SQL Server undoes the whole file,
    nothing is recorded and the error stops the service from starting.
    """
    applied = []
    with closing(get_connection(database_url)) as conn:
        cursor = conn.cursor()
        cursor.execute(TAKE_LOCK)
        cursor.execute(CREATE_RECORD_TABLE)
        conn.commit()
        for migration in read_migrations(folder):
            try:
                cursor.execute(TAKE_LOCK)
                cursor.execute(
                    "SELECT checksum FROM schema_migrations WHERE version = %s",
                    (migration.version,),
                )
                recorded = cursor.fetchone()
                if recorded is None:
                    run_file(cursor, migration)
                    applied.append(migration.name)
                    logger.info("Applied migration %s", migration.name)
                elif recorded["checksum"] != migration.checksum:
                    raise MigrationError(
                        f"{migration.name} was changed after it ran on this database. "
                        "Put the file back as it was and write the change as a new migration."
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    if not applied:
        logger.info("No new migration to apply")
    return applied
