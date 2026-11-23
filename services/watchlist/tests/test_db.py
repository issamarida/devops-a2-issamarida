"""The connection string and the health check against a real SQL Server."""

from contextlib import closing

import pytest

from app.db import DatabaseError, check_database, get_connection, parse_database_url


def test_the_connection_string_is_split_into_its_parts():
    parts = parse_database_url("mssql+pymssql://someone:not-a-password@db.example:1444/stocks")

    assert parts == {
        "server": "db.example",
        "port": "1444",
        "user": "someone",
        "password": "not-a-password",
        "database": "stocks",
    }


def test_the_port_defaults_to_1433_and_escaped_characters_are_decoded():
    parts = parse_database_url("mssql+pymssql://some%40one:p%2Fss%3Aword@db.example/stocks")

    assert parts["port"] == "1433"
    assert parts["user"] == "some@one"
    assert parts["password"] == "p/ss:word"


def test_rows_come_back_as_dicts_and_nothing_is_saved_without_commit(database_url):
    with closing(get_connection(database_url)) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE health_check SET checked_at = '2000-01-01' WHERE id = 1")
        # Closed without commit: SQL Server rolls the change back.

    with closing(get_connection(database_url)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, YEAR(checked_at) AS year FROM health_check")
        row = cursor.fetchone()

    assert row["id"] == 1
    assert row["year"] != 2000


def test_the_health_check_writes_and_reads_its_row(database_url):
    with closing(get_connection(database_url)) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE health_check SET checked_at = '2000-01-01' WHERE id = 1")
        conn.commit()

    check_database(database_url)

    with closing(get_connection(database_url)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT YEAR(checked_at) AS year FROM health_check WHERE id = 1")
        assert cursor.fetchone()["year"] > 2000


def test_the_health_check_fails_when_the_server_cannot_be_reached():
    # Nothing listens on port 1, so the connection is refused at once.
    with pytest.raises(DatabaseError):
        check_database("mssql+pymssql://someone:not-a-password@127.0.0.1:1/nowhere")


def test_the_health_check_fails_when_its_row_is_gone(database_url):
    def run(sql):
        with closing(get_connection(database_url)) as conn:
            conn.cursor().execute(sql)
            conn.commit()

    run("DELETE FROM health_check")
    try:
        with pytest.raises(DatabaseError):
            check_database(database_url)
    finally:
        run("INSERT INTO health_check (id, checked_at) VALUES (1, SYSUTCDATETIME())")
