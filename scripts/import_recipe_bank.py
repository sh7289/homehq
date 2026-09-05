"""Bootstrap the recipe database from the "Recipe Bank" spreadsheet export.

Deterministic and idempotent: no AI, no guessing. Re-running skips recipes
whose slug already exists, so it is safe to run again after adding rows to
the sheet.

    python scripts/import_recipe_bank.py ~/Downloads/"Meal Planning HQ - Recipe Bank.csv"

Note on ingredients: the sheet has no ingredients column -- they live as prose
inside Notes. This importer deliberately leaves `ingredients` empty rather
than inventing a structured list, so the shopping-list netting features stay
honest about what they know. Fill ingredients in per recipe afterwards.
"""

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import recipe_loader  # noqa: E402
import recipe_writer  # noqa: E402
from catalog_writer import slugify  # noqa: E402

_EFFORT_SCALE = {
    "easy": 1,
    "easy/medium": 2,
    "medium": 3,
    "medium/hard": 4,
    "hard": 4,
    "project": 5,
}

# Values that mean "nothing here" rather than a real value.
_EMPTY_VALUES = {"", "none", "n/a", "-"}

# Spreadsheet column -> frontmatter key. Everything is kept verbatim: these
# fields are what makes a meal planner useful ("something freezer-friendly for
# a cold night"), so flattening them away would be the wrong trade.
_FIELD_MAP = (
    ("Category", "category"),
    ("Meal Type", "meal_type"),
    ("Protein", "protein"),
    ("Season", "season"),
    ("Steve Score", "score"),
    ("Leftovers", "leftovers"),
    ("Freezer Friendly", "freezer_friendly"),
    ("Uses Garden Herbs", "garden_herbs"),
    ("Source / Link", "source"),
)


def _clean(value):
    value = (value or "").strip()
    return "" if value.lower() in _EMPTY_VALUES else value


def effort_score(label):
    """Map the sheet's effort words onto the 1-5 scale, or None if unknown."""
    return _EFFORT_SCALE.get((label or "").strip().lower())


def is_favorite(score):
    """'High' and 'Likely High' are vouched for; 'Medium' is not."""
    return "high" in (score or "").strip().lower()


def kind_for(meal_type):
    return "side" if "side" in (meal_type or "").lower() else "meal"


def _body_for(row):
    """Notes first, then sides, then the household notes -- each labelled."""
    parts = []
    if _clean(row.get("Notes")):
        parts.append(_clean(row["Notes"]))
    if _clean(row.get("Serve With / Sides")):
        parts.append("**Serve with:** " + _clean(row["Serve With / Sides"]))
    if _clean(row.get("Heather Notes")):
        parts.append("**Notes:** " + _clean(row["Heather Notes"]))
    return "\n\n".join(parts)


def import_rows(rows, recipes_dir):
    """Write one markdown file per row. Returns (written, skipped)."""
    os.makedirs(recipes_dir, exist_ok=True)
    existing = {recipe.slug for recipe in recipe_loader.load_recipes(recipes_dir)}

    written = skipped = 0
    for row in rows:
        name = _clean(row.get("Recipe"))
        if not name:
            continue
        if slugify(name) in existing:
            skipped += 1
            continue

        frontmatter = {
            "kind": kind_for(row.get("Meal Type")),
            "favorite": is_favorite(row.get("Steve Score")),
            "effort": effort_score(row.get("Effort")),
        }
        for column, key in _FIELD_MAP:
            value = _clean(row.get(column))
            if value:
                frontmatter[key] = value

        recipe_writer.write_recipe(
            recipes_dir,
            name=name,
            frontmatter=frontmatter,
            ingredients=[],
            body=_body_for(row),
        )
        existing.add(slugify(name))
        written += 1

    return written, skipped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument(
        "--recipes-dir", default=os.environ.get("HOMEHQ_RECIPES_DIR", "recipes")
    )
    args = parser.parse_args(argv)

    with open(args.csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    written, skipped = import_rows(rows, args.recipes_dir)
    print(f"✅ {written} written, {skipped} already present -> {args.recipes_dir}")
    print("   Ingredients are empty by design; add them per recipe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
