def test_exact_match_wins():
    from matching import find_best_match

    candidates = [{"id": 1, "name": "Rice"}, {"id": 2, "name": "Cumin"}]

    match = find_best_match("Rice", candidates)

    assert match["id"] == 1


def test_case_and_whitespace_insensitive():
    from matching import find_best_match

    candidates = [{"id": 1, "name": "Ground Cumin"}]

    match = find_best_match("  ground cumin  ", candidates)

    assert match["id"] == 1


def test_tolerates_minor_typo():
    from matching import find_best_match

    candidates = [{"id": 1, "name": "Cumin"}]

    match = find_best_match("Cummin", candidates)

    assert match["id"] == 1


def test_returns_none_when_nothing_close_enough():
    from matching import find_best_match

    candidates = [{"id": 1, "name": "Rice"}, {"id": 2, "name": "Cumin"}]

    match = find_best_match("Chainsaw", candidates)

    assert match is None


def test_returns_none_for_empty_candidates():
    from matching import find_best_match

    assert find_best_match("Rice", []) is None


def test_picks_closest_among_multiple_reasonable_candidates():
    from matching import find_best_match

    candidates = [
        {"id": 1, "name": "Ground Cinnamon"},
        {"id": 2, "name": "Cinnamon Sticks"},
    ]

    match = find_best_match("Cinnamon sticks", candidates)

    assert match["id"] == 2
