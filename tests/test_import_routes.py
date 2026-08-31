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
            "estimated_value": "$40",
        },
    )

    assert response.status_code == 302
    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    md_path = os.path.join(content_dir, "vinyl-records", "kind-of-blue.md")
    assert os.path.exists(md_path)
    text = open(md_path).read()
    assert "brand: Columbia Records" in text
    assert "estimated_value: $40" in text
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
        estimated_value="$800-1200",
    )
    conn.close()

    client.post(f"/import/{item_id}/approve", data={})

    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    md_path = os.path.join(content_dir, "valuables", "turntable.md")
    text = open(md_path).read()
    assert "estimated_value: $800-1200" in text
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
