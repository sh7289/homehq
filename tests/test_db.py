from datetime import datetime, timezone

import pytest


@pytest.fixture
def conn(tmp_path):
    import db

    connection = db.get_connection(str(tmp_path / "pantry.db"))
    db.init_db(connection)
    yield connection
    connection.close()


def test_add_item_and_list(conn):
    import db

    db.add_item(conn, name="Rice", quantity=2, unit="bags", location="pantry shelf")

    items = db.list_items(conn)

    assert len(items) == 1
    assert items[0]["name"] == "Rice"
    assert items[0]["quantity"] == 2
    assert items[0]["unit"] == "bags"
    assert items[0]["location"] == "pantry shelf"


def test_adjust_quantity_increments_atomically(conn):
    import db

    item_id = db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")

    db.adjust_quantity(conn, item_id, delta=1)

    items = db.list_items(conn)
    assert items[0]["quantity"] == 3


def test_adjust_quantity_does_not_go_negative(conn):
    import db

    item_id = db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")

    db.adjust_quantity(conn, item_id, delta=-5)

    items = db.list_items(conn)
    assert items[0]["quantity"] == 0


def test_delete_item_removes_it(conn):
    import db

    item_id = db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")

    db.delete_item(conn, item_id)

    assert db.list_items(conn) == []


def test_updated_at_is_utc_iso8601_and_current(conn):
    import db

    before = datetime.now(timezone.utc)
    item_id = db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")
    db.adjust_quantity(conn, item_id, delta=1)
    after = datetime.now(timezone.utc)

    updated_at = datetime.fromisoformat(db.list_items(conn)[0]["updated_at"])

    assert before <= updated_at <= after
