import os
import re


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _write_recipe(app, filename, text):
    recipes_dir = os.environ["HOMEHQ_RECIPES_DIR"]
    os.makedirs(recipes_dir, exist_ok=True)
    with open(os.path.join(recipes_dir, filename), "w") as f:
        f.write(text)
    app.recipes.reload()


def _shopping_list():
    import db

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    try:
        return db.list_shopping_list_items(conn)
    finally:
        conn.close()


def _add_pantry_item(name, quantity, unit, storage="pantry"):
    import db

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    try:
        db.init_db(conn)
        db.add_item(conn, name=name, quantity=quantity, unit=unit, location="", storage=storage)
    finally:
        conn.close()


CHILI = """---
name: Chili
kind: meal
serves: 2
ingredients:
  - {name: Onions, quantity: 2, unit: count, fresh: true}
  - {name: Flour, quantity: 1, unit: cup, staple: true}
  - {name: Cummin, quantity: 2, unit: tsp}
  - {name: Saffron threads, quantity: 1, unit: pinch}
---
Cook it.
"""


def _setup_chili(client, app):
    _write_recipe(app, "chili.md", CHILI)
    _add_pantry_item("Onions", 3, "count", storage="pantry")
    _add_pantry_item("Flour", 2, "kg", storage="pantry")
    _add_pantry_item("Cumin", 1, "jar", storage="freezer")
    _login(client)


# --- GET: bucket panel rendering -------------------------------------------


def test_fresh_and_missing_are_pre_checked_on_the_page(client, app):
    _setup_chili(client, app)

    body = client.get("/recipes/chili").data.decode()

    onions_checkbox = re.search(r'<input type="checkbox"[^>]*name="add-0"[^>]*>', body)
    saffron_checkbox = re.search(r'<input type="checkbox"[^>]*name="add-3"[^>]*>', body)
    assert onions_checkbox and "checked" in onions_checkbox.group(0)
    assert saffron_checkbox and "checked" in saffron_checkbox.group(0)


def test_on_hand_checkbox_is_not_pre_checked(client, app):
    """Flour is staple and matches an existing pantry row -> on_hand,
    collapsed, one tap away -- but not selected by default."""
    _setup_chili(client, app)

    body = client.get("/recipes/chili").data.decode()

    flour_checkbox = re.search(r'<input type="checkbox"[^>]*name="add-1"[^>]*>', body)
    assert flour_checkbox
    assert "checked" not in flour_checkbox.group(0)
    assert "On hand" in body


def test_check_bucket_offers_two_radios_neither_preselected(client, app):
    """Cummin fuzzy-matches the freezer's "Cumin" and isn't staple/fresh ->
    check bucket. Never auto-resolved: neither radio starts checked."""
    _setup_chili(client, app)

    body = client.get("/recipes/chili").data.decode()

    have = re.search(r'<input type="radio" name="check-2" value="have"[^>]*>', body)
    add = re.search(r'<input type="radio" name="check-2" value="add"[^>]*>', body)
    assert have and "checked" not in have.group(0)
    assert add and "checked" not in add.group(0)
    assert "Cumin" in body  # the matched pantry row's own name is shown verbatim


def test_fresh_row_with_a_match_is_annotated_but_still_in_the_visible_list(client, app):
    """Onions is fresh AND matches an existing pantry row -- it must stay in
    the always-listed/pre-checked group, just with an annotation, never move
    into on_hand or check."""
    _setup_chili(client, app)

    body = client.get("/recipes/chili").data.decode()

    assert "Pantry says: Onions" in body
    onions_checkbox = re.search(r'<input type="checkbox"[^>]*name="add-0"[^>]*>', body)
    assert onions_checkbox and "checked" in onions_checkbox.group(0)


def test_shop_form_has_the_pending_guard_markup(client, app):
    """Server-verifiable half of Task 10's disable-while-pending pattern --
    the button-disable/duplicate-POST-prevention itself is client-side JS
    (base.html), so this checks the form carries the hook rather than
    trying to race two real POSTs against the test client."""
    _setup_chili(client, app)

    body = client.get("/recipes/chili").data.decode()

    shop_form = re.search(r'<form method="post" action="/recipes/chili/shop"[^>]*>', body, re.DOTALL)
    assert shop_form, "expected a shop form posting to /recipes/<slug>/shop"
    assert "data-pending-message=" in shop_form.group(0)
    assert 'aria-live="polite"' in body


def test_no_ingredients_recipe_has_no_shop_panel(client, app):
    _write_recipe(app, "bare.md", "---\nname: Bare\nkind: meal\n---\nCook.\n")
    _login(client)

    body = client.get("/recipes/bare").data.decode()

    assert 'action="/recipes/bare/shop"' not in body


