def test_secure_cookie_off_by_default(app):
    assert app.config["SESSION_COOKIE_SECURE"] is False


def test_secure_cookie_enabled_behind_tls_proxy(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("HOMEHQ_USER1_NAME", "alice")
    monkeypatch.setenv(
        "HOMEHQ_USER1_HASH",
        "$2b$12$rzzGNBovdhz97DdcD4Do8uWPzbP7JaRGRq6KsS9WJJ84.rpoC3nOS",
    )
    monkeypatch.setenv("HOMEHQ_CONTENT_DIR", str(tmp_path / "content"))
    monkeypatch.setenv("HOMEHQ_PHOTOS_DIR", str(tmp_path / "photos"))
    monkeypatch.setenv("HOMEHQ_DB_PATH", str(tmp_path / "pantry.db"))
    os.makedirs(tmp_path / "content", exist_ok=True)
    os.makedirs(tmp_path / "photos", exist_ok=True)
    monkeypatch.setenv("HOMEHQ_BEHIND_TLS_PROXY", "true")

    from app import create_app

    proxied_app = create_app()

    assert proxied_app.config["SESSION_COOKIE_SECURE"] is True
    from werkzeug.middleware.proxy_fix import ProxyFix

    assert isinstance(proxied_app.wsgi_app, ProxyFix)
