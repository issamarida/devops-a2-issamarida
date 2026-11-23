"""All SQL for alert_rules and alert_events. Each method opens a connection and closes it when done."""

from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from app.db import get_connection


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RuleLimitReached(ValueError):
    pass


class AlertRepository:
    def __init__(self, database_url, owner_id=0, max_rules=100):
        self.database_url = database_url
        self.owner_id = owner_id
        self.max_rules = max_rules

    def add_rule(self, ticker, condition, threshold) -> int:
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            # The two hints lock this account's rules until commit, so two
            # requests at the same moment cannot both pass the limit.
            cursor.execute(
                "SELECT COUNT(*) AS total FROM alert_rules WITH (UPDLOCK, HOLDLOCK)"
                " WHERE owner_id = %s",
                (self.owner_id,),
            )
            if cursor.fetchone()["total"] >= self.max_rules:
                raise RuleLimitReached(
                    f"You can keep up to {self.max_rules} rules. Delete an old rule first; its history stays."
                )
            cursor.execute(
                "INSERT INTO alert_rules (ticker, condition, threshold, created_at, owner_id, rule_token)"
                " OUTPUT INSERTED.id VALUES (%s, %s, %s, %s, %s, %s)",
                (ticker, condition, threshold, utc_now(), self.owner_id, uuid4().hex),
            )
            new_id = cursor.fetchone()["id"]
            conn.commit()
        return new_id

    def get_rule(self, rule_id) -> dict | None:
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM alert_rules WHERE id = %s AND owner_id = %s",
                (rule_id, self.owner_id),
            )
            return cursor.fetchone()

    def list_rules(self) -> list[dict]:
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM alert_rules WHERE owner_id = %s ORDER BY id",
                (self.owner_id,),
            )
            return cursor.fetchall()

    def list_active_rules(self) -> list[dict]:
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM alert_rules WHERE is_active = 1 AND owner_id = %s ORDER BY id",
                (self.owner_id,),
            )
            return cursor.fetchall()

    def delete_rule(self, rule_id) -> bool:
        """Returns True if a row was deleted. Its events stay, with rule_id set to NULL."""
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM alert_rules WHERE id = %s AND owner_id = %s",
                (rule_id, self.owner_id),
            )
            deleted = cursor.rowcount > 0
            conn.commit()
        return deleted

    def list_events(self) -> list[dict]:
        """Latest 100 events, newest first. Older history remains in the database."""
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT TOP 100 * FROM alert_events WHERE owner_id = %s"
                " ORDER BY triggered_at DESC, id DESC",
                (self.owner_id,),
            )
            return cursor.fetchall()

    def count_events(self) -> int:
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS total FROM alert_events WHERE owner_id = %s",
                (self.owner_id,),
            )
            return cursor.fetchone()["total"]

    def fire_rule(self, rule, observed_price) -> bool:
        """Deactivate the rule and log an event in one transaction.

        Returns False if the rule was already inactive, so a rule only fires once.
        """
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            # The UPDATE locks the row. If two threads try at the same moment,
            # the second one waits, then finds is_active = 0 and changes nothing.
            cursor.execute(
                "UPDATE alert_rules SET is_active = 0 WHERE id = %s AND is_active = 1 "
                "AND owner_id = %s AND rule_token = %s",
                (rule["id"], self.owner_id, rule["rule_token"]),
            )
            if cursor.rowcount == 0:
                return False
            cursor.execute(
                "INSERT INTO alert_events"
                " (rule_id, ticker, condition, threshold, observed_price, triggered_at, owner_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    rule["id"],
                    rule["ticker"],
                    rule["condition"],
                    rule["threshold"],
                    observed_price,
                    utc_now(),
                    self.owner_id,
                ),
            )
            # Both changes are saved here together. If the INSERT fails, the
            # connection closes without a commit and the rule stays active.
            conn.commit()
        return True


def owners_with_active_rules(database_url) -> list[int]:
    """Every account the poller has something to check for."""
    with closing(get_connection(database_url)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT owner_id FROM alert_rules"
            " WHERE is_active = 1 AND owner_id != 0 ORDER BY owner_id"
        )
        return [row["owner_id"] for row in cursor.fetchall()]


def active_rule_tickers(database_url) -> list[str]:
    """Every ticker some active rule is waiting on. These are the prices the poller asks for."""
    with closing(get_connection(database_url)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT ticker FROM alert_rules"
            " WHERE is_active = 1 AND owner_id != 0 ORDER BY ticker"
        )
        return [row["ticker"] for row in cursor.fetchall()]
