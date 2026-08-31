import os

import pytest


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    # bcrypt hash of "password1" for both test users
    test_hash = "$2b$12$rzzGNBovdhz97DdcD4Do8uWPzbP7JaRGRq6KsS9WJJ84.rpoC3nOS"
    monkeypatch.setenv("HOMEHQ_USER1_NAME", "alice")
    monkeypatch.setenv("HOMEHQ_USER1_HASH", test_hash)
    monkeypatch.setenv("HOMEHQ_USER2_NAME", "bob")
    monkeypatch.setenv("HOMEHQ_USER2_HASH", test_hash)
    monkeypatch.setenv("HOMEHQ_CONTENT_DIR", str(tmp_path / "content"))
    monkeypatch.setenv("HOMEHQ_PHOTOS_DIR", str(tmp_path / "photos"))
    monkeypatch.setenv("HOMEHQ_DB_PATH", str(tmp_path / "pantry.db"))
    os.makedirs(tmp_path / "content", exist_ok=True)
    os.makedirs(tmp_path / "photos", exist_ok=True)

    from app import create_app

    flask_app = create_app()
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()
