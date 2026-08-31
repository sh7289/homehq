import sys

sys.path.insert(0, "scripts")


def test_parse_items_groups_bullets_under_their_section():
    from import_pantry_from_markdown import parse_items

    text = """# Pantry Inventory

## Herbs

-   Oregano, dried
-   Thyme, ground

## Oils & Fats

-   Olive oil
"""

    items = list(parse_items(text))

    assert items == [
        ("Oregano, dried", "Herbs"),
        ("Thyme, ground", "Herbs"),
        ("Olive oil", "Oils & Fats"),
    ]


def test_parse_items_skips_notes_section():
    from import_pantry_from_markdown import parse_items

    text = """## Herbs

-   Oregano, dried

## Notes

-   Quantities are not recorded unless specifically mentioned.
"""

    items = list(parse_items(text))

    assert items == [("Oregano, dried", "Herbs")]
