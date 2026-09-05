"""Sub-areas within the pantry and freezer.

A section is *what kind of thing* an item is (canned goods, spices). It is
deliberately separate from an item's `location`, which is free text for where
it physically sits ("door rack", "back of the top shelf"). An item has both.

The lists are fixed rather than free text so that grouping stays stable and
the AI import path has a closed vocabulary to choose from.
"""

PANTRY_SECTIONS = (
    ("bulk-dry", "Bulk & Dry Goods"),
    ("canned", "Canned Goods"),
    ("sauces-oils", "Sauces, Oils & Sweeteners"),
    ("baking", "Baking"),
    ("spices", "Spices & Herbs"),
    ("other", "Other"),
)

FREEZER_SECTIONS = (
    ("meat", "Meat & Fish"),
    ("vegetables", "Vegetables"),
    ("prepared", "Prepared & Leftovers"),
    ("other", "Other"),
)

FALLBACK = "other"


def sections_for(storage):
    """The (key, label) pairs valid for a storage area, in display order."""
    return FREEZER_SECTIONS if storage == "freezer" else PANTRY_SECTIONS


def normalize(storage, value):
    """Coerce anything to a section key valid for this storage area.

    Unknown keys, blanks, None, and keys borrowed from the other storage area
    all collapse to "other" rather than raising -- this runs on form input and
    on AI-extracted values, and neither is trustworthy.
    """
    valid = {key for key, _label in sections_for(storage)}
    return value if value in valid else FALLBACK


def label_for(storage, key):
    for section_key, label in sections_for(storage):
        if section_key == key:
            return label
    return dict(sections_for(storage))[FALLBACK]


def group_items(items, storage):
    """Bucket items into their sections, in declared order.

    Returns a list of {"key", "label", "rows"} for non-empty sections only,
    so a mostly-empty pantry doesn't render a wall of blank headings.

    The bucket is called "rows" rather than "items" because Jinja resolves
    `group.items` to the dict's own items() method, not the key.
    """
    buckets = {}
    for item in items:
        key = normalize(storage, item.get("section"))
        buckets.setdefault(key, []).append(item)

    return [
        {"key": key, "label": label, "rows": buckets[key]}
        for key, label in sections_for(storage)
        if key in buckets
    ]
