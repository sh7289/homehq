import os


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _db_items(storage=None):
    import db

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    try:
        return db.list_items(conn, storage=storage)
    finally:
        conn.close()


def test_freezer_add_creates_item_scoped_to_freezer_storage(client):
    _login(client)

    response = client.post(
        "/freezer/add",
        data={"name": "Peas", "quantity": "1", "unit": "bag", "location": "top drawer"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Peas" in response.data
    assert _db_items(storage="freezer")[0]["name"] == "Peas"


def test_freezer_page_does_not_show_pantry_items(client):
    _login(client)
    client.post("/pantry/add", data={"name": "Rice", "quantity": "1", "unit": "", "location": ""})
    client.post("/freezer/add", data={"name": "Peas", "quantity": "1", "unit": "", "location": ""})

    response = client.get("/freezer")

    assert b"Peas" in response.data
    assert b"Rice" not in response.data


def test_freezer_routes_require_login(client):
    response = client.get("/freezer")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_update_item_edits_full_record_and_redirects_to_originating_page(client):
    _login(client)
    client.post("/freezer/add", data={"name": "Peas", "quantity": "1", "unit": "", "location": ""})
    item_id = _db_items(storage="freezer")[0]["id"]

    response = client.post(
        f"/inventory/{item_id}/update",
        data={
            "quantity": "3",
            "unit": "bags",
            "location": "drawer 2",
            "expiry_date": "2026-12-01",
            "acquired_date": "2026-06-01",
            "shelf_life_days": "270",
            "storage": "freezer",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/freezer")
    item = _db_items(storage="freezer")[0]
    assert item["quantity"] == 3
    assert item["unit"] == "bags"
    assert item["location"] == "drawer 2"
    assert item["expiry_date"] == "2026-12-01"
    assert item["acquired_date"] == "2026-06-01"
    assert item["shelf_life_days"] == 270


def test_pantry_page_shows_expiring_and_expired_sections(client):
    import db

    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    db.add_item(conn, name="Old Milk", quantity=1, unit="", location="", expiry_date="2020-01-01")
    db.add_item(
        conn, name="Fresh Bread", quantity=1, unit="", location="", expiry_date="2099-01-01"
    )
    conn.close()

    response = client.get("/pantry")

    assert response.status_code == 200
    assert b"Old Milk" in response.data


def test_export_freezer_csv_includes_expiry_date_column(client):
    _login(client)
    client.post(
        "/freezer/add",
        data={
            "name": "Peas",
            "quantity": "1",
            "unit": "",
            "location": "",
            "expiry_date": "2026-12-01",
        },
    )

    response = client.get("/export/freezer.csv")

    assert response.status_code == 200
    body = response.data.decode("utf-8-sig")
    assert "expiry_date" in body
    assert "2026-12-01" in body
