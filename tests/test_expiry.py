from datetime import date


def test_classify_returns_expired_for_past_date():
    from expiry import classify

    assert classify("2026-01-01", today=date(2026, 6, 1)) == "expired"


def test_classify_returns_expiring_soon_within_window():
    from expiry import classify

    assert classify("2026-06-15", today=date(2026, 6, 1), warning_days=30) == "expiring_soon"


def test_classify_returns_none_when_far_out():
    from expiry import classify

    assert classify("2026-12-31", today=date(2026, 6, 1), warning_days=30) is None


def test_classify_returns_none_for_missing_date():
    from expiry import classify

    assert classify(None, today=date(2026, 6, 1)) is None


def test_classify_today_counts_as_expiring_soon_not_expired():
    from expiry import classify

    assert classify("2026-06-01", today=date(2026, 6, 1)) == "expiring_soon"


def test_classify_ignores_malformed_date_instead_of_raising():
    from expiry import classify

    assert classify("not-a-date", today=date(2026, 6, 1)) is None


def test_split_by_expiry_groups_and_sorts_soonest_first():
    from expiry import split_by_expiry

    items = [
        {"id": 1, "name": "A", "expiry_date": "2026-05-01"},  # expired
        {"id": 2, "name": "B", "expiry_date": "2026-06-20"},  # expiring soon
        {"id": 3, "name": "C", "expiry_date": "2026-06-10"},  # expiring soon, sooner
        {"id": 4, "name": "D", "expiry_date": None},  # no date
        {"id": 5, "name": "E", "expiry_date": "2027-01-01"},  # far out
        {"id": 6, "name": "F", "expiry_date": "2026-04-01"},  # more expired
    ]

    expired, expiring_soon = split_by_expiry(items, today=date(2026, 6, 1), warning_days=30)

    assert [i["name"] for i in expired] == ["F", "A"]
    assert [i["name"] for i in expiring_soon] == ["C", "B"]
