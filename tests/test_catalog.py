import os


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _write_item(content_dir, category, slug, name, photos=None):
    category_dir = os.path.join(content_dir, category)
    os.makedirs(category_dir, exist_ok=True)
    photos_yaml = ""
    if photos:
        photos_yaml = "photos:\n" + "\n".join(f"  - {p}" for p in photos) + "\n"
    with open(os.path.join(category_dir, f"{slug}.md"), "w") as f:
        f.write(f"---\nname: {name}\ncategory: {category}\n{photos_yaml}---\nSome notes.\n")


def test_catalog_list_requires_login(client):
    response = client.get("/catalog/kitchen")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_catalog_list_shows_items_in_category(client, app):
    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    _write_item(content_dir, "kitchen", "dutch-oven", "Dutch Oven")
    app.catalog.reload()

    _login(client)
    response = client.get("/catalog/kitchen")

    assert response.status_code == 200
    assert b"Dutch Oven" in response.data


def test_catalog_detail_shows_item_notes(client, app):
    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    _write_item(content_dir, "kitchen", "dutch-oven", "Dutch Oven")
    app.catalog.reload()

    _login(client)
    response = client.get("/catalog/kitchen/dutch-oven")

    assert response.status_code == 200
    assert b"Some notes" in response.data


def test_catalog_detail_unknown_slug_is_404(client, app):
    app.catalog.reload()
    _login(client)

    response = client.get("/catalog/kitchen/does-not-exist")

    assert response.status_code == 404


def test_photo_route_serves_existing_photo(client):
    photos_dir = os.environ["HOMEHQ_PHOTOS_DIR"]
    os.makedirs(os.path.join(photos_dir, "kitchen"), exist_ok=True)
    with open(os.path.join(photos_dir, "kitchen", "dutch-oven-1.jpg"), "wb") as f:
        f.write(b"fake-jpeg-bytes")

    _login(client)
    response = client.get("/photos/kitchen/dutch-oven-1.jpg")

    assert response.status_code == 200
    assert response.data == b"fake-jpeg-bytes"


def test_photo_route_rejects_path_traversal(client):
    _login(client)

    response = client.get("/photos/..%2F..%2Fetc%2Fpasswd")

    assert response.status_code in (400, 404)
