import os
import re


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _db_items(storage=None):
    import db

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    try:
        return db.list_items(conn, storage=storage)
    finally:
        conn.close()


def _shopping_list():
    import db

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    try:
        return db.list_shopping_list_items(conn)
    finally:
        conn.close()


def test_shopping_list_requires_login(client):
    response = client.get("/shopping-list")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_add_item_shows_up_on_shopping_list(client):
    _login(client)

    response = client.post(
        "/shopping-list/add",
        data={"name": "Rice", "storage": "pantry", "quantity_to_buy": "2"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Rice" in response.data


def test_add_item_accepts_an_amount_note(client):
    _login(client)

    response = client.post(
        "/shopping-list/add",
        data={"name": "Bananas", "storage": "fresh", "amount_note": "a bunch"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert _shopping_list()[0]["amount_note"] == "a bunch"
    assert b"a bunch" in response.data


def test_add_item_rejects_an_unrecognized_storage_value(client):
    """storage is whitelisted to pantry/freezer/fresh (see
    SHOPPING_LIST_STORAGES in app.py) -- anything else falls back to
    pantry rather than being written to the database as-is."""
    _login(client)

    response = client.post(
        "/shopping-list/add",
        data={"name": "Mystery item", "storage": "garage"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert _shopping_list()[0]["storage"] == "pantry"


def test_delete_removes_shopping_list_item(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    item_id = _shopping_list()[0]["id"]

    client.post(f"/shopping-list/{item_id}/delete")

    assert _shopping_list() == []


def test_delete_shows_an_undo_banner_and_restore_brings_the_item_back(client):
    _login(client)
    client.post(
        "/shopping-list/add", data={"name": "Rice", "storage": "pantry", "quantity_to_buy": "2"}
    )
    item_id = _shopping_list()[0]["id"]

    delete_response = client.post(f"/shopping-list/{item_id}/delete", follow_redirects=True)

    assert _shopping_list() == []
    body = delete_response.data.decode()
    assert "Removed" in body
    assert "Rice" in body
    assert f"/shopping-list/{item_id}/restore" in body

    restore_response = client.post(f"/shopping-list/{item_id}/restore", follow_redirects=True)

    assert restore_response.status_code == 200
    restored = _shopping_list()
    assert len(restored) == 1
    assert restored[0]["id"] == item_id
    assert restored[0]["name"] == "Rice"
    assert restored[0]["quantity_to_buy"] == 2


def test_double_restore_via_the_route_does_not_duplicate_the_shopping_list_item(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{item_id}/delete")

    client.post(f"/shopping-list/{item_id}/restore")
    client.post(f"/shopping-list/{item_id}/restore")

    assert len(_shopping_list()) == 1


def test_unrelated_shopping_list_item_survives_delete_and_undo_of_another(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Beans", "storage": "pantry"})
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    items = {item["name"]: item for item in _shopping_list()}
    rice_id = items["Rice"]["id"]

    client.post(f"/shopping-list/{rice_id}/delete")
    client.post(f"/shopping-list/{rice_id}/restore")

    by_name = {item["name"]: item for item in _shopping_list()}
    assert "Beans" in by_name
    assert "Rice" in by_name


def test_new_item_starts_unpurchased(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})

    assert _shopping_list()[0]["purchased"] == 0


def test_toggle_purchased_persists_across_a_page_refresh(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    item_id = _shopping_list()[0]["id"]

    client.post(f"/shopping-list/{item_id}/toggle-purchased")

    assert _shopping_list()[0]["purchased"] == 1
    # And it's really persisted, not just returned by the POST -- a fresh
    # GET (a stand-in for a page refresh) still shows it purchased.
    response = client.get("/shopping-list")
    assert response.status_code == 200
    assert _shopping_list()[0]["purchased"] == 1


def test_toggle_purchased_is_reversible(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    item_id = _shopping_list()[0]["id"]

    client.post(f"/shopping-list/{item_id}/toggle-purchased")
    assert _shopping_list()[0]["purchased"] == 1

    client.post(f"/shopping-list/{item_id}/toggle-purchased")
    assert _shopping_list()[0]["purchased"] == 0


def test_checking_a_box_never_touches_inventory(client):
    """Core behavioral split for this task: checking the box is no longer
    the same action as reconciling into inventory. Toggling purchased must
    never create or increment any pantry/freezer row."""
    _login(client)
    client.post("/pantry/add", data={"name": "Rice", "quantity": "2", "unit": "bags", "location": ""})
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    item_id = _shopping_list()[0]["id"]
    before = _db_items()

    client.post(f"/shopping-list/{item_id}/toggle-purchased")

    assert _db_items() == before
    assert _db_items(storage="pantry")[0]["quantity"] == 2


def test_toggle_purchased_on_missing_item_404s(client):
    _login(client)

    response = client.post("/shopping-list/999/toggle-purchased")

    assert response.status_code == 404


def test_shopping_list_page_shows_progress_count(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    client.post("/shopping-list/add", data={"name": "Beans", "storage": "pantry"})
    item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{item_id}/toggle-purchased")

    response = client.get("/shopping-list")

    assert response.status_code == 200
    assert b"1 of 2 purchased" in response.data


def test_finish_shopping_matches_purchased_item_into_existing_inventory(client):
    _login(client)
    client.post(
        "/pantry/add", data={"name": "Rice", "quantity": "2", "unit": "bags", "location": ""}
    )
    existing_id = _db_items(storage="pantry")[0]["id"]
    client.post("/shopping-list/add", data={"name": "Ricee", "storage": "pantry"})
    list_item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{list_item_id}/toggle-purchased")

    response = client.post(
        "/shopping-list/finish",
        data={
            f"action-{list_item_id}": "match",
            f"matched_item_id-{list_item_id}": str(existing_id),
            f"quantity-{list_item_id}": "3",
        },
    )

    assert response.status_code == 302
    assert _db_items(storage="pantry")[0]["quantity"] == 5  # 2 + 3
    assert _shopping_list() == []


def test_finish_shopping_adds_purchased_item_as_new_inventory_row(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Peas", "storage": "freezer"})
    list_item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{list_item_id}/toggle-purchased")

    response = client.post(
        "/shopping-list/finish",
        data={f"action-{list_item_id}": "new", f"quantity-{list_item_id}": "1"},
    )

    assert response.status_code == 302
    items = _db_items(storage="freezer")
    assert len(items) == 1
    assert items[0]["name"] == "Peas"
    assert items[0]["quantity"] == 1
    assert _shopping_list() == []


def test_finish_shopping_ignores_unpurchased_items(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    list_item_id = _shopping_list()[0]["id"]
    # Never toggled purchased.

    client.post(
        "/shopping-list/finish",
        data={f"action-{list_item_id}": "new", f"quantity-{list_item_id}": "1"},
    )

    assert _db_items() == []
    assert len(_shopping_list()) == 1


def test_finish_shopping_skips_a_purchased_item_missing_its_action_field(client):
    """Regression test for a stale/replayed Finish-shopping submission --
    e.g. a browser back-button resubmit of a page rendered before this item
    was purchased, or before it even existed. Without its action-<id> field
    present, the item must be left alone entirely: not reconciled, not
    turned into a phantom inventory row, and still available for a later,
    properly-rendered Finish-shopping submission."""
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{item_id}/toggle-purchased")

    # A Finish-shopping POST that says nothing at all about this item.
    response = client.post("/shopping-list/finish", data={})

    assert response.status_code == 302
    assert _db_items() == []
    remaining = _shopping_list()
    assert len(remaining) == 1
    assert remaining[0]["id"] == item_id
    assert remaining[0]["purchased"] == 1
    assert remaining[0]["reconciled_at"] is None

    # A later, properly-formed Finish-shopping submission still works.
    finish_response = client.post(
        "/shopping-list/finish",
        data={f"action-{item_id}": "new", f"quantity-{item_id}": "1"},
    )

    assert finish_response.status_code == 302
    items = _db_items(storage="pantry")
    assert len(items) == 1
    assert items[0]["name"] == "Rice"
    assert _shopping_list() == []


def test_finish_shopping_skips_a_purchased_item_with_an_empty_quantity_field(client):
    """Same stale-resubmission concern, for the quantity field specifically
    -- an empty value must not fall back to quantity_to_buy/0 and silently
    create a phantom row."""
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{item_id}/toggle-purchased")

    response = client.post(
        "/shopping-list/finish",
        data={f"action-{item_id}": "new", f"quantity-{item_id}": ""},
    )

    assert response.status_code == 302
    assert _db_items() == []
    remaining = _shopping_list()
    assert len(remaining) == 1
    assert remaining[0]["purchased"] == 1
    assert remaining[0]["reconciled_at"] is None


def test_finish_shopping_twice_does_not_double_increment_inventory(client):
    """The core correctness risk for this task: calling Finish shopping
    twice on the same purchased set must not double-apply the inventory
    write."""
    _login(client)
    client.post(
        "/pantry/add", data={"name": "Rice", "quantity": "2", "unit": "bags", "location": ""}
    )
    existing_id = _db_items(storage="pantry")[0]["id"]
    client.post("/shopping-list/add", data={"name": "Ricee", "storage": "pantry"})
    list_item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{list_item_id}/toggle-purchased")

    finish_data = {
        f"action-{list_item_id}": "match",
        f"matched_item_id-{list_item_id}": str(existing_id),
        f"quantity-{list_item_id}": "3",
    }
    client.post("/shopping-list/finish", data=finish_data)
    client.post("/shopping-list/finish", data=finish_data)

    assert _db_items(storage="pantry")[0]["quantity"] == 5  # 2 + 3, not 2 + 3 + 3


def test_finish_shopping_twice_does_not_double_create_a_new_item(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Peas", "storage": "freezer"})
    list_item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{list_item_id}/toggle-purchased")

    finish_data = {f"action-{list_item_id}": "new", f"quantity-{list_item_id}": "1"}
    client.post("/shopping-list/finish", data=finish_data)
    client.post("/shopping-list/finish", data=finish_data)

    items = _db_items(storage="freezer")
    assert len(items) == 1
    assert items[0]["quantity"] == 1


def test_fresh_item_completes_via_finish_shopping_with_no_pantry_write(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Basil", "storage": "fresh"})
    list_item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{list_item_id}/toggle-purchased")

    response = client.post("/shopping-list/finish", data={})

    assert response.status_code == 302
    assert _db_items() == []
    assert _shopping_list() == []


def test_fresh_item_finished_twice_stays_a_no_op(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Basil", "storage": "fresh"})
    list_item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{list_item_id}/toggle-purchased")

    client.post("/shopping-list/finish", data={})
    client.post("/shopping-list/finish", data={})

    assert _db_items() == []
    assert _shopping_list() == []


def test_shopping_list_page_shows_suggested_match_for_a_purchased_item(client):
    _login(client)
    client.post(
        "/pantry/add", data={"name": "Cumin", "quantity": "1", "unit": "jar", "location": ""}
    )
    client.post("/shopping-list/add", data={"name": "Cummin", "storage": "pantry"})
    list_item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{list_item_id}/toggle-purchased")

    response = client.get("/shopping-list")

    assert response.status_code == 200
    assert b"Cumin" in response.data


def test_shopping_list_page_does_not_show_a_match_suggestion_for_an_unpurchased_item(client):
    """Suggested matching only happens at Finish-shopping review time now --
    an item that hasn't been checked off shouldn't be offered a match yet."""
    _login(client)
    client.post(
        "/pantry/add", data={"name": "Cumin", "quantity": "1", "unit": "jar", "location": ""}
    )
    client.post("/shopping-list/add", data={"name": "Cummin", "storage": "pantry"})

    response = client.get("/shopping-list")

    assert response.status_code == 200
    assert b"Looks like a match" not in response.data


def test_finish_shopping_on_a_deleted_item_does_not_revive_it(client):
    """A soft-deleted shopping-list row must not be reconcilable via
    Finish shopping, mirroring the old resolve-on-a-deleted-item guard."""
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{item_id}/toggle-purchased")
    client.post(f"/shopping-list/{item_id}/delete")
    assert _shopping_list() == []

    response = client.post(
        "/shopping-list/finish",
        data={f"action-{item_id}": "new", f"quantity-{item_id}": "1"},
    )

    assert response.status_code == 302
    assert _db_items() == []
    assert _shopping_list() == []


def test_finish_shopping_skips_a_match_whose_inventory_row_was_deleted_meanwhile(client):
    """Important finding #1: the matched inventory row (matched_item_id)
    can be soft-deleted between the GET that rendered the review form and
    the Finish-shopping POST. Since claim_shopping_list_item_for_finish is
    irreversible, this must be detected and the item skipped BEFORE
    claiming it -- not silently reconciled while its inventory write
    no-ops -- exactly like the existing stale-resubmission guards above."""
    _login(client)
    client.post(
        "/pantry/add", data={"name": "Rice", "quantity": "2", "unit": "bags", "location": ""}
    )
    existing_id = _db_items(storage="pantry")[0]["id"]
    client.post("/shopping-list/add", data={"name": "Ricee", "storage": "pantry"})
    list_item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{list_item_id}/toggle-purchased")

    # The matched pantry row vanishes after the review page was rendered
    # but before Finish shopping is submitted.
    client.post(f"/inventory/{existing_id}/delete")

    response = client.post(
        "/shopping-list/finish",
        data={
            f"action-{list_item_id}": "match",
            f"matched_item_id-{list_item_id}": str(existing_id),
            f"quantity-{list_item_id}": "3",
        },
    )

    assert response.status_code == 302
    # Not reconciled, not deleted from the list -- left purchased for a
    # future, properly-rendered submission.
    remaining = _shopping_list()
    assert len(remaining) == 1
    assert remaining[0]["id"] == list_item_id
    assert remaining[0]["purchased"] == 1
    assert remaining[0]["reconciled_at"] is None
    # And no new inventory row was created either.
    assert _db_items(storage="pantry") == []


def test_finish_shopping_skips_a_match_with_a_malformed_matched_item_id(client):
    """A crafted/malformed matched_item_id-<id> field must not 500 (int()
    on a non-numeric value) -- skip the item instead."""
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    list_item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{list_item_id}/toggle-purchased")

    response = client.post(
        "/shopping-list/finish",
        data={
            f"action-{list_item_id}": "match",
            f"matched_item_id-{list_item_id}": "not-an-id",
            f"quantity-{list_item_id}": "3",
        },
    )

    assert response.status_code == 302
    assert _db_items() == []
    remaining = _shopping_list()
    assert len(remaining) == 1
    assert remaining[0]["purchased"] == 1
    assert remaining[0]["reconciled_at"] is None


def test_finish_shopping_skips_a_purchased_item_with_a_non_numeric_quantity_field(client):
    """A crafted/malformed quantity-<id> field must not 500 (float() on a
    non-numeric value) -- skip that item, same treatment as the empty-
    quantity stale-resubmission guard above."""
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})
    item_id = _shopping_list()[0]["id"]
    client.post(f"/shopping-list/{item_id}/toggle-purchased")

    response = client.post(
        "/shopping-list/finish",
        data={f"action-{item_id}": "new", f"quantity-{item_id}": "not-a-number"},
    )

    assert response.status_code == 302
    assert _db_items() == []
    remaining = _shopping_list()
    assert len(remaining) == 1
    assert remaining[0]["purchased"] == 1
    assert remaining[0]["reconciled_at"] is None


def test_purchased_toggle_has_a_real_submit_button_fallback(client):
    """Finding 3a: the checkbox's onchange="this.form.submit()" is a JS-only
    interaction. Its form must also contain a real <button type="submit">
    so the purchased toggle still works with JS disabled (a script in
    base.html hides the button once it confirms JS is present)."""
    _login(client)
    client.post("/shopping-list/add", data={"name": "Rice", "storage": "pantry"})

    body = client.get("/shopping-list").data.decode()

    assert re.search(
        r'<form class="inline-form" method="post" action="/shopping-list/\d+/toggle-purchased">'
        r'\s*<input type="checkbox"[^>]*>\s*'
        r'<button class="btn btn--sm purchase-check-submit" type="submit">',
        body,
    )


def test_source_recipe_renders_when_set(client):
    _login(client)
    import db

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    try:
        db.add_shopping_list_item(
            conn, name="Tomatoes", storage="fresh", source_recipe="Weeknight Pasta"
        )
    finally:
        conn.close()

    response = client.get("/shopping-list")

    assert response.status_code == 200
    assert b"Weeknight Pasta" in response.data


def test_source_recipe_renders_nothing_when_unset(client):
    _login(client)
    client.post("/shopping-list/add", data={"name": "Tomatoes", "storage": "fresh"})

    response = client.get("/shopping-list")

    assert response.status_code == 200
    assert b"From None" not in response.data
    assert b"source_recipe" not in response.data
