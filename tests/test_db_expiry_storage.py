import sqlite3

import pytest


@pytest.fixture
def conn(tmp_path):
    import db

    connection = db.get_connection(str(tmp_path / "pantry.db"))
    db.init_db(connection)
    yield connection
    connection.close()


def test_init_db_migrates_pre_existing_table_without_data_loss(tmp_path):
    import db

    db_path = str(tmp_path / "old.db")
    # Simulate a database created before expiry_date/storage existed.
    old_conn = sqlite3.connect(db_path)
    old_conn.execute(
        """
        CREATE TABLE pantry_items (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT,
            location TEXT,
            updated_at TEXT
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

    items = db.list_items(conn)
    assert len(items) == 1
    assert items[0]["name"] == "Rice"
    assert items[0]["storage"] == "pantry"
    assert items[0]["expiry_date"] is None
    conn.close()


def test_add_item_defaults_storage_to_pantry(conn):
    import db

    db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")

    assert db.list_items(conn)[0]["storage"] == "pantry"


def test_add_item_accepts_storage_and_expiry_date(conn):
    import db

    db.add_item(
        conn,
        name="Peas",
        quantity=1,
        unit="bag",
        location="",
        storage="freezer",
        expiry_date="2026-09-15",
    )

    item = db.list_items(conn)[0]
    assert item["storage"] == "freezer"
    assert item["expiry_date"] == "2026-09-15"


def test_list_items_filters_by_storage(conn):
    import db

    db.add_item(conn, name="Rice", quantity=1, unit="", location="", storage="pantry")
    db.add_item(conn, name="Peas", quantity=1, unit="", location="", storage="freezer")

    assert [i["name"] for i in db.list_items(conn, storage="pantry")] == ["Rice"]
    assert [i["name"] for i in db.list_items(conn, storage="freezer")] == ["Peas"]
    assert len(db.list_items(conn)) == 2
