import sqlite3

import pytest


@pytest.fixture
def conn(tmp_path):
    import db

    connection = db.get_connection(str(tmp_path / "pantry.db"))
    db.init_db(connection)
    yield connection
    connection.close()


def test_init_db_migrates_table_missing_acquired_date_and_shelf_life(tmp_path):
    import db

    db_path = str(tmp_path / "old.db")
    old_conn = sqlite3.connect(db_path)
    old_conn.execute(
        """
        CREATE TABLE pantry_items (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT,
            location TEXT,
            updated_at TEXT,
            expiry_date TEXT,
            storage TEXT NOT NULL DEFAULT 'pantry'
        )
        """
    )
    old_conn.execute(
        "INSERT INTO pantry_items (name, quantity, unit, location, updated_at) "
        "VALUES ('Rice', 2, 'bags', 'shelf', '2026-01-01T00:00:00+00:00')"
    )
    old_conn.commit()
    old_conn.close()

    conn = db.get_connection(db_path)
    db.init_db(conn)

    item = db.list_items(conn)[0]
    assert item["acquired_date"] is None
    assert item["shelf_life_days"] is None
    conn.close()


def test_update_item_sets_quantity_unit_location(conn):
    import db

    item_id = db.add_item(conn, name="Rice", quantity=1, unit="", location="")

    db.update_item(
        conn,
        item_id,
        quantity=5,
        unit="bags",
        location="pantry shelf 2",
        expiry_date=None,
        acquired_date=None,
        shelf_life_days=None,
    )

    item = db.list_items(conn)[0]
    assert item["quantity"] == 5
    assert item["unit"] == "bags"
    assert item["location"] == "pantry shelf 2"


def test_update_item_sets_acquired_date_and_shelf_life(conn):
    import db

    item_id = db.add_item(conn, name="Cumin", quantity=1, unit="jar", location="")

    db.update_item(
        conn,
        item_id,
        quantity=1,
        unit="jar",
        location="",
        expiry_date=None,
        acquired_date="2024-01-01",
        shelf_life_days=730,
    )

    item = db.list_items(conn)[0]
    assert item["acquired_date"] == "2024-01-01"
    assert item["shelf_life_days"] == 730


def test_add_item_accepts_acquired_date_and_shelf_life(conn):
    import db

    db.add_item(
        conn,
        name="Peas",
        quantity=1,
        unit="bag",
        location="",
        storage="freezer",
        acquired_date="2026-01-01",
        shelf_life_days=270,
    )

    item = db.list_items(conn)[0]
    assert item["acquired_date"] == "2026-01-01"
    assert item["shelf_life_days"] == 270


def test_update_item_can_clear_expiry_date(conn):
    import db

    item_id = db.add_item(conn, name="Rice", quantity=1, unit="", location="", expiry_date="2026-01-01")

    db.update_item(
        conn,
        item_id,
        quantity=1,
        unit="",
        location="",
        expiry_date=None,
        acquired_date=None,
        shelf_life_days=None,
    )

    assert db.list_items(conn)[0]["expiry_date"] is None
