import json
import os
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest

from app import MIGRATIONS, create_app
from app.config import load_config
from app.db import get_connection
from app.migrations import apply_migrations
from openapi_check import watch

# Not a real secret. It only signs tokens inside a test run.
TEST_TOKEN_SECRET = "test-only-token-secret-not-a-real-one"
# Emptied before every test that uses the database. Events first: they point at rules.
TABLES = ["alert_events", "alert_rules"]

ANSWER = {
    "owners": {"7": ["AAPL", "MSFT"], "8": ["TSLA"]},
    "prices": {"AAPL": 200.0, "MSFT": None, "TSLA": 300},
}


@pytest.fixture(autouse=True)
def service_environment(monkeypatch):
    """Every test gets a token secret.

    DATABASE_URL is unset, so a test that needs the database must ask for the
    database_url fixture. It can never reach a real database by accident.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TOKEN_SECRET", TEST_TOKEN_SECRET)


def empty_tables(url):
    """Delete every row and start the IDs at 1 again."""
    with closing(get_connection(url)) as conn:
        cursor = conn.cursor()
        for table in TABLES:
            cursor.execute(f"DELETE FROM {table}")
            # A table that never held a row is left alone: RESEED would make its first ID 0.
            cursor.execute(
                f"""IF EXISTS (SELECT 1 FROM sys.identity_columns
                    WHERE object_id = OBJECT_ID('{table}') AND last_value IS NOT NULL)
                    DBCC CHECKIDENT ('{table}', RESEED, 0) WITH NO_INFOMSGS"""
            )
        conn.commit()


@pytest.fixture
def database_url(monkeypatch):
    """The test database with the migrations applied and every table empty.

    It is also what load_config() reads, so the app in a test uses it too.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail(
            "This test needs SQL Server. Start the test database with "
            "`sh db/test-db.sh up` and set TEST_DATABASE_URL to the output of "
            "`sh db/test-db.sh url SERVICE`."
        )
    apply_migrations(url, MIGRATIONS)
    empty_tables(url)
    monkeypatch.setenv("DATABASE_URL", url)
    return url


def drop_every_table(url):
    with closing(get_connection(url)) as conn:
        cursor = conn.cursor()
        # Foreign keys first, so the order of the tables does not matter.
        cursor.execute(
            "SELECT OBJECT_NAME(parent_object_id) AS table_name, name FROM sys.foreign_keys"
        )
        for key in cursor.fetchall():
            cursor.execute(f"ALTER TABLE [{key['table_name']}] DROP CONSTRAINT [{key['name']}]")
        cursor.execute("SELECT name FROM sys.tables")
        for table in cursor.fetchall():
            cursor.execute(f"DROP TABLE [{table['name']}]")
        conn.commit()


@pytest.fixture
def empty_database(database_url):
    """A database with no tables at all, for the tests of the migration runner.

    The tables are dropped again afterwards, so the next test that asks for
    database_url gets the real schema rebuilt from the real migrations.
    """
    drop_every_table(database_url)
    yield database_url
    drop_every_table(database_url)


def user_headers(account_id, username="investor", secret=TEST_TOKEN_SECRET, lifetime=3600):
    """A token signed the way the watchlist service signs one at sign in."""
    now = int(time.time())
    claims = {"sub": str(account_id), "name": username, "iat": now, "exp": now + lifetime}
    return {"Authorization": "Bearer " + jwt.encode(claims, secret, algorithm="HS256")}


class StubWatchlist:
    """Stands in for the watchlist service. A real HTTP server on a free localhost port.

    Like the real one it only answers the alerts service: the request must carry
    a token with the snapshot scope, signed with the shared secret.
    """

    def __init__(self):
        self.answer = ANSWER
        self.status = 200
        self.delay_seconds = 0.0
        self.paths = []  # the path of every request it received
        self.asked = []  # the tickers each request asked prices for
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urlsplit(self.path)
                stub.paths.append(url.path)
                stub.asked.append(parse_qs(url.query).get("tickers", [""])[0])
                time.sleep(stub.delay_seconds)
                if url.path != "/internal/snapshot":
                    status, body = 404, {}
                elif not stub.is_alerts_service(self.headers.get("Authorization", "")):
                    status, body = 401, {"error": "This endpoint is for the alerts service."}
                else:
                    status, body = stub.status, stub.answer
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass  # the caller timed out and hung up, which is the point

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        # A short poll interval makes stop() return quickly.
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()

    @staticmethod
    def is_alerts_service(header) -> bool:
        try:
            claims = jwt.decode(
                header.removeprefix("Bearer "), TEST_TOKEN_SECRET, algorithms=["HS256"]
            )
        except jwt.InvalidTokenError:
            return False
        return claims.get("scope") == "snapshot"

    def stop(self):
        """Like docker compose stop watchlist: the port stops answering."""
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    stub = StubWatchlist()
    yield stub
    stub.stop()


@pytest.fixture
def app(stub, monkeypatch, database_url):
    """The service with the stub in place of the watchlist and a short timeout."""
    monkeypatch.setenv("WATCHLIST_URL", stub.url)
    monkeypatch.setenv("WATCHLIST_TIMEOUT_SECONDS", "0.3")
    app = create_app(load_config())
    # Every answer this app gives in a test is compared with openapi.yaml.
    problems = watch(app)
    yield app
    assert not problems, "openapi.yaml does not describe these answers:\n" + "\n".join(problems)


@pytest.fixture
def client(app):
    return app.test_client()
