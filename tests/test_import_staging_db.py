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
        estimated_value="$150-250",
        source_image_path="uploads/record1.jpg",
    )

    item = db.list_staging_items(conn, status="pending")[0]
    assert item["category"] == "valuables"
    assert item["brand"] == "Columbia Records"
    assert item["notes"] == "Miles Davis, 1959 pressing"
    assert item["estimated_value"] == "$150-250"


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


def test_group_staging_items_by_photo_groups_shared_source_image(conn):
    import db

    id_a1 = db.add_staging_item(
        conn, target_type="inventory", name="Rice", source_image_path="/uploads/a.jpg"
    )
    id_a2 = db.add_staging_item(
        conn, target_type="inventory", name="Beans", source_image_path="/uploads/a.jpg"
    )
    id_b1 = db.add_staging_item(
        conn, target_type="inventory", name="Milk", source_image_path="/uploads/b.jpg"
    )

    items = db.list_staging_items(conn, status="pending")
    groups = db.group_staging_items_by_photo(items)

    assert len(groups) == 2
    assert groups[0]["source_image_path"] == "/uploads/a.jpg"
    assert [row["id"] for row in groups[0]["rows"]] == [id_a1, id_a2]
    assert groups[1]["source_image_path"] == "/uploads/b.jpg"
    assert [row["id"] for row in groups[1]["rows"]] == [id_b1]


def test_group_staging_items_by_photo_keeps_text_captures_ungrouped(conn):
    """A row with no source_image_path (a /capture text entry) must never be
    merged with another photo-less row into a shared "no photo" bucket --
    each becomes its own single-row group."""
    import db

    text_id_1 = db.add_staging_item(conn, target_type="inventory", name="Onions")
    photo_id = db.add_staging_item(
        conn, target_type="inventory", name="Milk", source_image_path="/uploads/a.jpg"
    )
    text_id_2 = db.add_staging_item(conn, target_type="inventory", name="Garlic")

    items = db.list_staging_items(conn, status="pending")
    groups = db.group_staging_items_by_photo(items)

    assert len(groups) == 3
    assert [g["source_image_path"] for g in groups] == [None, "/uploads/a.jpg", None]
    assert [row["id"] for row in groups[0]["rows"]] == [text_id_1]
    assert [row["id"] for row in groups[1]["rows"]] == [photo_id]
    assert [row["id"] for row in groups[2]["rows"]] == [text_id_2]


def test_group_staging_items_by_photo_empty_list_is_empty(conn):
    import db

    assert db.group_staging_items_by_photo([]) == []
