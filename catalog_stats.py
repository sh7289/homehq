def _parse_value(raw):
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def compute_stats(catalog):
    """catalog: dict[category] -> list[Item]. Returns summary stats used by
    the /report page and the home index."""
    total_items = 0
    total_value = 0.0
    items_with_value = 0
    items_missing_photo = 0
    by_category = {}

    for category, items in catalog.items():
        category_total = 0.0
        for item in items:
            total_items += 1
            value = _parse_value(item.frontmatter.get("estimated_value"))
            if value is not None:
                total_value += value
                category_total += value
                items_with_value += 1
            if not item.photos:
                items_missing_photo += 1
        by_category[category] = {"count": len(items), "total_value": category_total}

    return {
        "total_items": total_items,
        "total_value": total_value,
        "items_with_value": items_with_value,
        "items_missing_photo": items_missing_photo,
        "by_category": by_category,
    }
