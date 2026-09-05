import json
import os

import pytest

import ai_extract
import recipe_loader
import recipe_writer

from tests.test_capture import _FakeClient


def _response(items):
    return json.dumps({"kind": "ingredients", "items": items})


# --- parsing --------------------------------------------------------------


def test_parses_ingredients_with_flags():
    ingredients = ai_extract.parse_ingredients_response(
        _response(
            [
                {"name": "ground beef", "quantity": 1, "unit": "lb", "fresh": True},
                {"name": "cumin", "quantity": 2, "unit": "tsp", "staple": True},
                {"name": "kidney beans", "quantity": 1, "unit": "can"},
            ]
        )
    )

    assert [i["name"] for i in ingredients] == ["ground beef", "cumin", "kidney beans"]
    assert ingredients[0]["fresh"] is True
    assert ingredients[1]["staple"] is True
    assert ingredients[2]["fresh"] is False
    assert ingredients[2]["staple"] is False


def test_ingredient_without_a_quantity_is_kept():
    """Terse recipe notes often name an ingredient with no amount."""
    ingredients = ai_extract.parse_ingredients_response(_response([{"name": "garlic"}]))

    assert ingredients[0]["quantity"] is None


def test_nameless_ingredients_are_dropped_not_fatal():
    """One junk entry shouldn't lose the whole extraction."""
    ingredients = ai_extract.parse_ingredients_response(
        _response([{"name": "onion"}, {"quantity": 2}])
    )

    assert [i["name"] for i in ingredients] == ["onion"]


def test_bad_json_raises_extraction_error():
    with pytest.raises(ai_extract.ExtractionError):
        ai_extract.parse_ingredients_response("sorry, no")


def test_extract_ingredients_sends_the_recipe_text():
    client = _FakeClient(_response([{"name": "onion"}]))

    ingredients = ai_extract.extract_ingredients(
        "Chili", "Brown the beef with onion.", client=client
    )

    sent = json.dumps(client.calls[0]["messages"][0]["content"])
    assert "Brown the beef with onion." in sent
    assert "Chili" in sent
    assert ingredients[0]["name"] == "onion"


# --- writing back ---------------------------------------------------------


def _seed(tmp_path):
    return recipe_writer.write_recipe(
        str(tmp_path),
        name="Classic Beef Chili",
        frontmatter={"kind": "meal", "favorite": True, "effort": 3, "season": "Fall/Winter"},
        ingredients=[],
        body="Simmer for an hour.\n\n**Serve with:** Cheddar",
    )


def test_set_ingredients_preserves_frontmatter_and_body(tmp_path):
    _seed(tmp_path)

    recipe_writer.set_ingredients(
        str(tmp_path),
        "classic-beef-chili",
        [{"name": "ground beef", "quantity": 1, "unit": "lb", "fresh": True, "staple": False}],
    )

    recipe = recipe_loader.load_recipes(str(tmp_path))[0]
    assert recipe.name == "Classic Beef Chili"
    assert recipe.is_favorite is True
    assert recipe.effort == 3
    assert recipe.frontmatter["season"] == "Fall/Winter"
    assert "Simmer for an hour." in recipe.body
    assert "**Serve with:** Cheddar" in recipe.body
    assert recipe.ingredients[0]["name"] == "ground beef"
    assert recipe.ingredients[0]["fresh"] is True


def test_set_ingredients_replaces_rather_than_appends(tmp_path):
    _seed(tmp_path)
    recipe_writer.set_ingredients(str(tmp_path), "classic-beef-chili", [{"name": "onion"}])

    recipe_writer.set_ingredients(str(tmp_path), "classic-beef-chili", [{"name": "garlic"}])

    ingredients = recipe_loader.load_recipes(str(tmp_path))[0].ingredients
    assert [i["name"] for i in ingredients] == ["garlic"]


def test_set_ingredients_does_not_create_a_second_file(tmp_path):
    _seed(tmp_path)

    recipe_writer.set_ingredients(str(tmp_path), "classic-beef-chili", [{"name": "onion"}])

    assert len([f for f in os.listdir(tmp_path) if f.endswith(".md")]) == 1


def test_set_ingredients_on_unknown_slug_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        recipe_writer.set_ingredients(str(tmp_path), "nope", [])


def test_ingredient_lines_round_trip(tmp_path):
    ingredients = [
        {"name": "ground beef", "quantity": 1, "unit": "lb", "fresh": True, "staple": False},
        {"name": "cumin", "quantity": 2, "unit": "tsp", "fresh": False, "staple": True},
        {"name": "garlic", "quantity": None, "unit": None, "fresh": False, "staple": False},
    ]

    lines = recipe_writer.ingredients_to_lines(ingredients)

    assert lines.splitlines()[0] == "ground beef | 1 | lb | fresh"
    assert lines.splitlines()[1] == "cumin | 2 | tsp | staple"
    assert lines.splitlines()[2] == "garlic"
