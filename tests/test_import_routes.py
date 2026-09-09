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


def test_partial_batch_failure_carries_both_outcomes_to_review_page(client, monkeypatch):
    """The bug this task fixes: redirecting to review used to drop the
    failure message silently whenever at least one photo succeeded."""
    import ai_extract

    def fake_extract(image_bytes, media_type, api_key=None):
        if image_bytes == b"blurry":
            raise ai_extract.ExtractionError("could not read that one")
        return [
            {
                "target_type": "inventory",
                "name": "rice",
                "quantity": 1,
                "unit": "bag",
                "storage": "pantry",
            }
        ]

    monkeypatch.setattr(ai_extract, "extract_from_image", fake_extract)
    _login(client)

    response = client.post(
        "/import/upload",
        data={
            "photo": [
                (io.BytesIO(b"blurry"), "shelf-a.jpg"),
                (io.BytesIO(b"good"), "shelf-b.jpg"),
            ]
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    body = response.data.decode()
    # Both outcomes are visible: the staged item, and the failure for the
    # photo that could not be read, identified by name.
    assert "rice" in body
    assert "shelf-a.jpg" in body
    assert "could not read that one" in body
    assert "1 item ready to review" in body
    assert "1 photo could not be read" in body

    failed = _staging_items(status="failed")
    assert len(failed) == 1
    assert failed[0]["name"] == "shelf-a.jpg"
    assert failed[0]["error"] == "could not read that one"
    # Preserved: the successful item from the same batch is still there.
    assert [i["name"] for i in _staging_items()] == ["rice"]


def test_retry_of_failed_file_stages_it_without_touching_the_rest(client, monkeypatch):
    import ai_extract

    def fake_extract(image_bytes, media_type, api_key=None):
        if image_bytes == b"blurry":
            raise ai_extract.ExtractionError("could not read that one")
        return [
            {
                "target_type": "inventory",
                "name": "rice",
                "quantity": 1,
                "unit": "bag",
                "storage": "pantry",
            }
        ]

    monkeypatch.setattr(ai_extract, "extract_from_image", fake_extract)
    _login(client)

    client.post(
        "/import/upload",
        data={
            "photo": [
                (io.BytesIO(b"blurry"), "shelf-a.jpg"),
                (io.BytesIO(b"good"), "shelf-b.jpg"),
            ]
        },
        content_type="multipart/form-data",
    )
    failed_id = _staging_items(status="failed")[0]["id"]

    # The retry now succeeds -- every call from here on returns the same row.
    monkeypatch.setattr(
        ai_extract,
        "extract_from_image",
        lambda image_bytes, media_type, api_key=None: [
            {
                "target_type": "inventory",
                "name": "beans",
                "quantity": 1,
                "unit": "can",
                "storage": "pantry",
            }
        ],
    )

    response = client.post(f"/import/{failed_id}/retry", follow_redirects=True)

    assert response.status_code == 200
    assert _staging_items(status="failed") == []
    names = sorted(i["name"] for i in _staging_items())
    # The originally-successful item ("rice") is untouched, and the retried
    # photo is now staged too -- with no duplication of the batch's items.
    assert names == ["beans", "rice"]


def test_retrying_a_resolved_failure_is_a_404_not_a_restage(client, monkeypatch):
    """Once a retry has resolved a failed row (staged it and deleted the
    row), the id no longer refers to anything -- a second retry POST for the
    same id (a slow double-submit landing after the first finished) must not
    silently re-stage anything."""
    import ai_extract

    monkeypatch.setattr(
        ai_extract,
        "extract_from_image",
        lambda image_bytes, media_type, api_key=None: (_ for _ in ()).throw(
            ai_extract.ExtractionError("nope")
        ),
    )
    _login(client)

    client.post(
        "/import/upload",
        data={"photo": (io.BytesIO(b"blurry"), "shelf-a.jpg")},
        content_type="multipart/form-data",
    )
    failed_id = _staging_items(status="failed")[0]["id"]

    monkeypatch.setattr(
        ai_extract,
        "extract_from_image",
        lambda image_bytes, media_type, api_key=None: [
            {"target_type": "inventory", "name": "beans", "quantity": 1, "storage": "pantry"}
        ],
    )

    client.post(f"/import/{failed_id}/retry")
    response = client.post(f"/import/{failed_id}/retry")

    assert response.status_code == 404
    assert [i["name"] for i in _staging_items()] == ["beans"]


def test_retry_in_flight_guard_blocks_a_concurrent_duplicate(client, monkeypatch):
    """Exercises the server-side guard directly: a row already claimed as
    'retrying' (a first request still in flight) must not be re-processed by
    a second request for the same id -- this is the "server-side batch
    identity" the task asks for, not anything client-side."""
    import ai_extract
    import db

    monkeypatch.setattr(
        ai_extract,
        "extract_from_image",
        lambda image_bytes, media_type, api_key=None: [
            {"target_type": "inventory", "name": "beans", "quantity": 1, "storage": "pantry"}
        ],
    )
    _login(client)

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    failed_id = db.add_staging_item(
        conn,
        target_type="failed",
        name="shelf-a.jpg",
        status="failed",
        error="could not read that one",
        source_image_path=os.path.join(os.environ["HOMEHQ_UPLOADS_DIR"], "shelf-a.jpg"),
        media_type="image/jpeg",
    )
    with open(os.path.join(os.environ["HOMEHQ_UPLOADS_DIR"], "shelf-a.jpg"), "wb") as f:
        f.write(b"fake")
    # Simulate a first retry request already in flight, having claimed the
    # row but not yet finished.
    db.set_staging_item_status(conn, failed_id, "retrying")
    conn.close()

    response = client.post(f"/import/{failed_id}/retry", follow_redirects=True)

    assert response.status_code == 200
    # Nothing staged -- the in-flight guard made this a no-op rather than a
    # second extraction pass.
    assert _staging_items() == []


def test_retry_unexpected_error_does_not_strand_the_row_in_retrying(client, monkeypatch):
    """If extraction blows up with something other than ExtractionError (a
    bug, an unwrapped network error), the row must come back to 'failed'
    rather than being stuck invisibly in 'retrying' forever. TESTING=True
    (set by the app fixture) makes Flask re-raise the exception instead of
    turning it into a 500 response, so this exercises the same code path
    production's own error handling would hit."""
    import ai_extract

    monkeypatch.setattr(
        ai_extract,
        "extract_from_image",
        lambda image_bytes, media_type, api_key=None: (_ for _ in ()).throw(
            ai_extract.ExtractionError("nope")
        ),
    )
    _login(client)
    client.post(
        "/import/upload",
        data={"photo": (io.BytesIO(b"blurry"), "shelf-a.jpg")},
        content_type="multipart/form-data",
    )
    failed_id = _staging_items(status="failed")[0]["id"]

    def boom(image_bytes, media_type, api_key=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(ai_extract, "extract_from_image", boom)

    try:
        client.post(f"/import/{failed_id}/retry")
    except RuntimeError:
        pass

    failed = _staging_items(status="failed")
    assert len(failed) == 1
    assert failed[0]["id"] == failed_id
    assert failed[0]["status"] == "failed"


def test_retry_failure_leaves_the_row_retryable_again(client, monkeypatch):
    import ai_extract

    monkeypatch.setattr(
        ai_extract,
        "extract_from_image",
        lambda image_bytes, media_type, api_key=None: (_ for _ in ()).throw(
            ai_extract.ExtractionError("still blurry")
        ),
    )
    _login(client)

    client.post(
        "/import/upload",
        data={"photo": (io.BytesIO(b"blurry"), "shelf-a.jpg")},
        content_type="multipart/form-data",
    )
    failed_id = _staging_items(status="failed")[0]["id"]

    response = client.post(f"/import/{failed_id}/retry", follow_redirects=True)

    assert response.status_code == 200
    failed = _staging_items(status="failed")
    assert len(failed) == 1
    assert failed[0]["id"] == failed_id
    assert failed[0]["error"] == "still blurry"
    assert b"still blurry" in response.data


def test_discard_removes_a_failed_photo_notice(client, monkeypatch):
    import ai_extract

    monkeypatch.setattr(
        ai_extract,
        "extract_from_image",
        lambda image_bytes, media_type, api_key=None: (_ for _ in ()).throw(
            ai_extract.ExtractionError("nope")
        ),
    )
    _login(client)

    client.post(
        "/import/upload",
        data={"photo": (io.BytesIO(b"blurry"), "shelf-a.jpg")},
        content_type="multipart/form-data",
    )
    failed_id = _staging_items(status="failed")[0]["id"]

    response = client.post(f"/import/{failed_id}/reject", follow_redirects=True)

    assert response.status_code == 200
    assert _staging_items(status="failed") == []


def test_import_upload_form_disables_submit_while_pending():
    """Server-verifiable half of the pending-state requirement: the form
    itself carries the pending-message hook and a polite live region for the
    client-side JS to drive -- without JS, nothing here should assume the
    request already completed."""
    with open("templates/import_upload.html") as f:
        html = f.read()

    assert 'data-pending-message="Reading photos' in html
    assert 'aria-live="polite"' in html
    assert 'role="alert"' not in html


def test_retry_form_has_a_live_status_region_that_actually_resolves(client, monkeypatch):
    """The Retry form sits inside a list row alongside a Discard form, so its
    next DOM sibling is NOT a status-live element -- unlike the standalone
    upload/capture/paste forms. It must instead point at its own live region
    via data-pending-status, and that id must resolve to a real
    aria-live="polite" element actually present in the rendered page (not
    just asserted to exist somewhere in the template source)."""
    import re

    import ai_extract
    import db

    monkeypatch.setattr(
        ai_extract,
        "extract_from_image",
        lambda image_bytes, media_type, api_key=None: (_ for _ in ()).throw(
            ai_extract.ExtractionError("could not read that one")
        ),
    )
    _login(client)
    client.post(
        "/import/upload",
        data={"photo": (io.BytesIO(b"blurry"), "shelf-a.jpg")},
        content_type="multipart/form-data",
    )
    failed_id = _staging_items(status="failed")[0]["id"]

    response = client.get("/import")
    body = response.data.decode()

    retry_form_match = re.search(
        r'<form[^>]*action="/import/%d/retry"[^>]*>' % failed_id, body
    )
    assert retry_form_match, "expected a retry form for the failed item"
    retry_form_html = retry_form_match.group(0)

    status_id_match = re.search(r'data-pending-status="([^"]+)"', retry_form_html)
    assert status_id_match, "retry form must name its own status region"
    status_id = status_id_match.group(1)

    # That id must resolve to a real element in the page -- not merely be a
    # string present somewhere -- and that element must be a polite live
    # region, not role="alert".
    status_el_match = re.search(
        r'<span id="%s"([^>]*)></span>' % re.escape(status_id), body
    )
    assert status_el_match, f"no element with id={status_id!r} found in the rendered page"
    status_el_attrs = status_el_match.group(1)
    assert 'aria-live="polite"' in status_el_attrs
    assert 'role="status"' in status_el_attrs
    assert "alert" not in status_el_attrs


def test_import_photo_route_requires_login(client):
    import db

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    upload_path = os.path.join(os.environ["HOMEHQ_UPLOADS_DIR"], "shelf.jpg")
    with open(upload_path, "wb") as f:
        f.write(b"fake-jpeg-bytes")
    item_id = db.add_staging_item(
        conn, target_type="inventory", name="Rice", source_image_path=upload_path
    )
    conn.close()

    response = client.get(f"/import/{item_id}/photo")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_import_photo_route_serves_the_staging_items_photo(client):
    import db

    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    upload_path = os.path.join(os.environ["HOMEHQ_UPLOADS_DIR"], "shelf.jpg")
    with open(upload_path, "wb") as f:
        f.write(b"fake-jpeg-bytes")
    item_id = db.add_staging_item(
        conn, target_type="inventory", name="Rice", source_image_path=upload_path
    )
    conn.close()

    response = client.get(f"/import/{item_id}/photo")

    assert response.status_code == 200
    assert response.data == b"fake-jpeg-bytes"


def test_import_photo_route_404s_for_nonexistent_item(client):
    _login(client)

    response = client.get("/import/999999/photo")

    assert response.status_code == 404


def test_import_photo_route_404s_for_a_text_capture_with_no_photo(client):
    import db

    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    item_id = db.add_staging_item(conn, target_type="inventory", name="Onions")
    conn.close()

    response = client.get(f"/import/{item_id}/photo")

    assert response.status_code == 404


def test_import_photo_route_cannot_be_used_to_serve_an_arbitrary_path(client, tmp_path):
    """The route is keyed on the staging item's own id -- there is no
    filename/path parameter for a client to supply or manipulate. This test
    exercises the defensive commonpath backstop directly: even if
    source_image_path in the database somehow pointed outside uploads_dir
    (corruption, a bug elsewhere), the route must still refuse to serve it
    rather than trusting the stored path blindly."""
    import db

    _login(client)
    outside_secret = tmp_path / "secret.txt"
    outside_secret.write_text("top secret")

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    item_id = db.add_staging_item(
        conn, target_type="inventory", name="Rice", source_image_path=str(outside_secret)
    )
    conn.close()

    response = client.get(f"/import/{item_id}/photo")

    assert response.status_code == 404


def test_import_photo_route_has_no_client_supplied_path_parameter(client):
    """Sanity check on the URL shape itself: unlike /photos/<path:filename>,
    this route must not accept a filename/path segment at all -- only the
    item's own integer id."""
    import db

    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    upload_path = os.path.join(os.environ["HOMEHQ_UPLOADS_DIR"], "shelf.jpg")
    with open(upload_path, "wb") as f:
        f.write(b"fake")
    item_id = db.add_staging_item(
        conn, target_type="inventory", name="Rice", source_image_path=upload_path
    )
    conn.close()

    # Appending an arbitrary path segment after the id must not resolve --
    # the route is /import/<int:item_id>/photo, nothing more.
    response = client.get(f"/import/{item_id}/photo/../../etc/passwd")

    assert response.status_code == 404


def test_review_page_groups_rows_by_source_photo(client):
    import db

    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    for name, path in (
        ("Rice", "/uploads/photo-a.jpg"),
        ("Beans", "/uploads/photo-a.jpg"),
        ("Milk", "/uploads/photo-b.jpg"),
    ):
        db.add_staging_item(conn, target_type="inventory", name=name, source_image_path=path)
    conn.close()

    response = client.get("/import")
    body = response.data.decode()

    assert response.status_code == 200
    # Two distinct photo previews, one per source image.
    assert body.count('alt="Photo for Rice"') == 1
    assert body.count('alt="Photo for Milk"') == 1
    # Rice and Beans (same photo) appear inside one group; find the group
    # boundary via the two enlarge links and check both names fall inside
    # the first one, not split across groups.
    first_group_end = body.index('alt="Photo for Milk"')
    assert "Rice" in body[:first_group_end]
    assert "Beans" in body[:first_group_end]


def test_review_page_gives_text_captures_a_no_photo_presentation(client):
    """A text-capture staging row (no source_image_path) must not render a
    broken <img> or an empty photo slot."""
    import db

    _login(client)
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    db.add_staging_item(conn, target_type="inventory", name="Garlic")
    conn.close()

    response = client.get("/import")
    body = response.data.decode()

    assert response.status_code == 200
    assert "Garlic" in body
    assert "<img" not in body or "Photo for Garlic" not in body
    assert "import-group--nophoto" in body


def test_review_page_match_panel_shows_unit_and_storage(client):
    import db

    _login(client)
    client.post(
        "/pantry/add",
        data={"name": "Rice", "quantity": "3", "unit": "bags", "location": ""},
    )
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    db.add_staging_item(
        conn, target_type="inventory", name="Rice", quantity=1, storage="pantry"
    )
    conn.close()

    response = client.get("/import")
    body = response.data.decode()

    assert response.status_code == 200
    assert "Looks like a match" in body
    # Matched item's own quantity, unit, and storage are shown, not just its
    # name and a quantity input for the new value.
    assert "3" in body
    assert "bags" in body
    assert "Pantry" in body


def test_upload_page_error_render_has_no_batch_or_failed_rows(client, monkeypatch):
    """The existing all-failed inline-error path is unchanged: it renders on
    the upload page itself (no redirect), and the pending 'failed' rows it
    records are not shown as staged/pending items."""
    import ai_extract

    monkeypatch.setattr(
        ai_extract,
        "extract_from_image",
        lambda image_bytes, media_type, api_key=None: (_ for _ in ()).throw(
            ai_extract.ExtractionError("could not read image")
        ),
    )
    _login(client)

    response = client.post(
        "/import/upload",
        data={"photo": (io.BytesIO(b"fake-jpeg-bytes"), "receipt.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert _staging_items() == []
