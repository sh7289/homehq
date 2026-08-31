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
