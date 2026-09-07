"""Tests for Task 2: faster inventory operation.

Covers query/filter preservation across adjust/delete/update, the new
All/Past date/Due soon/Unsorted filter controls, the empty-vs-no-results
distinction, add/edit validation error handling, and the search input's
accessible label.
"""

import os
from datetime import date, timedelta

import db


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _conn(app):
    with app.app_context():
        conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
        db.init_db(conn)
        return conn


def _db_items(storage=None):
    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    try:
        return db.list_items(conn, storage=storage)
    finally:
        conn.close()


# ---------- accessible label (Task 9 fold-in) ----------


def test_search_input_has_accessible_label(client):
    _login(client)

    body = client.get("/pantry").data.decode()

    assert '<label class="visually-hidden" for="q">Search pantry</label>' in body
    assert 'id="q"' in body


# ---------- query/filter preservation across adjust/delete/update ----------


def test_adjust_redirects_back_to_filtered_search_results(client, app):
    conn = _conn(app)
    db.add_item(conn, name="rice", quantity=2, unit="bag", location="", section="bulk-dry")
    db.add_item(conn, name="black beans", quantity=1, unit="can", location="", section="canned")
    conn.close()
    _login(client)

    # Confirm the search actually narrows the page first.
    body = client.get("/pantry?q=rice").data.decode()
    assert "rice" in body
    assert "black beans" not in body

    item_id = [i for i in _db_items(storage="pantry") if i["name"] == "rice"][0]["id"]

    response = client.post(
        f"/inventory/{item_id}/adjust",
        data={"delta": "1", "storage": "pantry", "q": "rice", "filter": "all"},
    )

    assert response.status_code == 302
    location = response.headers["Location"]
    assert "q=rice" in location
    assert location.endswith(f"#row-{item_id}")

    # Following the redirect lands back on the filtered "rice" results, not
    # the unfiltered list -- this is the acceptance-criteria scenario.
    followed = client.get("/pantry?q=rice")
    followed_body = followed.data.decode()
    assert "rice" in followed_body
    assert "black beans" not in followed_body
    assert [i for i in _db_items(storage="pantry") if i["name"] == "rice"][0]["quantity"] == 3


def test_delete_preserves_search_query_and_active_filter(client, app):
    conn = _conn(app)
    db.add_item(conn, name="old spice", quantity=1, unit="jar", location="", section="spices")
    conn.close()
    _login(client)
    item_id = _db_items(storage="pantry")[0]["id"]

    response = client.post(
        f"/inventory/{item_id}/delete",
        data={"storage": "pantry", "q": "old", "filter": "unsorted"},
    )

    assert response.status_code == 302
    location = response.headers["Location"]
    assert "q=old" in location
    assert "filter=unsorted" in location
    assert _db_items() == []


def test_update_redirects_back_with_query_and_filter_preserved(client, app):
    conn = _conn(app)
    item_id = db.add_item(conn, name="cumin", quantity=1, unit="jar", location="")
    conn.close()
    _login(client)

    response = client.post(
        f"/inventory/{item_id}/update",
        data={
            "storage": "pantry",
            "quantity": "2",
            "unit": "jar",
            "section": "spices",
            "q": "cumin",
            "filter": "expired",
        },
    )

    assert response.status_code == 302
    location = response.headers["Location"]
    assert "q=cumin" in location
    assert "filter=expired" in location
    assert location.endswith(f"#row-{item_id}")


