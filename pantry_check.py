"""Classify a recipe's ingredients into the four shopping-list buckets.

This is the four-bucket design from
docs/plans/2026-09-05-recipes-and-meal-planning.md's "Fresh goods are never
netted" section (decided 2026-09-05) -- NOT the earlier, superseded
three-bucket `check_ingredients` sketch elsewhere in that same document.

    Bucket    Condition                                    Default
    fresh     ingredient["fresh"] is True                  always listed, pre-checked
    on_hand   ingredient["staple"] is True and a match      not listed by default
    check     a match exists, not staple, not fresh         not listed by default
    missing   no match                                      listed, pre-checked

Deliberately dumb: no unit conversion, no deficit arithmetic ("need 0.5 more
cups") anywhere here. A matched inventory row is only ever carried through
verbatim (its own name/quantity/unit) for a human to read and judge for
themselves -- never combined with the recipe's own quantity to compute
anything. Matching itself is entirely delegated to
`matching.find_best_match`, unmodified.
"""

import matching


def check_ingredients(ingredients, inventory):
    """Tag each ingredient in `ingredients` with its bucket and, if any, the
    inventory row `matching.find_best_match` found for it in `inventory`.

    `ingredients` is a list of normalized recipe ingredient dicts (name,
    quantity, unit, staple, fresh -- see recipe_loader.normalize_ingredient;
    already scaled to the caller's desired servings, if that matters to
    them, since this function has no opinion on scaling). `inventory` is a
    single combined pool of candidate rows -- the caller is expected to pass
    pantry and freezer rows together (a recipe doesn't care where an
    ingredient lives), each shaped like `db.list_items` returns.

    Returns one row per ingredient, in the same order as `ingredients`:
        {"ingredient": <the ingredient dict>, "bucket": <bucket name>,
         "match": <matched inventory row dict, or None>}

    Fresh is checked first and wins outright: a fuzzy match on a fresh
    ingredient is still surfaced (in "match", for an informational
    annotation) but never changes its bucket -- fresh ingredients are never
    netted against inventory.
    """
    rows = []
    for ingredient in ingredients:
        match = matching.find_best_match(ingredient["name"], inventory)

        if ingredient.get("fresh"):
            bucket = "fresh"
        elif match is None:
            bucket = "missing"
        elif ingredient.get("staple"):
            bucket = "on_hand"
        else:
            bucket = "check"

        rows.append({"ingredient": ingredient, "bucket": bucket, "match": match})

    return rows
