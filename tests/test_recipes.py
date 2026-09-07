import os

import pytest

import recipe_loader
import recipe_writer
from recipe_store import RecipeStore

TINGA = """---
name: Chicken Tinga Tacos
kind: meal
cuisine: mexican
favorite: true
effort: 2
serves: 2
ingredients:
  - {name: chicken thighs, quantity: 1.5, unit: lb, fresh: true}
  - {name: chipotle chili powder, quantity: 2, unit: tsp, staple: true}
  - {name: corn tortillas, quantity: 12, unit: count}
---
Char the chilies, then simmer.
"""

SCONES = """---
name: Buttermilk Scones
kind: baked
favorite: false
effort: 4
ingredients:
  - {name: flour, quantity: 500, unit: g, staple: true}
---
Rub the butter in cold.
"""


def _write(recipes_dir, filename, text):
    os.makedirs(recipes_dir, exist_ok=True)
    with open(os.path.join(recipes_dir, filename), "w") as f:
        f.write(text)


# --- loader ---------------------------------------------------------------


def test_parses_ingredients_as_list_of_dicts(tmp_path):
    _write(tmp_path, "tinga.md", TINGA)

    recipes = recipe_loader.load_recipes(str(tmp_path))

    assert len(recipes) == 1
    assert [i["name"] for i in recipes[0].ingredients] == [
        "chicken thighs",
        "chipotle chili powder",
        "corn tortillas",
    ]


def test_keeps_quantities_numeric(tmp_path):
    """content_loader stringifies every scalar; recipes must not."""
    _write(tmp_path, "tinga.md", TINGA)

    recipe = recipe_loader.load_recipes(str(tmp_path))[0]

    assert recipe.ingredients[0]["quantity"] == 1.5
    assert recipe.frontmatter["effort"] == 2
    assert recipe.frontmatter["serves"] == 2


def test_defaults_staple_and_fresh_to_false(tmp_path):
    _write(tmp_path, "tinga.md", TINGA)

    ingredients = recipe_loader.load_recipes(str(tmp_path))[0].ingredients

    tortillas = ingredients[2]
    assert tortillas["staple"] is False
    assert tortillas["fresh"] is False
    assert ingredients[0]["fresh"] is True
    assert ingredients[1]["staple"] is True


def test_ingredient_given_as_a_bare_string_still_loads(tmp_path):
    _write(
        tmp_path,
        "simple.md",
        "---\nname: Toast\nkind: breakfast\ningredients:\n  - bread\n---\nToast it.\n",
    )

    ingredients = recipe_loader.load_recipes(str(tmp_path))[0].ingredients

    assert ingredients[0]["name"] == "bread"
    assert ingredients[0]["quantity"] is None


def test_skips_file_missing_a_name(tmp_path):
    _write(tmp_path, "bad.md", "---\nkind: meal\n---\nNo name.\n")
    _write(tmp_path, "tinga.md", TINGA)

    recipes = recipe_loader.load_recipes(str(tmp_path))

    assert [r.slug for r in recipes] == ["tinga"]


def test_skips_malformed_yaml_without_crashing(tmp_path):
    _write(tmp_path, "broken.md", "---\nname: [unclosed\n---\nbody\n")
    _write(tmp_path, "tinga.md", TINGA)

    assert [r.slug for r in recipe_loader.load_recipes(str(tmp_path))] == ["tinga"]


def test_missing_directory_returns_empty(tmp_path):
    assert recipe_loader.load_recipes(str(tmp_path / "nope")) == []


def test_body_is_preserved(tmp_path):
    _write(tmp_path, "tinga.md", TINGA)

    assert "Char the chilies" in recipe_loader.load_recipes(str(tmp_path))[0].body


# --- store ----------------------------------------------------------------


def _store(tmp_path):
    _write(tmp_path, "tinga.md", TINGA)
    _write(tmp_path, "scones.md", SCONES)
    store = RecipeStore(str(tmp_path))
    store.reload()
    return store


def test_store_get_returns_recipe_by_slug(tmp_path):
    assert _store(tmp_path).get("tinga").name == "Chicken Tinga Tacos"


def test_store_get_returns_none_for_unknown_slug(tmp_path):
    assert _store(tmp_path).get("nonexistent") is None


def test_store_favorites_excludes_non_favorites(tmp_path):
    assert [r.slug for r in _store(tmp_path).favorites()] == ["tinga"]


def test_store_filters_by_kind(tmp_path):
    assert [r.slug for r in _store(tmp_path).filter(kind="baked")] == ["scones"]


def test_store_filters_by_max_effort(tmp_path):
    assert [r.slug for r in _store(tmp_path).filter(max_effort=3)] == ["tinga"]


def test_store_filter_q_matches_recipe_name(tmp_path):
    assert [r.slug for r in _store(tmp_path).filter(q="tinga")] == ["tinga"]


