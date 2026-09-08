from pantry_check import check_ingredients


def _ingredient(name, quantity=None, unit=None, staple=False, fresh=False):
    return {
        "name": name,
        "quantity": quantity,
        "unit": unit,
        "staple": staple,
        "fresh": fresh,
    }


def test_exact_name_match_is_on_hand_when_staple():
    ingredients = [_ingredient("Flour", staple=True)]
    inventory = [{"id": 1, "name": "Flour", "quantity": 2, "unit": "kg", "storage": "pantry"}]

    rows = check_ingredients(ingredients, inventory)

    assert rows[0]["bucket"] == "on_hand"
    assert rows[0]["match"]["id"] == 1


def test_fuzzy_match_is_check_when_not_staple():
    ingredients = [_ingredient("Cummin")]
    inventory = [{"id": 1, "name": "Cumin", "quantity": 1, "unit": "jar", "storage": "pantry"}]

    rows = check_ingredients(ingredients, inventory)

    assert rows[0]["bucket"] == "check"
    assert rows[0]["match"]["name"] == "Cumin"


def test_no_match_is_missing():
    ingredients = [_ingredient("Saffron threads")]
    inventory = [{"id": 1, "name": "Flour", "quantity": 2, "unit": "kg", "storage": "pantry"}]

    rows = check_ingredients(ingredients, inventory)

    assert rows[0]["bucket"] == "missing"
    assert rows[0]["match"] is None


def test_fresh_is_its_own_bucket_regardless_of_a_match():
    """A fuzzy match on a fresh ingredient is still surfaced (for the
    annotation) but must never move it out of the fresh bucket -- fresh
    ingredients are never netted against inventory."""
    ingredients = [_ingredient("Onions", fresh=True, staple=True)]
    inventory = [{"id": 1, "name": "Onions", "quantity": 3, "unit": "count", "storage": "pantry"}]

    rows = check_ingredients(ingredients, inventory)

    assert rows[0]["bucket"] == "fresh"
    assert rows[0]["match"]["id"] == 1


def test_fresh_with_no_match_is_still_fresh_not_missing():
    ingredients = [_ingredient("Cilantro", fresh=True)]

    rows = check_ingredients(ingredients, [])

    assert rows[0]["bucket"] == "fresh"
    assert rows[0]["match"] is None


def test_matches_across_both_pantry_and_freezer_in_one_pool():
    """The caller is expected to combine pantry + freezer rows into one
    candidate pool before calling this -- a recipe doesn't care where the
    peas live -- so this just confirms a match from either storage counts."""
    ingredients = [_ingredient("Peas")]
    inventory = [{"id": 1, "name": "Peas", "quantity": 1, "unit": "bag", "storage": "freezer"}]

    rows = check_ingredients(ingredients, inventory)

    assert rows[0]["bucket"] == "check"
    assert rows[0]["match"]["storage"] == "freezer"


def test_never_computes_a_deficit():
    """The matched row's own quantity is carried through untouched -- never
    combined with the recipe's quantity into a "need N more" figure. This
    guards against a future regression that starts doing that arithmetic."""
    ingredients = [_ingredient("Rice", quantity=4, unit="cups")]
    inventory = [{"id": 1, "name": "Rice", "quantity": 1, "unit": "cup", "storage": "pantry"}]

    rows = check_ingredients(ingredients, inventory)

    row = rows[0]
    assert row["ingredient"]["quantity"] == 4
    assert row["match"]["quantity"] == 1
    # No deficit/needed/remaining key of any kind is ever introduced.
    assert set(row.keys()) == {"ingredient", "bucket", "match"}


def test_staple_without_a_match_is_missing_not_on_hand():
    """staple alone isn't enough -- on_hand also requires a fuzzy match."""
    ingredients = [_ingredient("Truffle salt", staple=True)]

    rows = check_ingredients(ingredients, [])

    assert rows[0]["bucket"] == "missing"


def test_order_and_count_are_preserved():
    ingredients = [
        _ingredient("Onions", fresh=True),
        _ingredient("Flour", staple=True),
        _ingredient("Cummin"),
        _ingredient("Saffron"),
    ]
    inventory = [
        {"id": 1, "name": "Flour", "quantity": 2, "unit": "kg", "storage": "pantry"},
        {"id": 2, "name": "Cumin", "quantity": 1, "unit": "jar", "storage": "pantry"},
    ]

    rows = check_ingredients(ingredients, inventory)

    assert [r["bucket"] for r in rows] == ["fresh", "on_hand", "check", "missing"]
    assert [r["ingredient"]["name"] for r in rows] == [
        "Onions", "Flour", "Cummin", "Saffron",
    ]
