import pytest

import recipe_scale


def _ing(name, quantity, unit=None, **flags):
    base = {"name": name, "quantity": quantity, "unit": unit, "fresh": False, "staple": False}
    base.update(flags)
    return base


def test_factor_is_target_over_base():
    assert recipe_scale.factor_for(serves=2, target=4) == 2
    assert recipe_scale.factor_for(serves=4, target=2) == 0.5
    assert recipe_scale.factor_for(serves=2, target=2) == 1


def test_factor_is_one_when_the_recipe_has_no_serves():
    """Without a base yield there is nothing to scale against."""
    assert recipe_scale.factor_for(serves=None, target=4) == 1
    assert recipe_scale.factor_for(serves=0, target=4) == 1


def test_scaling_multiplies_numeric_quantities():
    scaled = recipe_scale.scale([_ing("beef", 1.5, "lb")], 2)

    assert scaled[0]["quantity"] == 3


def test_scaling_leaves_null_quantities_alone():
    """'garlic' with no amount stays with no amount -- inventing 2x of
    nothing is worse than leaving it blank."""
    scaled = recipe_scale.scale([_ing("garlic", None)], 3)

    assert scaled[0]["quantity"] is None


def test_scaling_keeps_names_units_and_flags():
    scaled = recipe_scale.scale([_ing("onion", 1, "count", fresh=True)], 2)

    assert scaled[0]["name"] == "onion"
    assert scaled[0]["unit"] == "count"
    assert scaled[0]["fresh"] is True


def test_whole_numbers_stay_whole():
    scaled = recipe_scale.scale([_ing("eggs", 2, "count")], 2)

    assert scaled[0]["quantity"] == 4
    assert isinstance(scaled[0]["quantity"], int)


def test_awkward_results_are_rounded_not_left_long():
    scaled = recipe_scale.scale([_ing("flour", 100, "g")], 1 / 3)

    assert scaled[0]["quantity"] == 33.33


def test_scaling_by_one_is_a_no_op():
    original = [_ing("beef", 1.5, "lb")]

    assert recipe_scale.scale(original, 1) == original


def test_does_not_mutate_the_input():
    original = [_ing("beef", 1.5, "lb")]

    recipe_scale.scale(original, 4)

    assert original[0]["quantity"] == 1.5


def test_rejects_a_nonsense_factor():
    with pytest.raises(ValueError):
        recipe_scale.scale([_ing("beef", 1)], 0)
