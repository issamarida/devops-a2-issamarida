import os
from contextlib import closing

import pytest

from app import MIGRATIONS, create_app
from app.config import load_config
from app.db import get_connection
from app.migrations import apply_migrations
from app.watchlist.repository import WatchlistRepository
from app.watchlist.service import WatchlistService
from openapi_check import watch

# Not a real secret. It only signs tokens inside a test run.
TEST_TOKEN_SECRET = "test-only-token-secret-not-a-real-one"
PASSWORD = "a long test passphrase"
# Emptied before every test that uses the database.
TABLES = ["watchlist_items", "accounts", "rate_limits"]


@pytest.fixture(autouse=True)
def service_environment(monkeypatch):
    """Every test gets a token secret and no Finnhub key.

    DATABASE_URL is unset, so a test that needs the database must ask for the
    database_url fixture. It can never reach a real database by accident.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TOKEN_SECRET", TEST_TOKEN_SECRET)
    monkeypatch.setenv("PRICE_API_KEY", "")  # never use a real key from my shell


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


@pytest.fixture
def watchlist_service(database_url):
    return WatchlistService(WatchlistRepository(database_url))


class FakePrices:
    """Offline prices. Returns whatever price the test sets and records what was asked."""

    def __init__(self):
        self.price = 100.0
        self.asked = []  # one list of tickers per /api/quotes call
        self.looked_up = []  # every ticker get_price was called for

    def get_price(self, ticker):
        self.looked_up.append(ticker)
        return self.price

    def snapshot(self, tickers):
        self.asked.append(list(tickers))
        # The same fields the real LivePrices.snapshot sends.
        quote = {
            "price": self.price, "change": None, "percent": None, "source": "live", "at": 1.0,
            "previous_close": None, "open": None, "high": None, "low": None, "spark": [],
        }
        return {
            "status": "live",
            "server_time": 1.0,
            "quotes": {ticker: dict(quote) for ticker in tickers},
        }


@pytest.fixture
def prices():
    return FakePrices()


@pytest.fixture
def app(prices, database_url):
    app = create_app(load_config(), price_source=prices)
    # Every answer this app gives in a test is compared with openapi.yaml.
    problems = watch(app)
    yield app
    assert not problems, "openapi.yaml does not describe these answers:\n" + "\n".join(problems)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def sign_up(client):
    """sign_up("alice") registers and signs in. Returns the headers that carry the token."""

    def sign_up(username, password=PASSWORD):
        body = {"username": username, "password": password}
        assert client.post("/auth/register", json=body).status_code == 201
        token = client.post("/auth/login", json=body).get_json()["token"]
        return {"Authorization": f"Bearer {token}"}

    return sign_up
