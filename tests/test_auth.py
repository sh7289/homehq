def test_unauthenticated_request_redirects_to_login(client):
    response = client.get("/pantry")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_with_correct_password_grants_access(client):
    client.post("/login", data={"username": "alice", "password": "password1"})

    response = client.get("/pantry")

    assert response.status_code == 200


def test_login_with_wrong_password_stays_logged_out(client):
    client.post("/login", data={"username": "alice", "password": "wrong"})

    response = client.get("/pantry")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def _fail_login(client, times, username="alice", password="wrong"):
    for _ in range(times):
        response = client.post(
            "/login", data={"username": username, "password": password}
        )
    return response


def test_repeated_failures_lock_the_account_out(client):
    import db

    _fail_login(client, db.LOCKOUT_THRESHOLD)

    response = client.post("/login", data={"username": "alice", "password": "password1"})

    assert response.status_code == 429
    assert b"too many" in response.data.lower()


def test_lockout_refuses_even_the_correct_password(client):
    import db

    _fail_login(client, db.LOCKOUT_THRESHOLD)

    client.post("/login", data={"username": "alice", "password": "password1"})
    response = client.get("/pantry")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_a_successful_login_resets_the_counter(client):
    import db

    _fail_login(client, db.LOCKOUT_THRESHOLD - 1)

    good = client.post("/login", data={"username": "alice", "password": "password1"})
    assert good.status_code == 302

    client.post("/logout")
    _fail_login(client, db.LOCKOUT_THRESHOLD - 1)
    still_ok = client.post("/login", data={"username": "alice", "password": "password1"})
    assert still_ok.status_code == 302


def test_locking_one_user_does_not_lock_the_other(client):
    import db

    _fail_login(client, db.LOCKOUT_THRESHOLD, username="alice")

    response = client.post("/login", data={"username": "bob", "password": "password1"})

    assert response.status_code == 302


def test_login_failure_is_logged_without_the_password(client, caplog):
    with caplog.at_level("WARNING"):
        client.post("/login", data={"username": "alice", "password": "hunter2"})

    assert "alice" in caplog.text
    assert "hunter2" not in caplog.text


def test_session_hardening_is_configured(app):
    from datetime import timedelta

    assert app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(days=14)
    assert app.login_manager.session_protection == "strong"
