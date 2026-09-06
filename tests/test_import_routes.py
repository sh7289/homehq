import io
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


def _staging_items(status="pending"):
    import db

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    try:
        return db.list_staging_items(conn, status=status)
    finally:
        conn.close()


def test_import_pages_require_login(client):
    for path in ("/import", "/import/upload"):
        response = client.get(path)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


def test_upload_extracts_receipt_into_staging_items(client, monkeypatch):
    import ai_extract

    def fake_extract(image_bytes, media_type, api_key=None):
        return [
            {
                "target_type": "inventory",
                "name": "Rice",
                "quantity": 2,
                "unit": "bags",
                "storage": "pantry",
            }
        ]

    monkeypatch.setattr(ai_extract, "extract_from_image", fake_extract)
    _login(client)

    response = client.post(
        "/import/upload",
        data={"photo": (io.BytesIO(b"fake-jpeg-bytes"), "receipt.jpg")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    staged = _staging_items()
    assert len(staged) == 1
    assert staged[0]["name"] == "Rice"
    assert staged[0]["target_type"] == "inventory"


def test_upload_shows_error_when_extraction_fails(client, monkeypatch):
    import ai_extract

    def fake_extract(image_bytes, media_type, api_key=None):
        raise ai_extract.ExtractionError("could not read image")

    monkeypatch.setattr(ai_extract, "extract_from_image", fake_extract)
    _login(client)

    response = client.post(
        "/import/upload",
        data={"photo": (io.BytesIO(b"fake-jpeg-bytes"), "receipt.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert b"could not read image" in response.data
    assert _staging_items() == []


def test_upload_shows_error_when_api_key_not_configured(client, monkeypatch):
    monkeypatch.delenv("HOMEHQ_ANTHROPIC_API_KEY", raising=False)
    _login(client)

    response = client.post(
        "/import/upload",
        data={"photo": (io.BytesIO(b"fake-jpeg-bytes"), "receipt.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert b"isn&#39;t configured" in response.data
    assert _staging_items() == []


def test_review_page_lists_pending_items(client):
    import db

    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    db.add_staging_item(conn, target_type="inventory", name="Rice", quantity=2)
    conn.close()

    response = client.get("/import")

    assert response.status_code == 200
    assert b"Rice" in response.data


def test_approve_inventory_item_as_new_creates_item(client):
    import db

    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    item_id = db.add_staging_item(
        conn, target_type="inventory", name="Rice", quantity=2, unit="bags", storage="pantry"
    )
    conn.close()

    response = client.post(
        f"/import/{item_id}/approve", data={"action": "new", "quantity": "2"}
    )

    assert response.status_code == 302
    items = _db_items(storage="pantry")
    assert len(items) == 1
    assert items[0]["name"] == "Rice"
    assert _staging_items() == []


def test_approve_inventory_item_as_match_increments_existing(client):
    import db

    _login(client)
    client.post(
        "/pantry/add", data={"name": "Rice", "quantity": "2", "unit": "bags", "location": ""}
    )
    existing_id = _db_items(storage="pantry")[0]["id"]
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    item_id = db.add_staging_item(conn, target_type="inventory", name="Ricee", quantity=1)
    conn.close()

    response = client.post(
        f"/import/{item_id}/approve",
        data={"action": "match", "matched_item_id": str(existing_id), "quantity": "1"},
    )

    assert response.status_code == 302
    assert _db_items(storage="pantry")[0]["quantity"] == 3
    assert _staging_items() == []


def test_approve_inventory_item_uses_edited_name_and_unit(client):
    import db

    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    item_id = db.add_staging_item(
        conn, target_type="inventory", name="Cvmmin", quantity=1, unit="jr", storage="pantry"
    )
    conn.close()

    response = client.post(
        f"/import/{item_id}/approve",
        data={
            "action": "new",
            "name": "Cumin",
            "quantity": "1",
            "unit": "jar",
            "storage": "pantry",
        },
    )

    assert response.status_code == 302
    items = _db_items(storage="pantry")
    assert items[0]["name"] == "Cumin"
    assert items[0]["unit"] == "jar"


def test_approve_catalog_item_writes_markdown_file(client, monkeypatch):
    import db

    monkeypatch.delenv("HOMEHQ_GITHUB_TOKEN", raising=False)
    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    item_id = db.add_staging_item(
        conn,
        target_type="catalog",
        name="Kind of Blue",
        category="valuables",
        brand="Columbia Records",
        notes="Miles Davis, 1959 pressing",
    )
    conn.close()

    response = client.post(f"/import/{item_id}/approve", data={})

    assert response.status_code == 302
    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    md_path = os.path.join(content_dir, "valuables", "kind-of-blue.md")
    assert os.path.exists(md_path)
    assert _staging_items() == []


def test_approve_catalog_item_uses_edited_fields(client, monkeypatch):
    import db

    monkeypatch.delenv("HOMEHQ_GITHUB_TOKEN", raising=False)
    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    item_id = db.add_staging_item(
        conn, target_type="catalog", name="Kind of Blu", category="valuables", brand="Columbi"
    )
    conn.close()

    response = client.post(
        f"/import/{item_id}/approve",
        data={
            "name": "Kind of Blue",
            "category": "vinyl-records",
            "brand": "Columbia Records",
            "model": "",
            "serial_number": "",
            "notes": "Miles Davis, 1959 pressing",
            "estimated_value": "40",
        },
    )

    assert response.status_code == 302
    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    md_path = os.path.join(content_dir, "vinyl-records", "kind-of-blue.md")
    assert os.path.exists(md_path)
    text = open(md_path).read()
    assert "brand: Columbia Records" in text
    assert "estimated_value: 40" in text
    assert "Miles Davis, 1959 pressing" in text


def test_approve_catalog_item_with_estimate_writes_value_and_date(client, monkeypatch):
    import datetime

    import db

    monkeypatch.delenv("HOMEHQ_GITHUB_TOKEN", raising=False)
    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    item_id = db.add_staging_item(
        conn,
        target_type="catalog",
        name="Turntable",
        category="valuables",
        estimated_value="1000",
    )
    conn.close()

    client.post(f"/import/{item_id}/approve", data={})

    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    md_path = os.path.join(content_dir, "valuables", "turntable.md")
    text = open(md_path).read()
    assert "estimated_value: 1000" in text
    today = datetime.date.today().isoformat()
    assert f"estimated_value_date: {today}" in text


def test_approve_catalog_item_without_estimate_omits_value_fields(client, monkeypatch):
    import db

    monkeypatch.delenv("HOMEHQ_GITHUB_TOKEN", raising=False)
    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    item_id = db.add_staging_item(
        conn, target_type="catalog", name="Dishwasher Manual", category="manuals"
    )
    conn.close()

    client.post(f"/import/{item_id}/approve", data={})

    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    md_path = os.path.join(content_dir, "manuals", "dishwasher-manual.md")
    text = open(md_path).read()
    assert "estimated_value" not in text


def test_reject_deletes_staging_item(client):
    import db

    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    item_id = db.add_staging_item(conn, target_type="inventory", name="Rice")
    conn.close()

    response = client.post(f"/import/{item_id}/reject")

    assert response.status_code == 302
    assert _staging_items() == []


def test_approve_clears_staging_even_when_the_push_fails(client, monkeypatch, app):
    """The bug that stranded the Ukulele: the file was written and committed,
    the push raised, and the staging row was never cleared -- so the item sat
    in /import forever and re-approving would write a duplicate file."""
    import catalog_writer

    import ai_extract

    monkeypatch.setenv("HOMEHQ_GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(
        ai_extract,
        "extract_from_image",
        lambda image_bytes, media_type, api_key=None: [
            {"target_type": "catalog", "name": "Ukulele", "category": "musical-instruments"}
        ],
    )

    def failing_push(*args, **kwargs):
        raise catalog_writer.PushFailed("Committed locally, but the push failed.")

    monkeypatch.setattr(catalog_writer, "git_commit_and_push", failing_push)

    _login(client)
    client.post(
        "/import/upload",
        data={"photo": (io.BytesIO(b"fake"), "uke.jpg")},
        content_type="multipart/form-data",
    )
    staged_id = _staging_items()[0]["id"]

    response = client.post(f"/import/{staged_id}/approve", data={})

    assert response.status_code == 302
    assert _staging_items() == [], "staging row should be cleared"
    assert os.path.exists(
        os.path.join(
            os.environ["HOMEHQ_CONTENT_DIR"], "musical-instruments", "ukulele.md"
        )
    )


def test_review_page_warns_when_a_push_failed(client, monkeypatch, app):
    import catalog_writer

    import ai_extract

    monkeypatch.setenv("HOMEHQ_GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(
        ai_extract,
        "extract_from_image",
        lambda image_bytes, media_type, api_key=None: [
            {"target_type": "catalog", "name": "Ukulele", "category": "musical-instruments"}
        ],
    )
    monkeypatch.setattr(
        catalog_writer,
        "git_commit_and_push",
        lambda *a, **k: (_ for _ in ()).throw(catalog_writer.PushFailed("nope")),
    )

    _login(client)
    client.post(
        "/import/upload",
        data={"photo": (io.BytesIO(b"fake"), "uke.jpg")},
        content_type="multipart/form-data",
    )
    staged_id = _staging_items()[0]["id"]

    response = client.post(f"/import/{staged_id}/approve", data={}, follow_redirects=True)

    assert b"saved on the server" in response.data or b"push" in response.data.lower()


def test_upload_size_limit_is_configured(app):
    """Flask had no limit at all; nginx caps the request at 10M in prod."""
    assert app.config["MAX_CONTENT_LENGTH"] == 10 * 1024 * 1024


def test_oversized_upload_gets_a_readable_message(client, app):
    _login(client)

    response = client.post(
        "/import/upload",
        data={"photo": (io.BytesIO(b"x" * (11 * 1024 * 1024)), "huge.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert b"too large" in response.data.lower()


def test_upload_stages_several_shelf_photos_in_one_batch(client, monkeypatch):
    import ai_extract

    seen = []

    def fake_extract(image_bytes, media_type, api_key=None):
        seen.append(image_bytes)
        index = len(seen)
        return [
            {
                "target_type": "inventory",
                "name": f"item {index}",
                "quantity": 1,
                "unit": "can",
                "storage": "pantry",
                "section": "canned",
            }
        ]

    monkeypatch.setattr(ai_extract, "extract_from_image", fake_extract)
    _login(client)

    response = client.post(
        "/import/upload",
        data={
            "photo": [
                (io.BytesIO(b"shelf-one"), "a.jpg"),
                (io.BytesIO(b"shelf-two"), "b.jpg"),
            ]
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert len(seen) == 2, "both photos should be sent to the model"
    names = sorted(i["name"] for i in _staging_items())
    assert names == ["item 1", "item 2"]


def test_one_bad_photo_does_not_lose_the_others(client, monkeypatch):
    """A batch of shelf photos shouldn't be all-or-nothing."""
    import ai_extract

    calls = []

    def fake_extract(image_bytes, media_type, api_key=None):
        calls.append(image_bytes)
        if len(calls) == 1:
            raise ai_extract.ExtractionError("could not read that one")
        return [
            {
                "target_type": "inventory",
                "name": "rice",
                "quantity": 1,
                "unit": "bag",
                "storage": "pantry",
                "section": "bulk-dry",
            }
        ]

    monkeypatch.setattr(ai_extract, "extract_from_image", fake_extract)
    _login(client)

    client.post(
        "/import/upload",
        data={
            "photo": [
                (io.BytesIO(b"blurry"), "a.jpg"),
                (io.BytesIO(b"good"), "b.jpg"),
            ]
        },
        content_type="multipart/form-data",
    )

    assert [i["name"] for i in _staging_items()] == ["rice"]
