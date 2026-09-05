import os
import sys

sys.path.insert(0, "scripts")

import import_recipe_bank as importer
import recipe_loader

ROW = {
    "Recipe": "Chicken Tacos",
    "Category": "Mexican",
    "Meal Type": "Formula",
    "Protein": "Chicken breast",
    "Effort": "Easy",
    "Season": "All",
    "Steve Score": "High",
    "Heather Notes": "Fresh tortillas matter",
    "Leftovers": "Medium",
    "Freezer Friendly": "No",
    "Uses Garden Herbs": "Cilantro",
    "Serve With / Sides": "Avocado, sour cream",
    "Source / Link": "Household",
    "Notes": "Season and sear the chicken.",
}


def _row(**overrides):
    row = dict(ROW)
    row.update(overrides)
    return row


def test_effort_labels_map_to_the_numeric_scale():
    assert importer.effort_score("Easy") == 1
    assert importer.effort_score("Easy/Medium") == 2
    assert importer.effort_score("Medium") == 3
    assert importer.effort_score("Project") == 5


def test_unknown_effort_label_is_left_unset():
    """Better no effort than an invented one -- the store treats None as
    'not excluded by an effort filter'."""
    assert importer.effort_score("Whenever") is None
    assert importer.effort_score("") is None


def test_high_scores_become_favorites():
    assert importer.is_favorite("High") is True
    assert importer.is_favorite("Likely High") is True
    assert importer.is_favorite("Medium") is False


def test_sides_are_classified_as_kind_side():
    assert importer.kind_for("Project Side") == "side"
    assert importer.kind_for("Recipe / Freezer-Friendly") == "meal"


def test_import_writes_a_recipe_file_with_the_rich_metadata(tmp_path):
    written, skipped = importer.import_rows([ROW], str(tmp_path))

    assert (written, skipped) == (1, 0)
    recipe = recipe_loader.load_recipes(str(tmp_path))[0]
    assert recipe.slug == "chicken-tacos"
    assert recipe.name == "Chicken Tacos"
    assert recipe.is_favorite is True
    assert recipe.effort == 1
    assert recipe.frontmatter["category"] == "Mexican"
    assert recipe.frontmatter["protein"] == "Chicken breast"
    assert recipe.frontmatter["season"] == "All"
    assert recipe.frontmatter["leftovers"] == "Medium"
    assert recipe.frontmatter["freezer_friendly"] == "No"
    assert recipe.frontmatter["garden_herbs"] == "Cilantro"
    assert recipe.frontmatter["source"] == "Household"


def test_notes_and_sides_both_reach_the_body(tmp_path):
    importer.import_rows([ROW], str(tmp_path))

    body = recipe_loader.load_recipes(str(tmp_path))[0].body

    assert "Season and sear the chicken." in body
    assert "Avocado, sour cream" in body
    assert "Fresh tortillas matter" in body


def test_import_leaves_ingredients_empty(tmp_path):
    """The CSV has no ingredients column -- they're prose inside Notes.
    Inventing structured ingredients here would be fabrication."""
    importer.import_rows([ROW], str(tmp_path))

    assert recipe_loader.load_recipes(str(tmp_path))[0].ingredients == []


def test_rerunning_the_import_skips_existing_recipes(tmp_path):
    importer.import_rows([ROW], str(tmp_path))

    written, skipped = importer.import_rows([ROW], str(tmp_path))

    assert (written, skipped) == (0, 1)
    assert len(recipe_loader.load_recipes(str(tmp_path))) == 1


def test_rows_without_a_name_are_skipped(tmp_path):
    written, skipped = importer.import_rows([_row(Recipe="  ")], str(tmp_path))

    assert written == 0
    assert recipe_loader.load_recipes(str(tmp_path)) == []


def test_blank_columns_are_omitted_rather_than_written_empty(tmp_path):
    importer.import_rows([_row(**{"Uses Garden Herbs": "", "Season": ""})], str(tmp_path))

    frontmatter = recipe_loader.load_recipes(str(tmp_path))[0].frontmatter
    assert "garden_herbs" not in frontmatter
    assert "season" not in frontmatter


def test_none_garden_herbs_is_dropped(tmp_path):
    """'None' in the sheet means no herbs, not a herb called None."""
    importer.import_rows([_row(**{"Uses Garden Herbs": "None"})], str(tmp_path))

    assert "garden_herbs" not in recipe_loader.load_recipes(str(tmp_path))[0].frontmatter
