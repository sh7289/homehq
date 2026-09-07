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


def test_listing_meta_has_no_orphan_separator(client, app):
    _write_recipe(
        app,
        "plain.md",
        "---\nname: Plain Thing\nkind: meal\n---\nDo it.\n",
    )
    _login(client)

    body = client.get("/recipes").data.decode()

    assert "Plain Thing" in body
    assert '<span class="row__meta">·' not in body
    assert '<span class="row__meta"> ·' not in body


def test_listing_shows_the_imported_category(client, app):
    _write_recipe(
        app,
        "chili.md",
        "---\nname: Chili\nkind: meal\ncategory: Chili / Soup\neffort: 3\n---\nSimmer.\n",
    )
    _login(client)

    body = client.get("/recipes").data.decode()

    assert "Chili / Soup" in body
    assert "effort 3/5" in body


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


def test_bold_markers_in_notes_render_as_strong(client, app):
    _write_recipe(
        app,
        "bold.md",
        "---\nname: Bold\nkind: meal\n---\n**Serve with:** Cheddar and sour cream.\n",
    )
    _login(client)

    body = client.get("/recipes/bold").data.decode()

    assert "<strong>Serve with:</strong>" in body
    assert "**Serve with:**" not in body


def test_html_in_notes_is_escaped(client, app):
    """Notes are author-controlled, but never trust them into raw HTML."""
    _write_recipe(
        app,
        "sneaky.md",
        "---\nname: Sneaky\nkind: meal\n---\nUse <script>alert(1)</script> tongs.\n",
    )
    _login(client)

    body = client.get("/recipes/sneaky").data.decode()

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


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


SERVES_TWO = """---
name: Serves Two
kind: meal
serves: 2
ingredients:
  - {name: beef, quantity: 1, unit: lb}
  - {name: garlic, fresh: true}
---
Cook it.
"""


def test_detail_scales_quantities_to_the_requested_yield(client, app):
    _write_recipe(app, "serves-two.md", SERVES_TWO)
    _login(client)

    body = client.get("/recipes/serves-two?serves=4").data.decode()

    assert "2 lb" in body
    assert "serves 4" in body.lower()


def test_scaling_does_not_change_the_stored_recipe(client, app):
    _write_recipe(app, "serves-two.md", SERVES_TWO)
    _login(client)

    client.get("/recipes/serves-two?serves=8")

    assert app.recipes.get("serves-two").ingredients[0]["quantity"] == 1


def test_unscaled_detail_shows_the_original_amounts(client, app):
    _write_recipe(app, "serves-two.md", SERVES_TWO)
    _login(client)

    body = client.get("/recipes/serves-two").data.decode()

    assert "1 lb" in body


def test_a_bogus_serves_value_is_ignored(client, app):
    _write_recipe(app, "serves-two.md", SERVES_TWO)
    _login(client)

    body = client.get("/recipes/serves-two?serves=nonsense").data.decode()

    assert body.count("1 lb") >= 1


def test_scaling_offers_choices_only_when_serves_is_known(client, app):
    _write_recipe(app, "serves-two.md", SERVES_TWO)
    _write_recipe(app, "noserves.md", "---\nname: No Serves\nkind: meal\n---\nCook.\n")
    _login(client)

    assert b"Scale to" in client.get("/recipes/serves-two").data
    assert b"Scale to" not in client.get("/recipes/noserves").data


STEPPED = """---
name: Stepped
kind: meal
serves: 2
ingredients:
  - {name: chicken thighs, quantity: 1, unit: lb}
  - {name: tomatoes, quantity: 2, unit: count}
steps:
  - {id: s1, action: sear the chicken, inputs: [chicken thighs]}
  - {id: s2, action: simmer 45 min, inputs: [s1, tomatoes]}
---
Notes here.
"""


def test_detail_shows_a_numbered_method_from_steps(client, app):
    _write_recipe(app, "stepped.md", STEPPED)
    _login(client)

    body = client.get("/recipes/stepped").data.decode()

    assert "sear the chicken" in body
    assert "<ol class=\"method\">" in body


