import pytest

from app import MIGRATIONS
from app.db import IntegrityError
from app.migrations import apply_migrations
from app.watchlist.repository import WatchlistRepository


@pytest.fixture
def repo(database_url):
    return WatchlistRepository(database_url)


def test_add_returns_item_with_new_id(repo):
    item = repo.add("AAPL", "Apple", "", "2026-10-01T09:00:00+00:00")

    assert item.id is not None
    assert repo.get_by_ticker("AAPL") == item


def test_add_duplicate_raises_integrity_error(repo):
    repo.add("AAPL", "Apple", "", "2026-10-01T09:00:00+00:00")

    with pytest.raises(IntegrityError):
        repo.add("AAPL", "Apple again", "", "2026-10-01T09:01:00+00:00")


def test_get_by_ticker_returns_none_when_missing(repo):
    assert repo.get_by_ticker("NOPE") is None
    assert repo.exists("NOPE") is False


def test_list_all_is_sorted_by_ticker(repo):
    for ticker in ["MSFT", "AAPL", "BRK.B"]:
        repo.add(ticker, ticker, "", "2026-10-01T09:00:00+00:00")

    assert [item.ticker for item in repo.list_all()] == ["AAPL", "BRK.B", "MSFT"]


def test_sql_server_compares_tickers_without_case(repo):
    # SQLite told "aapl" and "AAPL" apart. SQL Server's default collation does
    # not, so the same ticker can never be stored twice in two spellings.
    # WatchlistService still uppercases first, so what is stored is upper case.
    repo.add("AAPL", "Apple", "", "2026-10-01T09:00:00+00:00")

    assert repo.exists("aapl") is True
    with pytest.raises(IntegrityError):
        repo.add("aapl", "Apple in lower case", "", "2026-10-01T09:01:00+00:00")
    assert [item.ticker for item in repo.list_all()] == ["AAPL"]


def test_delete_removes_only_that_ticker(repo):
    repo.add("AAPL", "Apple", "", "2026-10-01T09:00:00+00:00")
    repo.add("MSFT", "Microsoft", "", "2026-10-01T09:00:00+00:00")

    assert repo.delete("AAPL") is True
    assert [item.ticker for item in repo.list_all()] == ["MSFT"]


def test_applying_the_migrations_again_keeps_existing_rows(repo, database_url):
    repo.add("AAPL", "Apple", "", "2026-10-01T09:00:00+00:00")

    assert apply_migrations(database_url, MIGRATIONS) == []

    assert repo.exists("AAPL") is True


def test_same_ticker_is_private_to_each_owner(database_url):
    alice = WatchlistRepository(database_url, 1)
    bob = WatchlistRepository(database_url, 2)
    alice.add("AAPL", "Apple", "Private note", "2026-10-03")
    assert bob.list_all() == []
    assert bob.get_by_ticker("AAPL") is None
    assert bob.delete("AAPL") is False
    bob.add("AAPL", "Apple", "Other note", "2026-10-03")
    assert bob.get_by_ticker("AAPL").notes == "Other note"
    assert alice.get_by_ticker("AAPL").notes == "Private note"


def test_two_requests_at_the_same_moment_cannot_both_pass_the_limit(database_url):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.watchlist.repository import WatchlistLimitReached

    repo = WatchlistRepository(database_url, owner_id=1, max_items=1)
    barrier = Barrier(2)

    def add(ticker):
        barrier.wait()
        try:
            repo.add(ticker, ticker, "", "2026-10-01T09:00:00+00:00")
            return "added"
        except WatchlistLimitReached:
            return "limit reached"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(add, ["AAPL", "MSFT"]))

    assert results == ["added", "limit reached"]
    assert len(repo.list_all()) == 1
