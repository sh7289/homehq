import pytest


@pytest.fixture
def conn(tmp_path):
    import db

    connection = db.get_connection(str(tmp_path / "pantry.db"))
    db.init_db(connection)
    yield connection
    connection.close()


def test_add_and_list_pending_staging_items(conn):
    import db

    db.add_staging_item(
        conn,
        target_type="inventory",
        name="Rice",
        quantity=2,
        unit="bags",
        storage="pantry",
        source_image_path="uploads/receipt1.jpg",
    )

    items = db.list_staging_items(conn, status="pending")

    assert len(items) == 1
    assert items[0]["name"] == "Rice"
    assert items[0]["target_type"] == "inventory"
    assert items[0]["status"] == "pending"


def test_add_catalog_staging_item_with_catalog_fields(conn):
    import db

    db.add_staging_item(
        conn,
        target_type="catalog",
        name="Kind of Blue",
        category="valuables",
        brand="Columbia Records",
        notes="Miles Davis, 1959 pressing",
        source_image_path="uploads/record1.jpg",
    )

    item = db.list_staging_items(conn, status="pending")[0]
    assert item["category"] == "valuables"
    assert item["brand"] == "Columbia Records"
    assert item["notes"] == "Miles Davis, 1959 pressing"


def test_update_staging_item_edits_fields(conn):
    import db

    item_id = db.add_staging_item(conn, target_type="inventory", name="Rice", quantity=1)

    db.update_staging_item(conn, item_id, name="Jasmine Rice", quantity=3, unit="bags")

    item = db.get_staging_item(conn, item_id)
    assert item["name"] == "Jasmine Rice"
    assert item["quantity"] == 3
    assert item["unit"] == "bags"


def test_set_staging_item_status(conn):
    import db

    item_id = db.add_staging_item(conn, target_type="inventory", name="Rice")

    db.set_staging_item_status(conn, item_id, "approved")

    assert db.get_staging_item(conn, item_id)["status"] == "approved"
    assert db.list_staging_items(conn, status="pending") == []


def test_delete_staging_item_removes_it(conn):
    import db

    item_id = db.add_staging_item(conn, target_type="inventory", name="Rice")

    db.delete_staging_item(conn, item_id)

    assert db.get_staging_item(conn, item_id) is None
