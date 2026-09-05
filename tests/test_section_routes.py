import db


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _conn(app):
    with app.app_context():
        import os
        import sqlite3

        conn = sqlite3.connect(os.environ["HOMEHQ_DB_PATH"])
        conn.row_factory = sqlite3.Row
        db.init_db(conn)
        return conn


def test_pantry_page_groups_items_under_section_headings(client, app):
    conn = _conn(app)
    db.add_item(conn, name="black beans", quantity=2, unit="can", location="", section="canned")
    db.add_item(conn, name="penne", quantity=1, unit="box", location="", section="bulk-dry")
    conn.close()
    _login(client)

    response = client.get("/pantry")

    assert response.status_code == 200
    body = response.data.decode()
    assert "Bulk &amp; Dry Goods" in body or "Bulk & Dry Goods" in body
    assert "Canned Goods" in body
    # Declared order: bulk-dry comes before canned.
    assert body.index("penne") < body.index("black beans")


def test_unsectioned_items_appear_under_other(client, app):
    conn = _conn(app)
    db.add_item(conn, name="mystery jar", quantity=1, unit="", location="")
    conn.close()
    _login(client)

    body = client.get("/pantry").data.decode()

    assert "mystery jar" in body
    assert "Other" in body


def test_freezer_page_offers_freezer_sections_not_pantry_ones(client, app):
    _login(client)

    body = client.get("/freezer").data.decode()

    assert "Meat &amp; Fish" in body or "Meat & Fish" in body
    assert "Canned Goods" not in body


def test_add_item_form_submits_section(client, app):
    _login(client)

    client.post(
        "/pantry/add",
        data={"name": "jasmine rice", "quantity": "1", "unit": "bag", "section": "bulk-dry"},
    )

    conn = _conn(app)
    assert db.list_items(conn)[0]["section"] == "bulk-dry"
    conn.close()


def test_add_item_with_bogus_section_falls_back_to_other(client, app):
    _login(client)

    client.post(
        "/pantry/add",
        data={"name": "odd thing", "quantity": "1", "unit": "", "section": "not-a-section"},
    )

    conn = _conn(app)
    assert db.list_items(conn)[0]["section"] == "other"
    conn.close()


def test_update_item_changes_section(client, app):
    conn = _conn(app)
    item_id = db.add_item(conn, name="cumin", quantity=1, unit="jar", location="")
    conn.close()
    _login(client)

    client.post(
        f"/inventory/{item_id}/update",
        data={"storage": "pantry", "quantity": "1", "unit": "jar", "section": "spices"},
    )

    conn = _conn(app)
    assert db.list_items(conn)[0]["section"] == "spices"
    conn.close()


def test_bulk_assign_sets_sections_for_several_items(client, app):
    conn = _conn(app)
    first = db.add_item(conn, name="rice", quantity=1, unit="bag", location="")
    second = db.add_item(conn, name="oregano", quantity=1, unit="jar", location="")
    conn.close()
    _login(client)

    response = client.post(
        "/inventory/sections",
        data={
            "storage": "pantry",
            f"section-{first}": "bulk-dry",
            f"section-{second}": "spices",
        },
    )

    assert response.status_code == 302
    conn = _conn(app)
    by_id = {item["id"]: item for item in db.list_items(conn)}
    assert by_id[first]["section"] == "bulk-dry"
    assert by_id[second]["section"] == "spices"
    conn.close()


def test_section_sorter_shows_only_one_batch(client, app):
    """106 backfilled rows must not render a 106-row form under the list."""
    conn = _conn(app)
    for index in range(20):
        db.add_item(conn, name=f"item {index}", quantity=1, unit="", location="")
    conn.close()
    _login(client)

    body = client.get("/pantry").data.decode()

    assert body.count('name="section-') == 15
    assert "15 of 20" in body


def test_section_sorter_disappears_when_everything_is_sorted(client, app):
    conn = _conn(app)
    db.add_item(conn, name="rice", quantity=1, unit="bag", location="", section="bulk-dry")
    conn.close()
    _login(client)

    body = client.get("/pantry").data.decode()

    assert 'name="section-' not in body


def test_bulk_assign_requires_login(client):
    response = client.post("/inventory/sections", data={"storage": "pantry"})

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_search_still_filters_across_sections(client, app):
    conn = _conn(app)
    db.add_item(conn, name="black beans", quantity=2, unit="can", location="", section="canned")
    db.add_item(conn, name="penne", quantity=1, unit="box", location="", section="bulk-dry")
    conn.close()
    _login(client)

    body = client.get("/pantry?q=penne").data.decode()

    assert "penne" in body
    assert "black beans" not in body
