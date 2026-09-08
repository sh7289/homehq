import pytest


@pytest.fixture
def conn(tmp_path):
    import db

    connection = db.get_connection(str(tmp_path / "pantry.db"))
    db.init_db(connection)
    yield connection
    connection.close()


def test_add_and_list_shopping_list_items(conn):
    import db

    db.add_shopping_list_item(conn, name="Rice", storage="pantry", quantity_to_buy=2)

    items = db.list_shopping_list_items(conn)

    assert len(items) == 1
    assert items[0]["name"] == "Rice"
    assert items[0]["storage"] == "pantry"
    assert items[0]["quantity_to_buy"] == 2


def test_add_shopping_list_item_quantity_to_buy_is_optional(conn):
    import db

    db.add_shopping_list_item(conn, name="Peas", storage="freezer")

    assert db.list_shopping_list_items(conn)[0]["quantity_to_buy"] is None


def test_get_shopping_list_item_returns_none_when_missing(conn):
    import db

    assert db.get_shopping_list_item(conn, 999) is None


def test_get_shopping_list_item_returns_the_item(conn):
    import db

    item_id = db.add_shopping_list_item(conn, name="Rice", storage="pantry")

    item = db.get_shopping_list_item(conn, item_id)

    assert item["name"] == "Rice"


def test_get_shopping_list_item_returns_none_for_a_soft_deleted_row(conn):
    """Regression test for the resolve-on-a-deleted-item bug: this lookup
    must stay deleted-aware, since a caller (shopping_list_resolve) uses it
    to decide whether a mutation should proceed at all, not only to read
    display data."""
    import db

    item_id = db.add_shopping_list_item(conn, name="Rice", storage="pantry")
    db.delete_shopping_list_item(conn, item_id)

    assert db.get_shopping_list_item(conn, item_id) is None


def test_delete_shopping_list_item_removes_it(conn):
    import db

    item_id = db.add_shopping_list_item(conn, name="Rice", storage="pantry")

    db.delete_shopping_list_item(conn, item_id)

    assert db.list_shopping_list_items(conn) == []


def test_delete_shopping_list_item_is_a_soft_delete(conn):
    import db

    item_id = db.add_shopping_list_item(conn, name="Rice", storage="pantry")

    db.delete_shopping_list_item(conn, item_id)

    row = conn.execute(
        "SELECT * FROM shopping_list_items WHERE id = ?", (item_id,)
    ).fetchone()
    assert row is not None
    assert row["deleted_at"] is not None


def test_restore_shopping_list_item_round_trips_every_field(conn):
    import db

    item_id = db.add_shopping_list_item(
        conn, name="Rice", storage="freezer", quantity_to_buy=2
    )

    db.delete_shopping_list_item(conn, item_id)
    assert db.list_shopping_list_items(conn) == []

    db.restore_shopping_list_item(conn, item_id)

    items = db.list_shopping_list_items(conn)
    assert len(items) == 1
    assert items[0]["id"] == item_id
    assert items[0]["name"] == "Rice"
    assert items[0]["storage"] == "freezer"
    assert items[0]["quantity_to_buy"] == 2
    assert items[0]["deleted_at"] is None


def test_double_restore_shopping_list_item_is_idempotent(conn):
    import db

    item_id = db.add_shopping_list_item(conn, name="Rice", storage="pantry")
    db.delete_shopping_list_item(conn, item_id)

    db.restore_shopping_list_item(conn, item_id)
    db.restore_shopping_list_item(conn, item_id)

    items = db.list_shopping_list_items(conn)
    assert len(items) == 1
    assert items[0]["id"] == item_id


def test_unrelated_shopping_list_item_survives_delete_and_undo(conn):
    import db

    keep_id = db.add_shopping_list_item(conn, name="Beans", storage="pantry")
    gone_id = db.add_shopping_list_item(conn, name="Rice", storage="pantry")

    db.delete_shopping_list_item(conn, gone_id)
    db.restore_shopping_list_item(conn, gone_id)

    items = {item["id"]: item for item in db.list_shopping_list_items(conn)}
    assert items[keep_id]["name"] == "Beans"
    assert items[gone_id]["name"] == "Rice"
