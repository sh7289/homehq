import io
import os


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _write_item(app_content_dir, category, slug, name):
    import os as _os

    category_dir = _os.path.join(app_content_dir, category)
    _os.makedirs(category_dir, exist_ok=True)
    with open(_os.path.join(category_dir, f"{slug}.md"), "w") as f:
        f.write(f"---\nname: {name}\ncategory: {category}\n---\n")


def test_add_photo_requires_login(client):
    response = client.post("/catalog/valuables/turntable/add-photo")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_add_photo_appends_photo_and_redirects(client, app, monkeypatch):
    monkeypatch.delenv("HOMEHQ_GITHUB_TOKEN", raising=False)
    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    _write_item(content_dir, "valuables", "turntable", "Turntable")
    app.catalog.reload()
    _login(client)

    response = client.post(
        "/catalog/valuables/turntable/add-photo",
        data={"photo": (io.BytesIO(b"fake-jpeg-bytes"), "photo1.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/catalog/valuables/turntable")
    text = open(os.path.join(content_dir, "valuables", "turntable.md")).read()
    assert "photos:" in text
    assert "turntable-1" in text


def test_add_photo_to_unknown_item_is_404(client):
    _login(client)

    response = client.post(
        "/catalog/valuables/does-not-exist/add-photo",
        data={"photo": (io.BytesIO(b"fake-jpeg-bytes"), "photo1.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 404
