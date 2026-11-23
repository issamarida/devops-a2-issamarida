"""The migration runner against a real SQL Server (brief section 4)."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

from app import MIGRATIONS
from app.db import DatabaseError, get_connection
from app.migrations import MigrationError, apply_migrations, read_migrations

CREATE_THINGS = "CREATE TABLE things (id INT IDENTITY(1,1) PRIMARY KEY, name NVARCHAR(50) NOT NULL);"
# The UPDATE uses the new column, so it must come after a GO.
ADD_LABEL = """ALTER TABLE things ADD label NVARCHAR(20) NOT NULL DEFAULT 'none';
GO
UPDATE things SET label = 'old ' + name;
"""


def query(url, sql):
    with closing(get_connection(url)) as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall() if cursor.description else []
        conn.commit()
    return rows


def recorded(url):
    rows = query(url, "SELECT version, name FROM schema_migrations ORDER BY version")
    return [(row["version"], row["name"]) for row in rows]


def table_exists(url, name):
    return query(url, f"SELECT OBJECT_ID('{name}') AS id")[0]["id"] is not None


@pytest.fixture
def folder(tmp_path):
    """An empty migrations folder the test fills with its own files."""
    return tmp_path


def test_this_service_s_migrations_build_its_schema_from_an_empty_database(empty_database):
    names = [path.name for path in sorted(MIGRATIONS.glob("*.sql"))]

    assert names[0] == "001_init.sql"
    assert apply_migrations(empty_database, MIGRATIONS) == names
    assert [name for _, name in recorded(empty_database)] == names
    assert table_exists(empty_database, "health_check")


def test_a_second_start_applies_nothing(empty_database):
    apply_migrations(empty_database, MIGRATIONS)
    before = recorded(empty_database)

    assert apply_migrations(empty_database, MIGRATIONS) == []
    assert recorded(empty_database) == before


def test_files_run_in_number_order(empty_database, folder):
    # 002 only works after 001. A third file proves the order is by number.
    (folder / "003_rename_label.sql").write_text("EXEC sp_rename 'things.label', 'tag', 'COLUMN';")
    (folder / "002_add_label.sql").write_text(ADD_LABEL)
    (folder / "001_create_things.sql").write_text(CREATE_THINGS)

    applied = apply_migrations(empty_database, folder)

    assert applied == ["001_create_things.sql", "002_add_label.sql", "003_rename_label.sql"]
    assert recorded(empty_database) == [
        (1, "001_create_things.sql"),
        (2, "002_add_label.sql"),
        (3, "003_rename_label.sql"),
    ]


def test_a_new_file_changes_a_table_that_holds_data_and_keeps_every_row(empty_database, folder):
    (folder / "001_create_things.sql").write_text(CREATE_THINGS)
    apply_migrations(empty_database, folder)
    query(empty_database, "INSERT INTO things (name) VALUES ('first'), ('second')")

    (folder / "002_add_label.sql").write_text(ADD_LABEL)

    # Only the new file runs. The rows are still there and got the new column filled.
    assert apply_migrations(empty_database, folder) == ["002_add_label.sql"]
    assert query(empty_database, "SELECT id, name, label FROM things ORDER BY id") == [
        {"id": 1, "name": "first", "label": "old first"},
        {"id": 2, "name": "second", "label": "old second"},
    ]


def test_a_failing_file_leaves_no_trace_and_can_be_fixed(empty_database, folder):
    (folder / "001_create_things.sql").write_text(CREATE_THINGS)
    apply_migrations(empty_database, folder)
    broken = "CREATE TABLE half_done (id INT PRIMARY KEY);\nGO\nINSERT INTO no_such_table VALUES (1);"
    (folder / "002_broken.sql").write_text(broken)

    with pytest.raises(DatabaseError):
        apply_migrations(empty_database, folder)

    # SQL Server undid the CREATE TABLE as well, and the file is not recorded.
    assert not table_exists(empty_database, "half_done")
    assert recorded(empty_database) == [(1, "001_create_things.sql")]

    # It never ran, so it may still be corrected in place.
    (folder / "002_broken.sql").write_text("CREATE TABLE half_done (id INT PRIMARY KEY);")
    assert apply_migrations(empty_database, folder) == ["002_broken.sql"]
    assert table_exists(empty_database, "half_done")


def test_an_applied_file_that_was_edited_stops_the_start(empty_database, folder):
    (folder / "001_create_things.sql").write_text(CREATE_THINGS)
    apply_migrations(empty_database, folder)

    (folder / "001_create_things.sql").write_text(CREATE_THINGS + "\n-- a later edit")

    with pytest.raises(MigrationError, match="001_create_things.sql was changed after it ran"):
        apply_migrations(empty_database, folder)


def test_windows_line_endings_are_not_an_edit(empty_database, folder):
    (folder / "001_create_things.sql").write_bytes(b"CREATE TABLE things (\n  id INT PRIMARY KEY\n);\n")
    apply_migrations(empty_database, folder)

    (folder / "001_create_things.sql").write_bytes(b"CREATE TABLE things (\r\n  id INT PRIMARY KEY\r\n);\r\n")

    assert apply_migrations(empty_database, folder) == []


@pytest.mark.parametrize("name", ["init.sql", "1_init.sql", "001-init.sql", "001_Init.sql"])
def test_a_file_without_a_three_digit_number_is_refused(folder, name):
    (folder / name).write_text(CREATE_THINGS)

    with pytest.raises(MigrationError, match="is not named like"):
        read_migrations(folder)


def test_two_files_with_the_same_number_are_refused(folder):
    (folder / "001_create_things.sql").write_text(CREATE_THINGS)
    (folder / "001_create_others.sql").write_text("CREATE TABLE others (id INT PRIMARY KEY);")

    with pytest.raises(MigrationError, match="same number"):
        read_migrations(folder)


def test_two_copies_starting_at_once_apply_each_file_once(empty_database, folder):
    (folder / "001_create_things.sql").write_text(CREATE_THINGS)
    (folder / "002_add_label.sql").write_text(ADD_LABEL)
    barrier = Barrier(2)

    def start():
        barrier.wait()
        return apply_migrations(empty_database, folder)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(start), pool.submit(start)]]

    # Neither copy failed, and together they applied each file exactly once.
    assert sorted(results[0] + results[1]) == ["001_create_things.sql", "002_add_label.sql"]
    assert len(recorded(empty_database)) == 2


def test_the_log_names_every_file_it_applies_and_says_when_there_is_none(
    empty_database, folder, caplog
):
    (folder / "001_create_things.sql").write_text(CREATE_THINGS)

    with caplog.at_level("INFO", logger="app.migrations"):
        apply_migrations(empty_database, folder)
        apply_migrations(empty_database, folder)

    assert [record.getMessage() for record in caplog.records] == [
        "Applied migration 001_create_things.sql",
        "No new migration to apply",
    ]
