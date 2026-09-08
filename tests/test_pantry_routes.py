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
    # Redirects back with a #row-N anchor so the browser scrolls to the item
    # that was just adjusted, instead of dropping the user at the top.
    assert response.headers["Location"].endswith(f"/pantry#row-{item_id}")
    assert _db_items()[0]["quantity"] == 3


def test_delete_removes_item(client):
    _login(client)
    client.post(
        "/pantry/add", data={"name": "Rice", "quantity": "2", "unit": "bags", "location": ""}
    )
    item_id = _db_items()[0]["id"]

    client.post(f"/inventory/{item_id}/delete", data={"storage": "pantry"})

    assert _db_items() == []


def test_delete_shows_an_undo_banner_and_restore_brings_the_item_back(client):
    _login(client)
    client.post(
        "/pantry/add",
        data={"name": "Rice", "quantity": "2", "unit": "bags", "location": "top shelf"},
    )
    item_id = _db_items()[0]["id"]

    delete_response = client.post(
        f"/inventory/{item_id}/delete", data={"storage": "pantry"}, follow_redirects=True
    )

    assert _db_items() == []
    body = delete_response.data.decode()
    assert "Removed" in body
    assert "Rice" in body
    assert f'/inventory/{item_id}/restore' in body

    restore_response = client.post(
        f"/inventory/{item_id}/restore", data={"storage": "pantry"}, follow_redirects=True
    )

    assert restore_response.status_code == 200
    restored = _db_items()
    assert len(restored) == 1
    assert restored[0]["id"] == item_id
    assert restored[0]["name"] == "Rice"
    assert restored[0]["quantity"] == 2
    assert restored[0]["unit"] == "bags"
    assert restored[0]["location"] == "top shelf"


def test_double_restore_via_the_route_does_not_duplicate_the_item(client):
    _login(client)
    client.post(
        "/pantry/add", data={"name": "Rice", "quantity": "2", "unit": "bags", "location": ""}
    )
    item_id = _db_items()[0]["id"]
    client.post(f"/inventory/{item_id}/delete", data={"storage": "pantry"})

    client.post(f"/inventory/{item_id}/restore", data={"storage": "pantry"})
    client.post(f"/inventory/{item_id}/restore", data={"storage": "pantry"})

    assert len(_db_items()) == 1


def test_deleted_item_is_excluded_from_search(client):
    _login(client)
    # A second, undeleted item keeps the storage non-empty, so the assertion
    # below exercises the "no matches for this search" empty state rather
    # than the "nothing logged at all" one.
    client.post(
        "/pantry/add", data={"name": "Beans", "quantity": "1", "unit": "cans", "location": ""}
    )
    client.post(
        "/pantry/add", data={"name": "Rice", "quantity": "2", "unit": "bags", "location": ""}
    )
    rice_id = next(item for item in _db_items() if item["name"] == "Rice")["id"]
    client.post(f"/inventory/{rice_id}/delete", data={"storage": "pantry"})

    body = client.get("/pantry?q=Rice").data.decode()

    assert "No items match your search." in body


def test_unrelated_item_survives_delete_and_undo_of_another_item(client):
    _login(client)
    client.post(
        "/pantry/add", data={"name": "Beans", "quantity": "1", "unit": "cans", "location": ""}
    )
    client.post(
        "/pantry/add", data={"name": "Rice", "quantity": "2", "unit": "bags", "location": ""}
    )
    items = {item["name"]: item for item in _db_items()}
    rice_id = items["Rice"]["id"]

    client.post(f"/inventory/{rice_id}/delete", data={"storage": "pantry"})
    client.post(f"/inventory/{rice_id}/restore", data={"storage": "pantry"})

    by_name = {item["name"]: item for item in _db_items()}
    assert by_name["Beans"]["quantity"] == 1
    assert by_name["Rice"]["quantity"] == 2


def test_pantry_routes_require_login(client):
    response = client.post("/pantry/add", data={"name": "Rice", "quantity": "1"})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
