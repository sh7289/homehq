import os


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _db_items():
    import db

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    try:
        return db.list_items(conn)
    finally:
        conn.close()


def test_add_item_shows_up_on_pantry_page(client):
    _login(client)

    response = client.post(
        "/pantry/add",
        data={"name": "Rice", "quantity": "2", "unit": "bags", "location": "shelf"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Rice" in response.data


def test_increment_button_increases_quantity(client):
    _login(client)
    client.post(
        "/pantry/add", data={"name": "Rice", "quantity": "2", "unit": "bags", "location": ""}
    )
    item_id = _db_items()[0]["id"]

    response = client.post(f"/inventory/{item_id}/adjust", data={"delta": "1", "storage": "pantry"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/pantry")
    assert _db_items()[0]["quantity"] == 3


def test_delete_removes_item(client):
    _login(client)
    client.post(
        "/pantry/add", data={"name": "Rice", "quantity": "2", "unit": "bags", "location": ""}
    )
    item_id = _db_items()[0]["id"]

    client.post(f"/inventory/{item_id}/delete", data={"storage": "pantry"})

    assert _db_items() == []


def test_pantry_routes_require_login(client):
    response = client.post("/pantry/add", data={"name": "Rice", "quantity": "1"})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
