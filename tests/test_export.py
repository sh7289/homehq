import os


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def test_pantry_csv_export_contains_items(client):
    _login(client)
    client.post(
        "/pantry/add", data={"name": "Rice", "quantity": "2", "unit": "bags", "location": "shelf"}
    )

    response = client.get("/export/pantry.csv")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/csv")
    body = response.data.decode("utf-8-sig")
    assert "Rice" in body
    assert "2" in body


def test_pantry_csv_export_escapes_formula_injection(client):
    _login(client)
    client.post(
        "/pantry/add",
        data={"name": "=cmd|'/c calc'!A1", "quantity": "1", "unit": "", "location": ""},
    )

    response = client.get("/export/pantry.csv")

    body = response.data.decode("utf-8-sig")
    assert "'=cmd" in body


def test_catalog_csv_export_contains_items(client, app):
    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    os.makedirs(os.path.join(content_dir, "kitchen"), exist_ok=True)
    with open(os.path.join(content_dir, "kitchen", "pan.md"), "w") as f:
        f.write("---\nname: Pan\ncategory: kitchen\nbrand: Lodge\n---\nNotes\n")
    app.catalog.reload()

    _login(client)
    response = client.get("/export/kitchen.csv")

    assert response.status_code == 200
    body = response.data.decode("utf-8-sig")
    assert "Pan" in body
    assert "Lodge" in body


def test_export_requires_login(client):
    response = client.get("/export/pantry.csv")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
