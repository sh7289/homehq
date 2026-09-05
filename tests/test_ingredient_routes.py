import os

import ai_extract
import recipe_loader

CHILI = """---
name: Classic Beef Chili
kind: meal
favorite: true
effort: 3
season: Fall/Winter
---
Lean ground beef 90/10; kidney beans, onion, garlic.
"""


def _login(client):
    client.post("/login", data={"username": "alice", "password": "password1"})


def _write_recipe(app, filename, text):
    recipes_dir = os.environ["HOMEHQ_RECIPES_DIR"]
    os.makedirs(recipes_dir, exist_ok=True)
    with open(os.path.join(recipes_dir, filename), "w") as f:
        f.write(text)
    app.recipes.reload()


def _loaded(slug):
    recipes = recipe_loader.load_recipes(os.environ["HOMEHQ_RECIPES_DIR"])
    return {r.slug: r for r in recipes}[slug]


def test_ingredient_routes_require_login(client):
    for path in ("/recipes/classic-beef-chili/ingredients",):
        response = client.get(path)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


def test_ingredients_page_prefills_the_existing_list(client, app):
    _write_recipe(
        app,
        "chili.md",
        "---\nname: Chili\nkind: meal\ningredients:\n"
        "  - {name: onion, quantity: 1, unit: count, fresh: true}\n---\nSimmer.\n",
    )
    _login(client)

    body = client.get("/recipes/chili/ingredients").data.decode()

    assert "onion | 1 | count | fresh" in body


def test_suggest_proposes_ingredients_without_writing_the_file(client, app, monkeypatch):
    _write_recipe(app, "classic-beef-chili.md", CHILI)
    monkeypatch.setattr(
        ai_extract,
        "extract_ingredients",
        lambda name, text, api_key=None: [
            {"name": "ground beef", "quantity": 1, "unit": "lb", "fresh": True, "staple": False},
            {"name": "kidney beans", "quantity": 1, "unit": "can", "fresh": False, "staple": False},
        ],
    )
    _login(client)

    response = client.post("/recipes/classic-beef-chili/suggest-ingredients")

    assert response.status_code == 200
    body = response.data.decode()
    assert "ground beef | 1 | lb | fresh" in body
    assert "kidney beans | 1 | can" in body
    # Nothing committed until the human saves.
    assert _loaded("classic-beef-chili").ingredients == []


def test_suggest_passes_the_recipe_name_and_notes_to_the_model(client, app, monkeypatch):
    _write_recipe(app, "classic-beef-chili.md", CHILI)
    seen = {}

    def fake(name, text, api_key=None):
        seen["name"] = name
        seen["text"] = text
        return []

    monkeypatch.setattr(ai_extract, "extract_ingredients", fake)
    _login(client)

    client.post("/recipes/classic-beef-chili/suggest-ingredients")

    assert seen["name"] == "Classic Beef Chili"
    assert "kidney beans" in seen["text"]


def test_saving_ingredients_writes_them_and_keeps_the_metadata(client, app):
    _write_recipe(app, "classic-beef-chili.md", CHILI)
    _login(client)

    response = client.post(
        "/recipes/classic-beef-chili/ingredients",
        data={"ingredients": "ground beef | 1 | lb | fresh\ncumin | 2 | tsp | staple"},
    )

    assert response.status_code == 302
    recipe = _loaded("classic-beef-chili")
    assert [i["name"] for i in recipe.ingredients] == ["ground beef", "cumin"]
    assert recipe.ingredients[0]["fresh"] is True
    assert recipe.ingredients[1]["staple"] is True
    assert recipe.is_favorite is True
    assert recipe.frontmatter["season"] == "Fall/Winter"
    assert "kidney beans" in recipe.body


def test_saving_refreshes_the_in_memory_store(client, app):
    _write_recipe(app, "classic-beef-chili.md", CHILI)
    _login(client)

    client.post(
        "/recipes/classic-beef-chili/ingredients", data={"ingredients": "onion"}
    )

    assert app.recipes.get("classic-beef-chili").ingredients[0]["name"] == "onion"


def test_suggest_reports_an_extraction_failure(client, app, monkeypatch):
    _write_recipe(app, "classic-beef-chili.md", CHILI)

    def boom(name, text, api_key=None):
        raise ai_extract.ExtractionError("Model response was not valid JSON")

    monkeypatch.setattr(ai_extract, "extract_ingredients", boom)
    _login(client)

    response = client.post("/recipes/classic-beef-chili/suggest-ingredients")

    assert response.status_code == 200
    assert b"not valid JSON" in response.data


def test_unknown_recipe_404s(client, app):
    _login(client)

    assert client.get("/recipes/nope/ingredients").status_code == 404
    assert client.post("/recipes/nope/suggest-ingredients").status_code == 404