def test_plain_adjust_with_no_search_or_filter_redirects_without_extra_query(client, app):
    """A bare adjust (no q/filter posted) must still redirect cleanly."""
    conn = _conn(app)
    item_id = db.add_item(conn, name="rice", quantity=2, unit="bags", location="")
    conn.close()
    _login(client)

    response = client.post(
        f"/inventory/{item_id}/adjust", data={"delta": "1", "storage": "pantry"}
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/pantry#row-{item_id}")
    assert _db_items()[0]["quantity"] == 3


# ---------- filter controls produce correct, non-overlapping row sets ----------


def _seed_filterable_items(app):
    conn = _conn(app)
    expired_date = (date.today() - timedelta(days=5)).isoformat()
    soon_date = (date.today() + timedelta(days=10)).isoformat()
    far_date = (date.today() + timedelta(days=200)).isoformat()
    db.add_item(
        conn, name="old bread", quantity=1, unit="loaf", location="",
        section="bulk-dry", expiry_date=expired_date,
    )
    db.add_item(
        conn, name="soon milk", quantity=1, unit="carton", location="",
        section="other", expiry_date=soon_date,
    )
    db.add_item(
        conn, name="fresh rice", quantity=1, unit="bag", location="",
        section="bulk-dry", expiry_date=far_date,
    )
    db.add_item(conn, name="mystery jar", quantity=1, unit="", location="")
    conn.close()


def test_filter_all_shows_every_item(client, app):
    _seed_filterable_items(app)
    _login(client)

    body = client.get("/pantry?filter=all").data.decode()

    for name in ("old bread", "soon milk", "fresh rice", "mystery jar"):
        assert name in body


def test_filter_expired_shows_only_past_date_items(client, app):
    _seed_filterable_items(app)
    _login(client)

    body = client.get("/pantry?filter=expired").data.decode()

    assert "old bread" in body
    assert "soon milk" not in body
    assert "fresh rice" not in body
    # "mystery jar" has no section, so it legitimately still appears in the
    # (collapsed, filter-independent) bulk section-sorter -- see
    # test_filter_unsorted_shows_only_items_without_a_section for its own
    # filter's row set.


def test_filter_expiring_soon_shows_only_due_soon_items(client, app):
    _seed_filterable_items(app)
    _login(client)

    body = client.get("/pantry?filter=expiring_soon").data.decode()

    assert "soon milk" in body
    assert "old bread" not in body
    assert "fresh rice" not in body


def test_filter_unsorted_shows_only_items_without_a_section(client, app):
    _seed_filterable_items(app)
    _login(client)

    body = client.get("/pantry?filter=unsorted").data.decode()

    assert "mystery jar" in body
    assert "old bread" not in body
    assert "soon milk" not in body
    assert "fresh rice" not in body


def test_unknown_filter_value_falls_back_to_all(client, app):
    _seed_filterable_items(app)
    _login(client)

    body = client.get("/pantry?filter=bogus").data.decode()

    for name in ("old bread", "soon milk", "fresh rice", "mystery jar"):
        assert name in body


# ---------- estimated dates keep their (est.) marker ----------


def test_estimated_expiry_keeps_est_marker_under_filter(client, app):
    conn = _conn(app)
    db.add_item(
        conn,
        name="dried lentils",
        quantity=1,
        unit="bag",
        location="",
        section="bulk-dry",
        acquired_date="2024-01-01",
        shelf_life_days=30,
    )
    conn.close()
    _login(client)

    body = client.get("/pantry?filter=expired").data.decode()

    assert "dried lentils" in body
    assert "(est.)" in body


# ---------- genuinely empty vs. no-results-for-this-search/filter ----------


def test_genuinely_empty_inventory_shows_empty_message(client):
    _login(client)

    body = client.get("/pantry").data.decode()

    assert "Nothing logged in the pantry yet" in body
    assert "Clear filters" not in body


def test_search_with_no_matches_offers_clear_filters(client, app):
    conn = _conn(app)
    db.add_item(conn, name="rice", quantity=1, unit="bag", location="")
    conn.close()
    _login(client)

    body = client.get("/pantry?q=nonexistentitem").data.decode()

    assert "Nothing logged in the pantry yet" not in body
    assert "No items match your search." in body
    assert 'href="/pantry">Clear filters' in body


def test_filter_with_no_matches_offers_clear_filters(client, app):
    conn = _conn(app)
    db.add_item(conn, name="rice", quantity=1, unit="bag", location="", section="bulk-dry")
    conn.close()
    _login(client)

    body = client.get("/pantry?filter=expired").data.decode()

    assert "Nothing logged in the pantry yet" not in body
    assert "No items match this filter." in body
    assert 'href="/pantry">Clear filters' in body


def test_search_and_filter_combined_no_matches_message(client, app):
    conn = _conn(app)
    db.add_item(conn, name="rice", quantity=1, unit="bag", location="", section="bulk-dry")
    conn.close()
    _login(client)

    body = client.get("/pantry?q=rice&filter=expired").data.decode()

    assert "No items match your search and filter." in body


# ---------- add-item validation retains input instead of losing it ----------


def test_add_item_without_name_shows_error_and_retains_other_values(client, app):
    _login(client)

    response = client.post(
        "/pantry/add",
        data={"name": "", "quantity": "2", "unit": "bags", "section": "bulk-dry"},
    )

    assert response.status_code == 200
    body = response.data.decode()
    assert "Give the item a name." in body
    assert 'name="quantity" value="2"' in body
    assert 'name="unit" value="bags"' in body
    assert _db_items() == []


def test_add_item_without_quantity_shows_error_and_retains_name(client, app):
    _login(client)

    response = client.post("/pantry/add", data={"name": "Rice", "quantity": ""})

    assert response.status_code == 200
    body = response.data.decode()
    assert "Enter a quantity." in body
    assert 'name="name" value="Rice"' in body
    assert _db_items() == []


def test_add_item_with_non_numeric_quantity_shows_error(client, app):
    _login(client)

    response = client.post("/pantry/add", data={"name": "Rice", "quantity": "abc"})

    assert response.status_code == 200
    body = response.data.decode()
    assert "Quantity must be a number." in body
    assert _db_items() == []


def test_add_item_with_non_numeric_shelf_life_shows_error(client, app):
    _login(client)

    response = client.post(
        "/pantry/add",
        data={"name": "Rice", "quantity": "1", "shelf_life_days": "forever"},
    )

    assert response.status_code == 200
    body = response.data.decode()
    assert "Shelf life override must be a whole number of days." in body
    assert _db_items() == []


def test_add_item_success_still_redirects_and_creates_item(client):
    _login(client)

    response = client.post(
        "/pantry/add",
        data={"name": "Rice", "quantity": "2", "unit": "bags", "q": "", "filter": "all"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/pantry")
    assert _db_items()[0]["name"] == "Rice"


# ---------- edit-item validation retains input and keeps the panel open ----------


def test_update_item_without_quantity_shows_error_and_keeps_entered_values(client, app):
    conn = _conn(app)
    item_id = db.add_item(conn, name="cumin", quantity=1, unit="jar", location="")
    conn.close()
    _login(client)

    response = client.post(
        f"/inventory/{item_id}/update",
        data={"storage": "pantry", "quantity": "", "unit": "jar-ish", "location": "top shelf"},
    )

    assert response.status_code == 200
    body = response.data.decode()
    assert "Enter a quantity." in body
    # The edit panel for this item is rendered open (no `hidden`), and shows
    # what the user just typed rather than the unchanged DB value.
    assert f'id="edit-{item_id}" hidden' not in body
    assert f'id="edit-{item_id}"' in body
    assert 'name="unit" value="jar-ish"' in body
    assert 'name="location" value="top shelf"' in body
    # Nothing was persisted.
    assert _db_items()[0]["quantity"] == 1
    assert _db_items()[0]["unit"] == "jar"


def test_update_item_with_non_numeric_quantity_shows_error(client, app):
    conn = _conn(app)
    item_id = db.add_item(conn, name="cumin", quantity=1, unit="jar", location="")
    conn.close()
    _login(client)

    response = client.post(
        f"/inventory/{item_id}/update",
        data={"storage": "pantry", "quantity": "lots", "unit": "jar"},
    )

    assert response.status_code == 200
    assert "Quantity must be a number." in response.data.decode()
    assert _db_items()[0]["quantity"] == 1


# ---------- bulk section-sorter is now its own opt-in mode ----------


def test_bulk_sorter_is_collapsed_by_default(client, app):
    conn = _conn(app)
    db.add_item(conn, name="mystery jar", quantity=1, unit="", location="")
    conn.close()
    _login(client)

    body = client.get("/pantry").data.decode()

    assert "Sort into sections" in body
    # The bulk-sort <details> has no `open` attribute (unlike the always-open
    # section groups above it), so it starts collapsed.
    assert '<details class="section-group">' in body