def test_store_filter_q_matches_ingredient_name(tmp_path):
    assert [r.slug for r in _store(tmp_path).filter(q="chipotle")] == ["tinga"]


def test_store_filter_q_is_case_insensitive(tmp_path):
    assert [r.slug for r in _store(tmp_path).filter(q="CHICKEN THIGHS")] == ["tinga"]


def test_store_filter_q_is_a_substring_match(tmp_path):
    assert [r.slug for r in _store(tmp_path).filter(q="scon")] == ["scones"]


def test_store_filter_q_combines_with_other_filters(tmp_path):
    """AND semantics: a search term narrows whatever the other filters
    already selected, it doesn't replace them."""
    store = _store(tmp_path)

    # "flour" only matches Scones, which isn't a favorite -- combined with
    # favorites_only the two conditions cancel each other out.
    assert store.filter(q="flour", favorites_only=True) == []
    assert [r.slug for r in store.filter(q="flour", favorites_only=False)] == ["scones"]


def test_store_filter_q_of_blank_string_matches_everything(tmp_path):
    assert [r.slug for r in _store(tmp_path).filter(q="   ")] == ["scones", "tinga"]


def test_store_filter_q_no_match_returns_empty(tmp_path):
    assert _store(tmp_path).filter(q="nonexistent-ingredient") == []


def test_store_kinds_lists_kinds_present(tmp_path):
    assert _store(tmp_path).kinds() == ["baked", "meal"]


# --- writer ---------------------------------------------------------------


def test_write_recipe_creates_slugged_file(tmp_path):
    path = recipe_writer.write_recipe(
        str(tmp_path),
        name="Chicken Tinga Tacos",
        frontmatter={"kind": "meal", "favorite": True},
        ingredients=[{"name": "chicken thighs", "quantity": 1.5, "unit": "lb"}],
        body="Char the chilies.",
    )

    assert os.path.basename(path) == "chicken-tinga-tacos.md"
    recipe = recipe_loader.load_recipes(str(tmp_path))[0]
    assert recipe.name == "Chicken Tinga Tacos"
    assert recipe.ingredients[0]["quantity"] == 1.5
    assert recipe.body == "Char the chilies."


def test_write_recipe_does_not_overwrite_an_existing_slug(tmp_path):
    first = recipe_writer.write_recipe(
        str(tmp_path), name="Toast", frontmatter={}, ingredients=[], body="a"
    )
    second = recipe_writer.write_recipe(
        str(tmp_path), name="Toast", frontmatter={}, ingredients=[], body="b"
    )

    assert first != second
    assert len(recipe_loader.load_recipes(str(tmp_path))) == 2


WITH_STEPS = """---
name: Tinga
kind: meal
ingredients:
  - {name: chicken thighs, quantity: 1.5, unit: lb}
steps:
  - {id: s1, action: sear, inputs: [chicken thighs]}
  - {id: s2, action: simmer, inputs: [s1]}
---
Body.
"""


def test_steps_are_loaded(tmp_path):
    _write(tmp_path, "tinga.md", WITH_STEPS)

    recipe = recipe_loader.load_recipes(str(tmp_path))[0]

    assert [s["id"] for s in recipe.steps] == ["s1", "s2"]
    assert recipe.steps[0]["action"] == "sear"
    assert recipe.steps[0]["inputs"] == ["chicken thighs"]


def test_steps_default_to_empty(tmp_path):
    _write(tmp_path, "tinga.md", TINGA)

    assert recipe_loader.load_recipes(str(tmp_path))[0].steps == []


def test_a_step_without_an_id_is_dropped(tmp_path):
    _write(
        tmp_path,
        "bad.md",
        "---\nname: Bad\nkind: meal\nsteps:\n  - {action: stir, inputs: []}\n---\nb\n",
    )

    assert recipe_loader.load_recipes(str(tmp_path))[0].steps == []


def test_steps_are_not_left_in_the_frontmatter_dict(tmp_path):
    _write(tmp_path, "tinga.md", WITH_STEPS)

    recipe = recipe_loader.load_recipes(str(tmp_path))[0]

    assert "steps" not in recipe.frontmatter


def test_set_steps_preserves_everything_else(tmp_path):
    recipe_writer.write_recipe(
        str(tmp_path),
        name="Tinga",
        frontmatter={"kind": "meal", "favorite": True, "serves": 2},
        ingredients=[{"name": "chicken thighs", "quantity": 1.5, "unit": "lb"}],
        body="Original notes.",
    )

    recipe_writer.set_steps(
        str(tmp_path), "tinga", [{"id": "s1", "action": "sear", "inputs": ["chicken thighs"]}]
    )

    recipe = recipe_loader.load_recipes(str(tmp_path))[0]
    assert recipe.steps[0]["action"] == "sear"
    assert recipe.ingredients[0]["quantity"] == 1.5
    assert recipe.frontmatter["serves"] == 2
    assert recipe.is_favorite is True
    assert recipe.body == "Original notes."
