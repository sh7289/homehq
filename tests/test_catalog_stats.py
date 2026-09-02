from content_loader import Item


def _item(category, name, estimated_value=None, photos=None):
    frontmatter = {"name": name, "category": category}
    if estimated_value is not None:
        frontmatter["estimated_value"] = estimated_value
    return Item(
        slug=name.lower().replace(" ", "-"),
        category=category,
        name=name,
        frontmatter=frontmatter,
        photos=photos or [],
    )


def test_compute_stats_totals_items_and_value(tmp_path):
    from catalog_stats import compute_stats

    catalog = {
        "valuables": [_item("valuables", "Turntable", estimated_value="1000")],
        "musical-instruments": [
            _item("musical-instruments", "Piano", estimated_value="1500", photos=["a.jpg"])
        ],
    }

    stats = compute_stats(catalog)

    assert stats["total_items"] == 2
    assert stats["total_value"] == 2500.0
    assert stats["items_with_value"] == 2
    assert stats["items_missing_photo"] == 1  # turntable has no photos


def test_compute_stats_skips_unparseable_values(tmp_path):
    from catalog_stats import compute_stats

    catalog = {
        "valuables": [
            _item("valuables", "Turntable", estimated_value="not-a-number"),
            _item("valuables", "Speaker"),  # no estimated_value at all
        ]
    }

    stats = compute_stats(catalog)

    assert stats["total_items"] == 2
    assert stats["total_value"] == 0.0
    assert stats["items_with_value"] == 0


def test_compute_stats_per_category_breakdown(tmp_path):
    from catalog_stats import compute_stats

    catalog = {
        "valuables": [_item("valuables", "Turntable", estimated_value="1000")],
        "kitchen": [_item("kitchen", "Dutch Oven")],
    }

    stats = compute_stats(catalog)

    assert stats["by_category"]["valuables"] == {"count": 1, "total_value": 1000.0}
    assert stats["by_category"]["kitchen"] == {"count": 1, "total_value": 0.0}


def test_compute_stats_handles_empty_catalog():
    from catalog_stats import compute_stats

    stats = compute_stats({})

    assert stats["total_items"] == 0
    assert stats["total_value"] == 0.0
    assert stats["by_category"] == {}
