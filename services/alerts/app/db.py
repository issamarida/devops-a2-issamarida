"""Short SQL Server connections and the health check.

Every repository call opens its own connection and closes it when done, so
threads never share one. The connection string comes from DATABASE_URL.
"""

import logging
import time
from contextlib import closing
from urllib.parse import unquote, urlsplit

import pymssql

logger = logging.getLogger(__name__)

# Every error the driver can raise, from a refused login to a broken query.
DatabaseError = pymssql.Error
# The one raised when a row breaks a UNIQUE or CHECK rule.
IntegrityError = pymssql.IntegrityError

# How long to wait for SQL Server to accept the login, and for one query.
LOGIN_TIMEOUT_SECONDS = 15
QUERY_TIMEOUT_SECONDS = 15

# How long to wait between tries while the database is starting. Azure SQL
# serverless pauses after an hour without connections, and the first connection
# wakes it. Resuming takes up to about a minute, and every try fails meanwhile
# (error 40613, or a DB-Lib timeout 20002 or 20003 with pymssql). Together with
# the login timeout of each try, this gives up after at most about four minutes.
STARTUP_WAITS_SECONDS = (2, 4, 8, 15, 15, 15, 15, 15)


def parse_database_url(url) -> dict:
    """Split mssql+pymssql://user:password@host:port/database into its parts.

    Raises ValueError with a message that never contains the password.
    """
    parts = urlsplit(url)
    database = parts.path.lstrip("/")
    try:
        port = parts.port or 1433
    except ValueError:
        port = None
    if (
        parts.scheme != "mssql+pymssql"
        or not parts.hostname
        or not parts.username
        or parts.password is None
        or not database
        or port is None
    ):
        raise ValueError(
            "DATABASE_URL must look like mssql+pymssql://user:password@host:port/database"
        )
    return {
        "server": parts.hostname,
        "port": str(port),
        "user": unquote(parts.username),
        "password": unquote(parts.password),
        "database": database,
    }


def get_connection(database_url):
    """A new connection. Rows come back as dicts and nothing is saved until commit()."""
    return pymssql.connect(
        **parse_database_url(database_url),
        as_dict=True,
        login_timeout=LOGIN_TIMEOUT_SECONDS,
        timeout=QUERY_TIMEOUT_SECONDS,
        # Azure SQL only accepts encrypted connections. The local server
        # supports them too, so the same setting is used everywhere.
        encryption="require",
    )


def check_database(database_url) -> None:
    """Write one row and read it back. Raises DatabaseError if either fails."""
    with closing(get_connection(database_url)) as conn:
        cursor = conn.cursor()
        # The table only ever holds the row with id 1, which the first migration adds.
        cursor.execute("UPDATE health_check SET checked_at = SYSUTCDATETIME() WHERE id = 1")
        cursor.execute("SELECT checked_at FROM health_check WHERE id = 1")
        if cursor.fetchone() is None:
            raise DatabaseError("The health_check row is missing")
        conn.commit()


def wait_for_database(database_url, waits=STARTUP_WAITS_SECONDS, sleep=time.sleep) -> int:
    """Open a connection, trying again while the database starts. Returns the tries it took.

    Every driver error counts as "not ready yet": pymssql can report a stale
    message from an earlier failure, so the text is not a safe guide. A wrong
    password fails the same way, only after the last wait, and the logged
    error names it. The last error is raised, and the service does not start.
    """
    for attempt, wait in enumerate((*waits, None), start=1):
        try:
            with closing(get_connection(database_url)):
                return attempt
        except DatabaseError as error:
            if wait is None:
                raise
            logger.warning(
                "The database did not answer (try %d: %s). Trying again in %d seconds.",
                attempt,
                error,
                wait,
            )
            sleep(wait)
