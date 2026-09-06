import os

import pytest

import catalog_writer


def _seed(tmp_path, slug="ukulele", photos=("musical-instruments/ukulele-1.jpg",)):
    content_dir = tmp_path / "content"
    photos_dir = tmp_path / "photos"
    category_dir = content_dir / "musical-instruments"
    os.makedirs(category_dir, exist_ok=True)
    os.makedirs(photos_dir / "musical-instruments", exist_ok=True)

    listed = "".join(f"  - {p}\n" for p in photos)
    with open(category_dir / f"{slug}.md", "w") as f:
        f.write(f"---\nname: {slug}\ncategory: musical-instruments\nphotos:\n{listed}---\nNotes.\n")
    for photo in photos:
        with open(photos_dir / photo, "wb") as f:
            f.write(b"jpeg")
    return str(content_dir), str(photos_dir)


def test_deletes_the_markdown_file(tmp_path):
    content_dir, photos_dir = _seed(tmp_path)

    catalog_writer.delete_catalog_item(
        content_dir, photos_dir, "musical-instruments", "ukulele",
        photos=["musical-instruments/ukulele-1.jpg"],
    )

    assert not os.path.exists(os.path.join(content_dir, "musical-instruments", "ukulele.md"))


def test_deletes_the_items_photos(tmp_path):
    content_dir, photos_dir = _seed(tmp_path)

    catalog_writer.delete_catalog_item(
        content_dir, photos_dir, "musical-instruments", "ukulele",
        photos=["musical-instruments/ukulele-1.jpg"],
    )

    assert not os.path.exists(os.path.join(photos_dir, "musical-instruments/ukulele-1.jpg"))


def test_leaves_other_items_alone(tmp_path):
    content_dir, photos_dir = _seed(tmp_path)
    _seed(tmp_path, slug="guitar", photos=("musical-instruments/guitar-1.jpg",))

    catalog_writer.delete_catalog_item(
        content_dir, photos_dir, "musical-instruments", "ukulele",
        photos=["musical-instruments/ukulele-1.jpg"],
    )

    assert os.path.exists(os.path.join(content_dir, "musical-instruments", "guitar.md"))
    assert os.path.exists(os.path.join(photos_dir, "musical-instruments/guitar-1.jpg"))


def test_missing_item_raises(tmp_path):
    content_dir, photos_dir = _seed(tmp_path)

    with pytest.raises(FileNotFoundError):
        catalog_writer.delete_catalog_item(
            content_dir, photos_dir, "musical-instruments", "nonexistent"
        )


@pytest.mark.parametrize("slug", ["../../etc/passwd", "../valuables/turntable"])
def test_refuses_to_escape_the_content_directory(tmp_path, slug):
    content_dir, photos_dir = _seed(tmp_path)

    with pytest.raises(ValueError):
        catalog_writer.delete_catalog_item(
            content_dir, photos_dir, "musical-instruments", slug
        )


def test_refuses_a_photo_path_outside_the_photos_directory(tmp_path):
    """Photo paths come from a file the AI wrote; treat them as untrusted."""
    content_dir, photos_dir = _seed(tmp_path)
    outsider = tmp_path / "important.txt"
    outsider.write_text("keep me")

    with pytest.raises(ValueError):
        catalog_writer.delete_catalog_item(
            content_dir, photos_dir, "musical-instruments", "ukulele",
            photos=["../important.txt"],
        )

    assert outsider.exists()


def test_a_missing_photo_does_not_stop_the_delete(tmp_path):
    content_dir, photos_dir = _seed(tmp_path, photos=())

    catalog_writer.delete_catalog_item(
        content_dir, photos_dir, "musical-instruments", "ukulele",
        photos=["musical-instruments/never-existed.jpg"],
    )

    assert not os.path.exists(os.path.join(content_dir, "musical-instruments", "ukulele.md"))


# --- route ----------------------------------------------------------------


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _seed_app_item(app, slug="ukulele"):
    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    photos_dir = os.environ["HOMEHQ_PHOTOS_DIR"]
    os.makedirs(os.path.join(content_dir, "musical-instruments"), exist_ok=True)
    os.makedirs(os.path.join(photos_dir, "musical-instruments"), exist_ok=True)
    with open(os.path.join(content_dir, "musical-instruments", f"{slug}.md"), "w") as f:
        f.write(
            f"---\nname: {slug}\ncategory: musical-instruments\n"
            f"photos:\n  - musical-instruments/{slug}-1.jpg\n---\nNotes.\n"
        )
    with open(os.path.join(photos_dir, "musical-instruments", f"{slug}-1.jpg"), "wb") as f:
        f.write(b"jpeg")
    app.catalog.reload()


def test_delete_requires_login(client, app):
    _seed_app_item(app)

    response = client.post("/catalog/musical-instruments/ukulele/delete")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_delete_removes_the_item_and_refreshes_the_catalog(client, app):
    _seed_app_item(app)
    _login(client)

    response = client.post("/catalog/musical-instruments/ukulele/delete")

    assert response.status_code == 302
    assert app.catalog.get("musical-instruments", "ukulele") is None
    assert not os.path.exists(
        os.path.join(
            os.environ["HOMEHQ_PHOTOS_DIR"], "musical-instruments", "ukulele-1.jpg"
        )
    )


def test_deleting_something_that_is_not_there_404s(client, app):
    _login(client)

    assert client.post("/catalog/musical-instruments/ghost/delete").status_code == 404


def test_detail_page_offers_a_confirm_step_not_a_bare_button(client, app):
    _seed_app_item(app)
    _login(client)

    body = client.get("/catalog/musical-instruments/ukulele").data.decode()

    assert "/delete" in body
    assert "js-edit-toggle" in body, "delete should be behind a reveal, not one tap"


def test_delete_commits_when_a_token_is_configured(client, app, monkeypatch):
    import catalog_writer as cw

    calls = []
    monkeypatch.setenv("HOMEHQ_GITHUB_TOKEN", "fake")
    monkeypatch.setattr(
        cw, "git_commit_and_push", lambda *a, **k: calls.append(a[1])
    )
    _seed_app_item(app)
    _login(client)

    client.post("/catalog/musical-instruments/ukulele/delete")

    assert calls and "ukulele" in calls[0].lower()


def test_delete_survives_a_push_failure(client, app, monkeypatch):
    import catalog_writer as cw

    monkeypatch.setenv("HOMEHQ_GITHUB_TOKEN", "fake")
    monkeypatch.setattr(
        cw,
        "git_commit_and_push",
        lambda *a, **k: (_ for _ in ()).throw(cw.PushFailed("no")),
    )
    _seed_app_item(app)
    _login(client)

    response = client.post("/catalog/musical-instruments/ukulele/delete")

    assert response.status_code == 302
    assert app.catalog.get("musical-instruments", "ukulele") is None
