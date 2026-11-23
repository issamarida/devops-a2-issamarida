"""All SQL for watchlist_items. Each method opens a connection and closes it when done."""

from contextlib import closing
from dataclasses import dataclass

from app.db import get_connection


@dataclass
class WatchlistItem:
    id: int
    ticker: str
    name: str
    notes: str
    added_at: str
    owner_id: int = 0


class WatchlistLimitReached(ValueError):
    pass


class WatchlistRepository:
    def __init__(self, database_url, owner_id=0, max_items=30):
        self.database_url = database_url
        self.owner_id = owner_id
        self.max_items = max_items

    def add(self, ticker, name, notes, added_at) -> WatchlistItem:
        """Raises IntegrityError if the ticker is already there (UNIQUE constraint)."""
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            # The two hints lock this account's rows until commit, so two
            # requests at the same moment cannot both pass the limit.
            cursor.execute(
                "SELECT COUNT(*) AS total FROM watchlist_items WITH (UPDLOCK, HOLDLOCK)"
                " WHERE owner_id = %s",
                (self.owner_id,),
            )
            if cursor.fetchone()["total"] >= self.max_items:
                raise WatchlistLimitReached(
                    f"Your watchlist is limited to {self.max_items} assets. Remove an asset first."
                )
            cursor.execute(
                "INSERT INTO watchlist_items (ticker, name, notes, added_at, owner_id)"
                " OUTPUT INSERTED.id VALUES (%s, %s, %s, %s, %s)",
                (ticker, name, notes, added_at, self.owner_id),
            )
            new_id = cursor.fetchone()["id"]
            conn.commit()
        return WatchlistItem(new_id, ticker, name, notes, added_at, self.owner_id)

    def get_by_ticker(self, ticker) -> WatchlistItem | None:
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM watchlist_items WHERE ticker = %s AND owner_id = %s",
                (ticker, self.owner_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return WatchlistItem(**row)

    def list_all(self) -> list[WatchlistItem]:
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM watchlist_items WHERE owner_id = %s ORDER BY ticker",
                (self.owner_id,),
            )
            rows = cursor.fetchall()
        return [WatchlistItem(**row) for row in rows]

    def delete(self, ticker) -> bool:
        """Returns True if a row was deleted."""
        with closing(get_connection(self.database_url)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM watchlist_items WHERE ticker = %s AND owner_id = %s",
                (ticker, self.owner_id),
            )
            deleted = cursor.rowcount > 0
            conn.commit()
        return deleted

    def exists(self, ticker) -> bool:
        return self.get_by_ticker(ticker) is not None


def most_watched_tickers(database_url) -> list[str]:
    """Every ticker an account watches, most watched first. Owner 0 is legacy data nobody sees."""
    with closing(get_connection(database_url)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT ticker FROM watchlist_items WHERE owner_id != 0
            GROUP BY ticker ORDER BY COUNT(*) DESC, ticker"""
        )
        rows = cursor.fetchall()
    return [row["ticker"] for row in rows]


def tickers_by_owner(database_url) -> dict[str, list[str]]:
    """Each account's tickers, for the snapshot the alerts service asks for.

    The keys are account IDs as text because JSON object keys are always text.
    """
    with closing(get_connection(database_url)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT owner_id, ticker FROM watchlist_items WHERE owner_id != 0"
            " ORDER BY owner_id, ticker"
        )
        rows = cursor.fetchall()
    owners = {}
    for row in rows:
        owners.setdefault(str(row["owner_id"]), []).append(row["ticker"])
    return owners