def test_table_view_renders_spanning_cells(client, app):
    _write_recipe(app, "stepped.md", STEPPED)
    _login(client)

    body = client.get("/recipes/stepped?view=table").data.decode()

    assert 'class="cfe"' in body
    assert 'rowspan="2"' in body, "the simmer step spans both ingredient rows"


def test_table_view_keeps_the_scaled_amounts(client, app):
    _write_recipe(app, "stepped.md", STEPPED)
    _login(client)

    body = client.get("/recipes/stepped?view=table&serves=4").data.decode()

    assert "2 lb" in body


def test_a_recipe_without_steps_offers_to_draft_them(client, app):
    _write_recipe(app, "tinga.md", TINGA)
    _login(client)

    body = client.get("/recipes/tinga").data.decode()

    assert "No steps recorded" in body


def test_step_editor_prefills_and_saves(client, app):
    _write_recipe(app, "stepped.md", STEPPED)
    _login(client)

    body = client.get("/recipes/stepped/steps").data.decode()
    assert "s1 | sear the chicken | chicken thighs" in body

    response = client.post(
        "/recipes/stepped/steps",
        data={"steps": "s1 | brown it | chicken thighs\ns2 | stew | s1, tomatoes"},
    )

    assert response.status_code == 302
    assert app.recipes.get("stepped").steps[0]["action"] == "brown it"


def test_suggest_steps_does_not_save(client, app, monkeypatch):
    import ai_extract

    _write_recipe(app, "stepped.md", STEPPED)
    monkeypatch.setattr(
        ai_extract,
        "extract_steps",
        lambda name, ingredients, notes, api_key=None: [
            {"id": "s1", "action": "invented step", "inputs": ["chicken thighs"]}
        ],
    )
    _login(client)

    body = client.post("/recipes/stepped/suggest-steps").data.decode()

    assert "invented step" in body
    assert app.recipes.get("stepped").steps[0]["action"] == "sear the chicken"


def test_suggest_steps_passes_ingredients_to_the_model(client, app, monkeypatch):
    import ai_extract

    seen = {}

    def fake(name, ingredients, notes, api_key=None):
        seen["names"] = [i["name"] for i in ingredients]
        seen["notes"] = notes
        return []

    _write_recipe(app, "stepped.md", STEPPED)
    monkeypatch.setattr(ai_extract, "extract_steps", fake)
    _login(client)

    client.post("/recipes/stepped/suggest-steps")

    assert "chicken thighs" in seen["names"]
    assert "Notes here." in seen["notes"]


def test_listing_marks_untried_recipes_not_favorites(client, app):
    """17 of 20 are favorites, so badging those is noise -- the informative
    ones are the few nobody has vouched for yet."""
    _write_recipe(app, "tinga.md", TINGA)          # favorite: true
    _write_recipe(app, "scones.md", SCONES)        # no favorite key
    _login(client)

    body = client.get("/recipes").data.decode()

    assert "Untried" in body
    # Scoped to the row badges: "Favorites only" is a filter control.
    assert 'class="tag-fav"' not in body


def test_listing_offers_a_category_filter_built_from_the_data(client, app):
    _write_recipe(
        app, "chili.md",
        "---\nname: Chili\nkind: meal\ncategory: Chili / Soup\n---\nSimmer.\n",
    )
    _login(client)

    body = client.get("/recipes").data.decode()

    assert "Chili / Soup" in body
    assert 'name="category"' in body


def test_category_filter_narrows_the_list(client, app):
    _write_recipe(
        app, "chili.md",
        "---\nname: Chili\nkind: meal\ncategory: Chili / Soup\n---\nSimmer.\n",
    )
    _write_recipe(
        app, "taco.md",
        "---\nname: Taco\nkind: meal\ncategory: Mexican\n---\nFold.\n",
    )
    _login(client)

    body = client.get("/recipes?category=Mexican").data.decode()

    # Check the row links, not raw names -- "Chili / Soup" is also a filter option.
    assert "/recipes/taco" in body
    assert "/recipes/chili" not in body


