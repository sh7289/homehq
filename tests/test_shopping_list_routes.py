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


def _shopping_list():
    import db

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    try:
        return db.list_shopping_list_items(conn)
    finally:
        conn.close()


def test_shopping_list_requires_login(client):
    response = client.get("/shopping-list")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_add_item_shows_up_on_shopping_list(client):
    _login(client)

    response = client.post(
        "/shopping-list/add",
        data={"name": "Rice", "storage": "pantry", "quantity_to_buy": "2"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Rice" in response.data


def test_delete_removes_shopping_list_item(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    item_id = _shopping_list()[0]["id"]

    client.post(f"/shopping-list/{item_id}/delete")

    assert _shopping_list() == []


def test_resolve_as_match_increments_existing_item_and_clears_list(client):
    _login(client)
    client.post(
        "/pantry/add", data={"name": "Rice", "quantity": "2", "unit": "bags", "location": ""}
    )
    existing_id = _db_items(storage="pantry")[0]["id"]
    client.post("/shopping-list/add", data={"name": "Ricee", "storage": "pantry"})
    list_item_id = _shopping_list()[0]["id"]

    response = client.post(
        f"/shopping-list/{list_item_id}/resolve",
        data={"action": "match", "matched_item_id": str(existing_id), "quantity": "3"},
    )

    assert response.status_code == 302
    assert _db_items(storage="pantry")[0]["quantity"] == 5  # 2 + 3
    assert _shopping_list() == []


def test_resolve_as_new_creates_inventory_item_and_clears_list(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Peas", "storage": "freezer"})
    list_item_id = _shopping_list()[0]["id"]

    response = client.post(
        f"/shopping-list/{list_item_id}/resolve",
        data={"action": "new", "quantity": "1"},
    )

    assert response.status_code == 302
    items = _db_items(storage="freezer")
    assert len(items) == 1
    assert items[0]["name"] == "Peas"
    assert items[0]["quantity"] == 1
    assert _shopping_list() == []


def test_shopping_list_page_shows_suggested_match(client):
    _login(client)
    client.post(
        "/pantry/add", data={"name": "Cumin", "quantity": "1", "unit": "jar", "location": ""}
    )
    client.post("/shopping-list/add", data={"name": "Cummin", "storage": "pantry"})

    response = client.get("/shopping-list")

    assert response.status_code == 200
    assert b"Cumin" in response.data
