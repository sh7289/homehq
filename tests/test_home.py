def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def test_home_requires_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_home_lists_categories_and_pantry_link(client, app):
    import os

    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    os.makedirs(os.path.join(content_dir, "kitchen"), exist_ok=True)
    with open(os.path.join(content_dir, "kitchen", "pan.md"), "w") as f:
        f.write("---\nname: Pan\ncategory: kitchen\n---\n")
    app.catalog.reload()

    _login(client)
    response = client.get("/")

    assert response.status_code == 200
    assert b"Kitchen" in response.data
    assert b"Pantry" in response.data