def test_favorites_only_toggle_is_offered_and_works(client, app):
    """The "Favorites" quick filter is a link (task 3), not a checkbox --
    check it's offered by its target query param rather than a field name."""
    _write_recipe(app, "tinga.md", TINGA)
    _write_recipe(app, "scones.md", SCONES)
    _login(client)

    assert "favorites=on" in client.get("/recipes").data.decode()

    body = client.get("/recipes?favorites=on").data.decode()
    assert "Chicken Tinga Tacos" in body
    assert "Buttermilk Scones" not in body


# --- task 3: search, quick filters, no-results states ---------------------


def test_search_by_recipe_name(client, app):
    _write_recipe(app, "tinga.md", TINGA)
    _write_recipe(app, "scones.md", SCONES)
    _login(client)

    body = client.get("/recipes?q=tinga").data.decode()

    assert "Chicken Tinga Tacos" in body
    assert "Buttermilk Scones" not in body


def test_search_by_ingredient_name(client, app):
    _write_recipe(app, "tinga.md", TINGA)
    _write_recipe(app, "scones.md", SCONES)
    _login(client)

    body = client.get("/recipes?q=chipotle").data.decode()

    assert "Chicken Tinga Tacos" in body
    assert "Buttermilk Scones" not in body


def test_search_combines_with_favorites_filter(client, app):
    """search "chicken" + Favorites only -> only favorited recipes whose
    name or an ingredient matches "chicken" (AND semantics)."""
    _write_recipe(app, "tinga.md", TINGA)  # favorite: true, has "chicken thighs"
    _write_recipe(
        app,
        "other-chicken.md",
        "---\nname: Chicken Salad\nkind: meal\nfavorite: false\n"
        "ingredients:\n  - {name: chicken breast}\n---\nToss it.\n",
    )
    _login(client)

    body = client.get("/recipes?q=chicken&favorites=on").data.decode()

    assert "Chicken Tinga Tacos" in body
    assert "Chicken Salad" not in body


def test_search_combines_with_category_filter(client, app):
    _write_recipe(
        app, "chili.md",
        "---\nname: Chili\nkind: meal\ncategory: Chili / Soup\n"
        "ingredients:\n  - {name: chicken thighs}\n---\nSimmer.\n",
    )
    _write_recipe(
        app, "taco.md",
        "---\nname: Taco\nkind: meal\ncategory: Mexican\n"
        "ingredients:\n  - {name: chicken thighs}\n---\nFold.\n",
    )
    _login(client)

    body = client.get("/recipes?q=chicken&category=Mexican").data.decode()

    assert "/recipes/taco" in body
    assert "/recipes/chili" not in body


def test_clearing_filters_restores_the_full_list(client, app):
    _write_recipe(app, "tinga.md", TINGA)
    _write_recipe(app, "scones.md", SCONES)
    _login(client)

    filtered = client.get("/recipes?q=tinga&favorites=on").data.decode()
    assert "Buttermilk Scones" not in filtered

    cleared = client.get("/recipes").data.decode()
    assert "Chicken Tinga Tacos" in cleared
    assert "Buttermilk Scones" in cleared


def test_low_effort_quick_filter_matches_max_effort_semantics(client, app):
    _write_recipe(app, "tinga.md", TINGA)   # effort: 2
    _write_recipe(app, "scones.md", SCONES)  # effort: 4
    _login(client)

    body = client.get("/recipes?max_effort=2").data.decode()

    assert "Chicken Tinga Tacos" in body
    assert "Buttermilk Scones" not in body


def test_unrecorded_effort_is_not_excluded_by_low_effort_filter_but_is_labelled(client, app):
    """A recipe with no effort recorded isn't excluded by the low-effort
    filter (that's existing RecipeStore.filter() behavior) -- but the row
    must say so, never render it as if it were a known low-effort recipe."""
    _write_recipe(
        app, "mystery.md",
        "---\nname: Mystery Effort\nkind: meal\n---\nCook it.\n",
    )
    _login(client)

    unfiltered = client.get("/recipes").data.decode()
    filtered = client.get("/recipes?max_effort=2").data.decode()

    assert "Mystery Effort" in filtered
    assert "effort not recorded" in filtered
    # Not labelled at all when no effort filter is narrowing the list --
    # avoids cluttering the everyday view with a non-issue.
    assert "Mystery Effort" in unfiltered
    assert "effort not recorded" not in unfiltered


