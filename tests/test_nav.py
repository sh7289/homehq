import os


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _add_category(app, category="kitchen"):
    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    os.makedirs(os.path.join(content_dir, category), exist_ok=True)
    with open(os.path.join(content_dir, category, "item.md"), "w") as f:
        f.write(f"---\nname: Widget\ncategory: {category}\n---\n")
    app.catalog.reload()


def test_shell_has_skip_link_and_landmarks(client):
    _login(client)

    body = client.get("/").data.decode()

    assert '<a class="skip-link" href="#main-content">Skip to content</a>' in body
    assert '<main id="main-content"' in body
    assert 'aria-label="Main"' in body


def test_active_nav_item_gets_aria_current(client):
    """Both the desktop rail and the mobile bar mark the active page --
    CSS hides whichever one doesn't apply to the viewport, but the markup
    for both is always present."""
    _login(client)

    body = client.get("/recipes").data.decode()

    assert body.count('aria-current="page"') == 2


def test_inactive_pages_have_no_aria_current_for_other_sections(client):
    _login(client)

    body = client.get("/report").data.decode()

    # Report's own nav entries (2: rail + bar) are current; nothing else is.
    assert body.count('aria-current="page"') == 2


def test_every_original_destination_is_still_reachable_from_nav(client, app):
    _add_category(app, "kitchen")
    _login(client)

    body = client.get("/pantry").data.decode()

    for path in (
        "/",
        "/pantry",
        "/freezer",
        "/recipes",
        "/shopping-list",
        "/capture",
        "/import",
        "/report",
        "/catalog/kitchen",
    ):
        assert f'href="{path}"' in body, f"{path} missing from nav"


def test_catalog_categories_are_nested_under_a_catalog_disclosure(client, app):
    _add_category(app, "kitchen")
    _login(client)

    body = client.get("/pantry").data.decode()

    assert "Catalog" in body
    assert 'href="/catalog/kitchen"' in body


def test_catalog_page_marks_its_own_category_current(client, app):
    _add_category(app, "kitchen")
    _login(client)

    body = client.get("/catalog/kitchen").data.decode()

    assert body.count('aria-current="page"') == 2
    assert 'href="/catalog/kitchen"' in body


def test_logout_lives_once_in_the_shared_shell(client):
    """Logout used to be repeated on every page's header; it should now be
    a single shared account control."""
    _login(client)

    for path in ("/", "/pantry", "/recipes", "/capture", "/shopping-list"):
        body = client.get(path).data.decode()
        assert body.count('action="/logout"') == 1


def test_mobile_inventory_group_exposes_pantry_and_freezer(client):
    _login(client)

    body = client.get("/").data.decode()

    assert "Inventory" in body
    assert 'href="/pantry"' in body
    assert 'href="/freezer"' in body
