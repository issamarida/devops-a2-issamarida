"""AllOwners: what the poller runs for every account."""

import logging

from app.alerts.checker import AllOwners
from app.alerts.repository import (
    AlertRepository,
    active_rule_tickers,
    owners_with_active_rules,
)
from app.alerts.service import AlertService
from app.alerts.watchlist_client import RemoteWatchlist, Snapshot
from app.ports import WatchlistUnavailable
from conftest import ANSWER


def make_checker(database_url, fetch):
    made = []  # the tickers each new snapshot was asked for

    def new_snapshot(tickers=()):
        made.append(list(tickers))
        return Snapshot(fetch)

    def service_for(owner_id, snapshot):
        watchlist = RemoteWatchlist(snapshot, owner_id)
        return AlertService(AlertRepository(database_url, owner_id), watchlist, watchlist)

    return AllOwners(database_url, new_snapshot, service_for), made


def test_owners_and_tickers_come_from_active_rules_only(database_url):
    AlertRepository(database_url, 8).add_rule("TSLA", "above", 250)
    AlertRepository(database_url, 7).add_rule("MSFT", "above", 1)
    AlertRepository(database_url, 7).add_rule("AAPL", "above", 190)
    AlertRepository(database_url, 7).add_rule("AAPL", "below", 100)
    # A fired rule and old data without an owner are not checked.
    fired = AlertRepository(database_url, 9)
    fired.fire_rule(fired.get_rule(fired.add_rule("NFLX", "above", 1)), 2.0)
    AlertRepository(database_url, 0).add_rule("IBM", "above", 1)

    assert owners_with_active_rules(database_url) == [7, 8]
    assert active_rule_tickers(database_url) == ["AAPL", "MSFT", "TSLA"]


def test_one_snapshot_serves_every_account(database_url):
    AlertRepository(database_url, 7).add_rule("AAPL", "above", 190)
    AlertRepository(database_url, 8).add_rule("TSLA", "above", 250)
    fetches = []
    checker, made = make_checker(database_url, lambda: fetches.append(1) or ANSWER)

    fired = checker.evaluate_all()

    assert sorted(rule["ticker"] for rule in fired) == ["AAPL", "TSLA"]
    assert made == [["AAPL", "TSLA"]]
    assert len(fetches) == 1


def test_nothing_is_fetched_when_no_rule_is_active(database_url):
    checker, made = make_checker(database_url, lambda: ANSWER)

    assert checker.evaluate_all() == []
    assert made == []


def test_an_unreachable_watchlist_skips_the_run_and_keeps_every_rule(database_url, caplog):
    AlertRepository(database_url, 7).add_rule("AAPL", "above", 190)
    AlertRepository(database_url, 8).add_rule("TSLA", "above", 250)

    def fetch():
        raise WatchlistUnavailable("down")

    checker, _ = make_checker(database_url, fetch)
    caplog.set_level(logging.WARNING)

    assert checker.evaluate_all() == []

    assert [r.getMessage() for r in caplog.records] == ["Watchlist unavailable, skipping this check"]
    assert owners_with_active_rules(database_url) == [7, 8]


def test_one_accounts_failure_does_not_stop_the_others(database_url, caplog):
    AlertRepository(database_url, 7).add_rule("AAPL", "above", 190)
    AlertRepository(database_url, 8).add_rule("TSLA", "above", 250)
    checker, _ = make_checker(database_url, lambda: ANSWER)
    working = checker.service_for

    def service_for(owner_id, snapshot):
        if owner_id == 7:
            raise RuntimeError("broken for account 7")
        return working(owner_id, snapshot)

    checker.service_for = service_for
    caplog.set_level(logging.ERROR)

    fired = checker.evaluate_all()

    assert [rule["ticker"] for rule in fired] == ["TSLA"]
    assert "Alert check failed for an account" in caplog.text