def test_missing_time_and_effort_are_never_defaulted(client, app):
    _write_recipe(
        app, "bare.md",
        "---\nname: Bare Recipe\nkind: meal\n---\nJust cook.\n",
    )
    _login(client)

    body = client.get("/recipes").data.decode()

    assert "effort 0/5" not in body
    assert "0 min" not in body


def test_recorded_total_time_is_shown(client, app):
    _write_recipe(
        app, "timed.md",
        "---\nname: Timed Recipe\nkind: meal\ntotal_minutes: 45\n---\nCook.\n",
    )
    _login(client)

    body = client.get("/recipes").data.decode()

    assert "45 min" in body


def test_empty_library_shows_a_distinct_message_from_no_filter_results(client, app):
    _login(client)

    body = client.get("/recipes").data.decode()

    assert "Nothing on file yet" in body
    assert "No recipes match" not in body


def test_filters_matching_nothing_show_a_distinct_message_from_empty_library(client, app):
    _write_recipe(app, "tinga.md", TINGA)
    _login(client)

    body = client.get("/recipes?q=nonexistent-ingredient-xyz").data.decode()

    assert "No recipes match" in body
    assert "Nothing on file yet" not in body


def test_matching_count_shown_versus_total(client, app):
    _write_recipe(app, "tinga.md", TINGA)
    _write_recipe(app, "scones.md", SCONES)
    _login(client)

    body = client.get("/recipes?favorites=on").data.decode()

    assert "1 of 2 recipe" in body


def test_active_filters_are_shown_with_a_clear_control(client, app):
    _write_recipe(app, "tinga.md", TINGA)
    _login(client)

    body = client.get("/recipes?q=tinga&favorites=on").data.decode()

    assert "filtered by" in body
    assert "Clear filters" in body
    # Unfiltered listing shouldn't show either.
    plain = client.get("/recipes").data.decode()
    assert "filtered by" not in plain
    assert "Clear filters" not in plain


def test_add_recipe_actions_are_at_the_top_of_the_page(client, app):
    _write_recipe(app, "tinga.md", TINGA)
    _login(client)

    body = client.get("/recipes").data.decode()

    assert body.index("Paste a recipe") < body.index("Chicken Tinga Tacos")
    assert body.index("Add by hand") < body.index("Chicken Tinga Tacos")


def test_long_recipe_name_and_meta_are_in_separate_stacked_elements(client, app):
    """Regression guard for name/metadata collision at narrow widths: name
    and meta must be in the row__info column wrapper, not loose flex
    siblings of the row itself."""
    _write_recipe(
        app, "long.md",
        "---\nname: A Genuinely Very Long Recipe Name That Could Wrap Awkwardly Next To Its Metadata\n"
        "kind: meal\ncategory: Mexican\n---\nCook.\n",
    )
    _login(client)

    body = client.get("/recipes").data.decode()

    assert '<div class="row__info">' in body


def test_paste_page_requires_login(client):
    response = client.get("/recipes/paste")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_pasting_a_recipe_prefills_the_new_recipe_form(client, app, monkeypatch):
    import ai_extract

    monkeypatch.setattr(
        ai_extract,
        "extract_recipe",
        lambda text, api_key=None: {
            "name": "Chicken Piccata",
            "kind": "meal",
            "serves": 4,
            "ingredients": [
                {"name": "chicken breasts", "quantity": 2, "unit": "count",
                 "fresh": True, "staple": False},
                {"name": "capers", "quantity": 2, "unit": "tbsp",
                 "fresh": False, "staple": False},
            ],
            "method": "Dredge, sear, deglaze.",
        },
    )
    _login(client)

    response = client.post("/recipes/paste", data={"text": "Chicken Piccata..."})

    assert response.status_code == 200
    body = response.data.decode()
    assert "Chicken Piccata" in body
    assert "chicken breasts | 2 | count | fresh" in body
    assert "Dredge, sear, deglaze." in body
    # Nothing is written until the human submits the prefilled form.
    assert app.recipes.get("chicken-piccata") is None


