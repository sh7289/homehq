import os


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _write_item(content_dir, category, slug, name, estimated_value=None):
    category_dir = os.path.join(content_dir, category)
    os.makedirs(category_dir, exist_ok=True)
    value_line = f"estimated_value: {estimated_value}\n" if estimated_value else ""
    with open(os.path.join(category_dir, f"{slug}.md"), "w") as f:
        f.write(f"---\nname: {name}\ncategory: {category}\n{value_line}---\n")


def test_report_requires_login(client):
    response = client.get("/report")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_report_shows_items_and_total_value(client, app):
    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    _write_item(content_dir, "valuables", "turntable", "Turntable", estimated_value=1000)
    _write_item(content_dir, "kitchen", "pan", "Pan")
    app.catalog.reload()
    _login(client)

    response = client.get("/report")

    assert response.status_code == 200
    assert b"Turntable" in response.data
    assert b"Pan" in response.data
    assert b"1,000" in response.data


def test_report_csv_requires_login(client):
    response = client.get("/report.csv")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_report_csv_includes_all_categories(client, app):
    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    _write_item(content_dir, "valuables", "turntable", "Turntable", estimated_value=1000)
    app.catalog.reload()
    _login(client)

    response = client.get("/report.csv")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/csv")
    body = response.data.decode("utf-8-sig")
    assert "Turntable" in body
    assert "1000" in body
