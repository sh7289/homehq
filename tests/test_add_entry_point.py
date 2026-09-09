"""Task 12: a shared "Add" entry point organized by intent.

Covers:
  - /add (top-level intent chooser: Groceries / Recipe / Household item)
  - /add/groceries (Groceries' method choice: Type it in / Upload a photo)
  - the shared Add nav link (desktop rail + mobile "More" panel)
  - contextual entry points that skip straight to a method choice
    (Pantry/Freezer -> /add/groceries; Recipes' existing top-of-page
    Paste/Manual buttons already skip it for Recipe)
  - relabeled buttons that previously all said "Read it"
"""


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


# --- /add: top-level intent chooser ---------------------------------------


def test_add_chooser_requires_login(client):
    response = client.get("/add")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_add_chooser_offers_all_three_intents(client):
    _login(client)

    body = client.get("/add").data.decode()

    assert "Groceries" in body
    assert "Recipe" in body
    assert "Household item" in body


def test_add_chooser_groceries_intent_leads_to_the_groceries_method_choice(client):
    _login(client)

    body = client.get("/add").data.decode()

    assert 'href="/add/groceries"' in body


def test_add_chooser_recipe_intent_jumps_straight_to_recipes_method_choices(client):
    """Recipe already has its Paste/Manual method choices at the top of the
    Recipes page (Task 3) -- the chooser should hand off there directly
    rather than duplicating a second recipe-method screen."""
    _login(client)

    body = client.get("/add").data.decode()

    assert 'href="/recipes"' in body


def test_add_chooser_household_item_intent_reuses_import_no_new_backend(client):
    """There is no dedicated manual entry route for a catalog item -- the
    only way to add one is via Import a Photo, which auto-detects a single
    item worth cataloguing. The chooser must route there, not invent a new
    form."""
    _login(client)

    body = client.get("/add").data.decode()

    assert 'href="/import/upload"' in body


def test_add_chooser_explains_what_each_intent_does_next(client):
    """Every entry route should make clear what happens next -- staging for
    review vs. opening a form."""
    _login(client)

    body = client.get("/add").data.decode().lower()

    assert "review" in body


# --- /add/groceries: Groceries' method choice ------------------------------


def test_add_groceries_requires_login(client):
    response = client.get("/add/groceries")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_add_groceries_offers_type_and_photo_methods(client):
    _login(client)

    body = client.get("/add/groceries").data.decode()

    assert 'href="/capture"' in body
    assert 'href="/import/upload"' in body


def test_add_groceries_makes_the_review_step_clear(client):
    _login(client)

    body = client.get("/add/groceries").data.decode().lower()

    assert "review" in body


# --- Contextual entry points skip the intent step --------------------------


def test_pantry_offers_a_direct_route_to_groceries_method_choice(client):
    """From Pantry, a user can start adding groceries (typing or uploading a
    photo) without needing to already know or understand the term
    "Capture" -- this link should skip straight past the top-level /add
    chooser to the Groceries method choice."""
    _login(client)

    body = client.get("/pantry").data.decode()

    assert 'href="/add/groceries"' in body


def test_freezer_offers_a_direct_route_to_groceries_method_choice(client):
    _login(client)

    body = client.get("/freezer").data.decode()

    assert 'href="/add/groceries"' in body


def test_recipes_page_still_skips_straight_to_paste_and_manual(client):
    """Recipe's contextual entry point already exists (Task 3) -- confirm
    it survives this task's changes untouched: no intermediate top-level
    intent screen when the context already establishes "Recipe"."""
    _login(client)

    body = client.get("/recipes").data.decode()

    assert 'href="/recipes/paste"' in body
    assert 'href="/recipes/new"' in body


# --- Shared Add nav entry point ---------------------------------------------


def test_add_is_reachable_from_nav_on_every_page(client):
    _login(client)

    for path in ("/", "/pantry", "/recipes", "/capture", "/shopping-list"):
        body = client.get(path).data.decode()
        assert 'href="/add"' in body, f"Add entry point missing from nav on {path}"


def test_add_page_marks_its_own_nav_link_current(client):
    _login(client)

    body = client.get("/add").data.decode()

    assert body.count('aria-current="page"') == 2


def test_add_groceries_page_marks_the_add_nav_link_current(client):
    """/add/groceries is reached from the Add entry point, so it should
    keep the same nav link highlighted rather than looking like a stray,
    unrelated page."""
    _login(client)

    body = client.get("/add/groceries").data.decode()

    assert body.count('aria-current="page"') == 2


# --- Relabeled buttons: "Read it" described what happens next instead -----


def test_capture_button_describes_what_happens_next(client):
    _login(client)

    body = client.get("/capture").data.decode()

    assert "Read it" not in body
    assert "Preview groceries" in body


def test_recipe_paste_button_describes_what_happens_next(client):
    _login(client)

    body = client.get("/recipes/paste").data.decode()

    assert "Read it" not in body
    assert "Prepare recipe" in body