def test_pasting_nothing_asks_for_text(client, app):
    _login(client)

    response = client.post("/recipes/paste", data={"text": "   "})

    assert response.status_code == 200
    assert b"aste" in response.data


def test_paste_reports_an_extraction_failure(client, app, monkeypatch):
    import ai_extract

    def boom(text, api_key=None):
        raise ai_extract.ExtractionError("Model response was not valid JSON")

    monkeypatch.setattr(ai_extract, "extract_recipe", boom)
    _login(client)

    response = client.post("/recipes/paste", data={"text": "some recipe"})

    assert response.status_code == 200
    assert b"not valid JSON" in response.data


EDITABLE = """---
name: Editable
kind: meal
category: Mexican
serves: 2
effort: 3
favorite: true
ingredients:
  - {name: chicken, quantity: 1, unit: lb, fresh: true}
steps:
  - {id: s1, action: sear, inputs: [chicken]}
---
Original notes that should be editable.
"""


def test_edit_page_requires_login(client):
    response = client.get("/recipes/editable/edit")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_edit_page_prefills_the_current_values(client, app):
    _write_recipe(app, "editable.md", EDITABLE)
    _login(client)

    body = client.get("/recipes/editable/edit").data.decode()

    assert "Editable" in body
    assert "Original notes that should be editable." in body
    assert "Mexican" in body


def test_editing_saves_the_notes_and_metadata(client, app):
    _write_recipe(app, "editable.md", EDITABLE)
    _login(client)

    response = client.post(
        "/recipes/editable/edit",
        data={
            "name": "Renamed",
            "kind": "side",
            "category": "Italian",
            "serves": "4",
            "effort": "1",
            "body": "Rewritten notes.",
        },
    )

    assert response.status_code == 302
    r = app.recipes.get("editable")
    assert r.name == "Renamed"
    assert r.kind == "side"
    assert r.frontmatter["category"] == "Italian"
    assert r.frontmatter["serves"] == 4
    assert r.body == "Rewritten notes."


def test_editing_preserves_ingredients_and_steps(client, app):
    """The notes are only one part of the file; structuring work must survive."""
    _write_recipe(app, "editable.md", EDITABLE)
    _login(client)

    client.post(
        "/recipes/editable/edit",
        data={"name": "Editable", "kind": "meal", "body": "New notes."},
    )

    r = app.recipes.get("editable")
    assert [i["name"] for i in r.ingredients] == ["chicken"]
    assert r.ingredients[0]["quantity"] == 1
    assert [s["action"] for s in r.steps] == ["sear"]


def test_notes_can_be_cleared_entirely(client, app):
    """Once steps are drafted the original prose is redundant."""
    _write_recipe(app, "editable.md", EDITABLE)
    _login(client)

    client.post(
        "/recipes/editable/edit",
        data={"name": "Editable", "kind": "meal", "body": "   "},
    )

    r = app.recipes.get("editable")
    assert r.body == ""
    assert r.steps, "clearing notes must not take the steps with them"


def test_unfavouriting_works(client, app):
    """A checkbox that is unchecked sends nothing, so absence must mean false."""
    _write_recipe(app, "editable.md", EDITABLE)
    _login(client)

    client.post(
        "/recipes/editable/edit",
        data={"name": "Editable", "kind": "meal", "body": "x"},
    )

    assert app.recipes.get("editable").is_favorite is False


def test_editing_an_unknown_recipe_404s(client, app):
    _login(client)

    assert client.get("/recipes/nope/edit").status_code == 404


def test_detail_page_links_to_the_editor(client, app):
    _write_recipe(app, "editable.md", EDITABLE)
    _login(client)

    body = client.get("/recipes/editable").data.decode()

    assert "/recipes/editable/edit" in body
