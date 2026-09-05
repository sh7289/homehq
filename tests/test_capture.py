import json
import os

import pytest

import ai_extract


class _FakeMessage:
    def __init__(self, text):
        self.content = [type("Block", (), {"text": text})()]


class _FakeClient:
    """Minimal stand-in for anthropic.Anthropic."""

    def __init__(self, response_text):
        self.response_text = response_text
        self.calls = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeMessage(self.response_text)


def _pantry_response(items):
    return json.dumps({"kind": "pantry_items", "items": items})


# --- parsing --------------------------------------------------------------


def test_parses_pantry_items_into_inventory_rows():
    rows = ai_extract.parse_extraction_response(
        _pantry_response(
            [{"name": "chickpeas", "quantity": 2, "unit": "can", "storage": "pantry",
              "section": "canned"}]
        )
    )

    assert rows == [
        {
            "target_type": "inventory",
            "name": "chickpeas",
            "quantity": 2,
            "unit": "can",
            "storage": "pantry",
            "section": "canned",
        }
    ]


def test_pantry_item_defaults_to_pantry_storage():
    rows = ai_extract.parse_extraction_response(
        _pantry_response([{"name": "rice", "quantity": 1, "unit": "bag"}])
    )

    assert rows[0]["storage"] == "pantry"


def test_invalid_section_falls_back_to_other():
    rows = ai_extract.parse_extraction_response(
        _pantry_response([{"name": "rice", "section": "invented-section"}])
    )

    assert rows[0]["section"] == "other"


def test_section_is_validated_against_the_items_own_storage():
    """'canned' is a pantry section; it is meaningless in the freezer."""
    rows = ai_extract.parse_extraction_response(
        _pantry_response([{"name": "peas", "storage": "freezer", "section": "canned"}])
    )

    assert rows[0]["section"] == "other"


def test_quantity_may_be_null():
    """'some rice left' is legitimate -- inventing a number is worse."""
    rows = ai_extract.parse_extraction_response(
        _pantry_response([{"name": "rice", "quantity": None, "unit": None}])
    )

    assert rows[0]["quantity"] is None


def test_pantry_item_without_a_name_is_rejected():
    with pytest.raises(ai_extract.ExtractionError, match="name"):
        ai_extract.parse_extraction_response(_pantry_response([{"quantity": 2}]))


# --- client wiring --------------------------------------------------------


def test_extract_from_text_sends_the_users_words_to_the_model():
    client = _FakeClient(_pantry_response([{"name": "rice"}]))

    rows = ai_extract.extract_from_text("two bags of rice", client=client)

    sent = client.calls[0]["messages"][0]["content"]
    assert "two bags of rice" in json.dumps(sent)
    assert rows[0]["name"] == "rice"


def test_extract_from_text_surfaces_bad_json_as_extraction_error():
    client = _FakeClient("I'm afraid I can't do that")

    with pytest.raises(ai_extract.ExtractionError):
        ai_extract.extract_from_text("anything", client=client)


# --- route ----------------------------------------------------------------


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _staging_items():
    import db

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    try:
        return db.list_staging_items(conn, status="pending")
    finally:
        conn.close()


def test_capture_page_requires_login(client):
    response = client.get("/capture")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_capture_stages_described_items(client, monkeypatch):
    def fake_extract(text, api_key=None):
        assert "chickpeas" in text
        return [
            {
                "target_type": "inventory",
                "name": "chickpeas",
                "quantity": 2,
                "unit": "can",
                "storage": "pantry",
                "section": "canned",
            }
        ]

    monkeypatch.setattr(ai_extract, "extract_from_text", fake_extract)
    _login(client)

    response = client.post("/capture", data={"text": "two cans of chickpeas"})

    assert response.status_code == 302
    assert "/import" in response.headers["Location"]
    staged = _staging_items()
    assert len(staged) == 1
    assert staged[0]["name"] == "chickpeas"
    assert staged[0]["section"] == "canned"


def test_capture_rejects_empty_text(client, monkeypatch):
    called = []
    monkeypatch.setattr(
        ai_extract, "extract_from_text", lambda *a, **k: called.append(1) or []
    )
    _login(client)

    response = client.post("/capture", data={"text": "   "})

    assert response.status_code == 200
    assert b"Describe" in response.data or b"describe" in response.data
    assert called == [], "should not call the model for empty input"


def test_approving_a_staged_item_carries_its_section_through(client, monkeypatch):
    import db

    def fake_extract(text, api_key=None):
        return [
            {
                "target_type": "inventory",
                "name": "chickpeas",
                "quantity": 2,
                "unit": "can",
                "storage": "pantry",
                "section": "canned",
            }
        ]

    monkeypatch.setattr(ai_extract, "extract_from_text", fake_extract)
    _login(client)
    client.post("/capture", data={"text": "two cans of chickpeas"})
    staged_id = _staging_items()[0]["id"]

    client.post(f"/import/{staged_id}/approve", data={"quantity": "2"})

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    try:
        assert db.list_items(conn)[0]["section"] == "canned"
    finally:
        conn.close()


def test_review_screen_lets_you_correct_the_section(client, monkeypatch):
    monkeypatch.setattr(
        ai_extract,
        "extract_from_text",
        lambda text, api_key=None: [
            {
                "target_type": "inventory",
                "name": "chickpeas",
                "quantity": 2,
                "unit": "can",
                "storage": "pantry",
                "section": "canned",
            }
        ],
    )
    _login(client)
    client.post("/capture", data={"text": "two cans of chickpeas"})

    body = client.get("/import").data.decode()

    assert 'name="section"' in body
    assert "Spices &amp; Herbs" in body or "Spices & Herbs" in body


def test_capture_reports_extraction_failure(client, monkeypatch):
    def boom(text, api_key=None):
        raise ai_extract.ExtractionError("Model response was not valid JSON")

    monkeypatch.setattr(ai_extract, "extract_from_text", boom)
    _login(client)

    response = client.post("/capture", data={"text": "two cans of chickpeas"})

    assert response.status_code == 200
    assert b"not valid JSON" in response.data
    assert _staging_items() == []
