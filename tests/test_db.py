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


def test_adjust_quantity_returns_true_for_a_live_row(conn):
    import db

    item_id = db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")

    assert db.adjust_quantity(conn, item_id, delta=1) is True


def test_adjust_quantity_returns_false_and_no_ops_for_a_soft_deleted_row(conn):
    """Guards the exact shape of Important finding #1/T13c: adjust_quantity
    previously updated WHERE id = ? with no deleted_at filter and no way
    for a caller to tell whether it actually touched a live row. A
    soft-deleted (or nonexistent) item_id must now no-op and report that
    back via a False return, not silently succeed."""
    import db

    item_id = db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")
    db.delete_item(conn, item_id)

    result = db.adjust_quantity(conn, item_id, delta=5)

    assert result is False
    row = conn.execute("SELECT quantity FROM pantry_items WHERE id = ?", (item_id,)).fetchone()
    assert row["quantity"] == 2  # untouched, not 7


def test_adjust_quantity_returns_false_for_a_nonexistent_row(conn):
    import db

    assert db.adjust_quantity(conn, 999999, delta=1) is False


def test_delete_item_removes_it(conn):
    import db

    item_id = db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")

    db.delete_item(conn, item_id)

    assert db.list_items(conn) == []


def test_delete_item_is_a_soft_delete_not_a_hard_delete(conn):
    """Task 13: deleting sets deleted_at instead of removing the row, which
    is what makes restore_item possible."""
    import db

    item_id = db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")

    db.delete_item(conn, item_id)

    row = conn.execute("SELECT * FROM pantry_items WHERE id = ?", (item_id,)).fetchone()
    assert row is not None
    assert row["deleted_at"] is not None


def test_restore_item_undoes_a_delete_with_every_field_intact(conn):
    import db

    item_id = db.add_item(
        conn,
        name="Rice",
        quantity=2,
        unit="bags",
        location="top shelf",
        storage="pantry",
        expiry_date="2027-01-01",
        acquired_date="2026-06-01",
        shelf_life_days=100,
        section="bulk-dry",
    )

    db.delete_item(conn, item_id)
    assert db.list_items(conn) == []  # gone from the live listing while deleted

    db.restore_item(conn, item_id)

    items = db.list_items(conn)
    assert len(items) == 1
    restored = items[0]
    assert restored["id"] == item_id
    assert restored["name"] == "Rice"
    assert restored["quantity"] == 2
    assert restored["unit"] == "bags"
    assert restored["location"] == "top shelf"
    assert restored["storage"] == "pantry"
    assert restored["expiry_date"] == "2027-01-01"
    assert restored["acquired_date"] == "2026-06-01"
    assert restored["shelf_life_days"] == 100
    assert restored["section"] == "bulk-dry"
    assert restored["deleted_at"] is None


def test_double_restore_item_is_idempotent_not_a_duplicate_insert(conn):
    """Restoring an already-restored (or never-deleted) row must be a no-op:
    it's always an UPDATE on the same id, never a re-INSERT."""
    import db

    item_id = db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")
    db.delete_item(conn, item_id)

    db.restore_item(conn, item_id)
    db.restore_item(conn, item_id)  # double-restore

    items = db.list_items(conn)
    assert len(items) == 1
    assert items[0]["id"] == item_id

    # Also true for an item that was never deleted at all.
    other_id = db.add_item(conn, name="Beans", quantity=1, unit="cans", location="")
    db.restore_item(conn, other_id)
    assert len(db.list_items(conn)) == 2


def test_concurrent_edit_to_a_different_item_survives_unrelated_delete_and_undo(conn):
    """Undo is per-row (deleted_at on the one soft-deleted row), not a
    snapshot/restore of the whole table -- so a concurrent edit made to a
    different item while the first item sits soft-deleted is never
    clobbered by that item's later restore."""
    import db

    keep_id = db.add_item(conn, name="Beans", quantity=1, unit="cans", location="")
    gone_id = db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")

    db.delete_item(conn, gone_id)
    # A concurrent edit to the unrelated item, made while gone_id is still
    # soft-deleted (simulating a second household member editing at the
    # same time).
    db.update_item(
        conn,
        keep_id,
        quantity=5,
        unit="cans",
        location="pantry shelf",
        expiry_date=None,
        acquired_date=None,
        shelf_life_days=None,
        section=None,
    )
    db.restore_item(conn, gone_id)

    items = {item["id"]: item for item in db.list_items(conn)}
    assert items[keep_id]["quantity"] == 5
    assert items[keep_id]["location"] == "pantry shelf"
    assert items[gone_id]["name"] == "Rice"
    assert items[gone_id]["quantity"] == 2


def test_list_items_excludes_soft_deleted_rows_by_storage_too(conn):
    import db

    item_id = db.add_item(
        conn, name="Peas", quantity=1, unit="bag", location="", storage="freezer"
    )

    db.delete_item(conn, item_id)

    assert db.list_items(conn, storage="freezer") == []
    assert db.list_items(conn) == []


def test_get_item_returns_the_item(conn):
    import db

    item_id = db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")

    item = db.get_item(conn, item_id)

    assert item["name"] == "Rice"


def test_get_item_returns_none_when_missing(conn):
    import db

    assert db.get_item(conn, 999) is None


def test_get_item_returns_none_for_a_soft_deleted_row(conn):
    """Regression test companion to the shopping-list equivalent: get_item
    is used to decide whether to act on a row, not only to read display
    data, so it must stay deleted-aware rather than surfacing a
    soft-deleted row as if it were still live."""
    import db

    item_id = db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")
    db.delete_item(conn, item_id)

    assert db.get_item(conn, item_id) is None


def test_updated_at_is_utc_iso8601_and_current(conn):
    import db

    before = datetime.now(timezone.utc)
    item_id = db.add_item(conn, name="Rice", quantity=2, unit="bags", location="")
    db.adjust_quantity(conn, item_id, delta=1)
    after = datetime.now(timezone.utc)

    updated_at = datetime.fromisoformat(db.list_items(conn)[0]["updated_at"])

    assert before <= updated_at <= after
