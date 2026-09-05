import os


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _write_recipe(app, filename, text):
    recipes_dir = os.environ["HOMEHQ_RECIPES_DIR"]
    os.makedirs(recipes_dir, exist_ok=True)
    with open(os.path.join(recipes_dir, filename), "w") as f:
        f.write(text)
    app.recipes.reload()


TINGA = """---
name: Chicken Tinga Tacos
kind: meal
cuisine: mexican
favorite: true
effort: 2
ingredients:
  - {name: chicken thighs, quantity: 1.5, unit: lb, fresh: true}
  - {name: chipotle chili powder, quantity: 2, unit: tsp, staple: true}
---
Char the chilies, then simmer.
"""

SCONES = """---
name: Buttermilk Scones
kind: baked
effort: 4
ingredients:
  - {name: flour, quantity: 500, unit: g, staple: true}
---
Rub the butter in cold.
"""


def test_recipe_pages_require_login(client):
    for path in ("/recipes", "/recipes/tinga", "/recipes/new"):
        response = client.get(path)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


def test_recipes_page_groups_by_kind(client, app):
    _write_recipe(app, "tinga.md", TINGA)
    _write_recipe(app, "scones.md", SCONES)
    _login(client)

    body = client.get("/recipes").data.decode()

    assert "Chicken Tinga Tacos" in body
    assert "Buttermilk Scones" in body
    # kinds render alphabetically: baked before meal
    assert body.index("Buttermilk Scones") < body.index("Chicken Tinga Tacos")


def test_recipes_page_filters_by_kind(client, app):
    _write_recipe(app, "tinga.md", TINGA)
    _write_recipe(app, "scones.md", SCONES)
    _login(client)

    body = client.get("/recipes?kind=baked").data.decode()

    assert "Buttermilk Scones" in body
    assert "Chicken Tinga Tacos" not in body


def test_recipe_detail_renders_ingredients_and_body(client, app):
    _write_recipe(app, "tinga.md", TINGA)
    _login(client)

    body = client.get("/recipes/tinga").data.decode()

    assert "chicken thighs" in body
    assert "1.5" in body
    assert "Char the chilies" in body


def test_recipe_detail_marks_fresh_and_staple_ingredients(client, app):
    """Fresh items always go on the list; staples are assumed on hand."""
    _write_recipe(app, "tinga.md", TINGA)
    _login(client)

    body = client.get("/recipes/tinga").data.decode()

    assert "Fresh" in body
    assert "Staple" in body


def test_method_hard_wraps_flow_into_one_paragraph(client, app):
    """Pasted recipes arrive wrapped at 80 chars; that must not show."""
    _write_recipe(
        app,
        "wrapped.md",
        "---\nname: Wrapped\nkind: meal\n---\n"
        "Simmer the thighs until they shred,\nabout 45 minutes.\n\nThen char the tortillas.\n",
    )
    _login(client)

    body = client.get("/recipes/wrapped").data.decode()

    assert "they shred, about 45 minutes." in body
    assert body.count("<p class=\"recipe-body__para\">") == 2


def test_recipe_detail_404s_for_unknown_slug(client):
    _login(client)

    assert client.get("/recipes/nope").status_code == 404


def test_new_recipe_form_writes_a_file_and_it_appears_in_the_list(client, app):
    _login(client)

    response = client.post(
        "/recipes/new",
        data={
            "name": "Simple Fish and Two Veg",
            "kind": "meal",
            "effort": "1",
            "favorite": "on",
            "ingredients": "cod fillet | 2 | count | fresh\nolive oil | 1 | tbsp | staple",
            "body": "Bake at 200C.",
        },
    )

    assert response.status_code == 302
    listing = client.get("/recipes").data.decode()
    assert "Simple Fish and Two Veg" in listing

    recipe = app.recipes.get("simple-fish-and-two-veg")
    assert recipe is not None
    assert recipe.ingredients[0]["name"] == "cod fillet"
    assert recipe.ingredients[0]["fresh"] is True
    assert recipe.ingredients[1]["staple"] is True
    assert recipe.is_favorite is True


def test_new_recipe_requires_a_name(client, app):
    _login(client)

    response = client.post("/recipes/new", data={"name": "  ", "kind": "meal"})

    assert response.status_code == 200
    assert b"name" in response.data.lower()


def test_recipes_are_not_scanned_into_the_catalog(client, app):
    """Recipes must not leak into the nav, the insurance report, or its CSV."""
    _write_recipe(app, "tinga.md", TINGA)
    _login(client)

    assert "recipes" not in app.catalog.categories()
    assert b"Chicken Tinga Tacos" not in client.get("/report").data
    assert b"Chicken Tinga Tacos" not in client.get("/report.csv").data


def test_reload_refreshes_recipes_too(client, app):
    _login(client)
    recipes_dir = os.environ["HOMEHQ_RECIPES_DIR"]
    os.makedirs(recipes_dir, exist_ok=True)
    with open(os.path.join(recipes_dir, "tinga.md"), "w") as f:
        f.write(TINGA)

    client.post("/reload")

    assert app.recipes.get("tinga") is not None