# --- POST: only selected items are added -----------------------------------


def test_shop_requires_login(client):
    response = client.post("/recipes/chili/shop", data={})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_unknown_slug_404s(client, app):
    _login(client)
    assert client.post("/recipes/nope/shop", data={}).status_code == 404


def test_only_selected_items_are_added(client, app):
    _setup_chili(client, app)

    client.post(
        "/recipes/chili/shop",
        data={"serves": "", "add-0": "on", "check-2": "add"},
    )

    names = {item["name"] for item in _shopping_list()}
    assert names == {"Onions", "Cummin"}


def test_unchecked_fresh_item_is_not_added_even_though_it_starts_pre_checked(client, app):
    _setup_chili(client, app)

    client.post("/recipes/chili/shop", data={"serves": ""})  # nothing selected

    assert _shopping_list() == []


def test_unresolved_check_item_is_never_added(client, app):
    """A check-bucket item with no explicit action (or an explicit "have")
    must never be treated as "have enough" automatically -- it just stays
    off the list, same as leaving it unresolved."""
    _setup_chili(client, app)

    client.post("/recipes/chili/shop", data={"serves": "", "check-2": "have"})
    assert _shopping_list() == []

    client.post("/recipes/chili/shop", data={"serves": ""})  # field absent entirely
    assert _shopping_list() == []


def test_on_hand_item_can_still_be_added_with_one_tap(client, app):
    _setup_chili(client, app)

    client.post("/recipes/chili/shop", data={"serves": "", "add-1": "on"})

    names = {item["name"] for item in _shopping_list()}
    assert names == {"Flour"}


def test_source_recipe_identifies_the_recipe(client, app):
    _setup_chili(client, app)

    client.post("/recipes/chili/shop", data={"serves": "", "add-0": "on"})

    item = _shopping_list()[0]
    assert "Chili" in item["source_recipe"]
    assert "chili" in item["source_recipe"]


def test_missing_item_defaults_to_pantry_storage(client, app):
    _setup_chili(client, app)

    client.post("/recipes/chili/shop", data={"serves": "", "add-3": "on"})

    item = _shopping_list()[0]
    assert item["name"] == "Saffron threads"
    assert item["storage"] == "pantry"


def test_fresh_item_gets_fresh_storage(client, app):
    _setup_chili(client, app)

    client.post("/recipes/chili/shop", data={"serves": "", "add-0": "on"})

    item = _shopping_list()[0]
    assert item["storage"] == "fresh"


def test_check_item_added_uses_the_matched_rows_storage(client, app):
    """Cummin matches a freezer row -- adding it should carry that storage
    through, not default to pantry."""
    _setup_chili(client, app)

    client.post("/recipes/chili/shop", data={"serves": "", "check-2": "add"})

    item = _shopping_list()[0]
    assert item["name"] == "Cummin"
    assert item["storage"] == "freezer"


def test_route_never_touches_inventory(client, app):
    _setup_chili(client, app)

    client.post(
        "/recipes/chili/shop",
        data={"serves": "", "add-0": "on", "add-3": "on", "check-2": "add"},
    )

    import db

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    try:
        pantry = db.list_items(conn, storage="pantry")
        freezer = db.list_items(conn, storage="freezer")
    finally:
        conn.close()
    # The pre-existing pantry/freezer rows are untouched -- same quantities
    # as _setup_chili seeded, nothing adjusted or newly created.
    assert {i["name"]: i["quantity"] for i in pantry} == {"Onions": 3, "Flour": 2}
    assert {i["name"]: i["quantity"] for i in freezer} == {"Cumin": 1}


# --- POST: scaled amounts carry through as the shopping-list note ----------


def test_scaled_amount_carries_into_the_note(client, app):
    """Chili serves 2 by default; posting serves=4 should double the
    recorded quantity into the note, not the unscaled base amount."""
    _setup_chili(client, app)

    client.post("/recipes/chili/shop", data={"serves": "4", "add-3": "on"})

    item = _shopping_list()[0]
    assert item["name"] == "Saffron threads"
    assert item["amount_note"] == "2 pinch"  # 1 pinch base * (4/2) factor


def test_unscaled_amount_is_the_base_recipe_amount(client, app):
    _setup_chili(client, app)

    client.post("/recipes/chili/shop", data={"serves": "", "add-3": "on"})

    item = _shopping_list()[0]
    assert item["amount_note"] == "1 pinch"


def test_bogus_serves_value_falls_back_to_the_unscaled_amount(client, app):
    _setup_chili(client, app)

    client.post("/recipes/chili/shop", data={"serves": "nonsense", "add-3": "on"})

    item = _shopping_list()[0]
    assert item["amount_note"] == "1 pinch"
