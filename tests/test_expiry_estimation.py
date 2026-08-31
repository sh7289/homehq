from datetime import date


def test_effective_expiry_prefers_recorded_expiry_date():
    from expiry import effective_expiry

    item = {"expiry_date": "2026-05-01", "acquired_date": "2020-01-01", "shelf_life_days": None}

    date_str, estimated = effective_expiry(item, storage="pantry")

    assert date_str == "2026-05-01"
    assert estimated is False


def test_effective_expiry_falls_back_to_acquired_date_plus_default_shelf_life():
    from expiry import effective_expiry

    item = {"expiry_date": None, "acquired_date": "2024-01-01", "shelf_life_days": None}

    date_str, estimated = effective_expiry(item, storage="pantry")

    assert date_str == "2026-12-31"  # +1095 days (3 years, spanning a leap day) for pantry
    assert estimated is True


def test_effective_expiry_uses_freezer_default_shelf_life():
    from expiry import effective_expiry

    item = {"expiry_date": None, "acquired_date": "2026-01-01", "shelf_life_days": None}

    date_str, estimated = effective_expiry(item, storage="freezer")

    assert date_str == "2027-01-01"  # +365 days default for freezer
    assert estimated is True


def test_effective_expiry_respects_per_item_shelf_life_override():
    from expiry import effective_expiry

    item = {"expiry_date": None, "acquired_date": "2026-01-01", "shelf_life_days": 30}

    date_str, estimated = effective_expiry(item, storage="pantry")

    assert date_str == "2026-01-31"
    assert estimated is True


def test_effective_expiry_returns_none_when_no_dates_recorded():
    from expiry import effective_expiry

    item = {"expiry_date": None, "acquired_date": None, "shelf_life_days": None}

    date_str, estimated = effective_expiry(item, storage="pantry")

    assert date_str is None
    assert estimated is False


def test_split_by_expiry_annotates_items_with_effective_expiry_status():
    from expiry import split_by_expiry

    items = [
        {
            "id": 1,
            "name": "Old spice",
            "expiry_date": None,
            "acquired_date": "2020-01-01",
            "shelf_life_days": None,
        },
        {
            "id": 2,
            "name": "Fresh spice",
            "expiry_date": None,
            "acquired_date": "2026-05-25",
            "shelf_life_days": None,
        },
    ]

    expired, expiring_soon = split_by_expiry(items, storage="pantry", today=date(2026, 6, 1))

    assert [i["name"] for i in expired] == ["Old spice"]
    assert expired[0]["expiry_status"] == "expired"
    assert expired[0]["effective_expiry_estimated"] is True
    assert items[0]["expiry_status"] == "expired"  # original dict mutated in place
