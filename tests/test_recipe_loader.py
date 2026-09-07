import pytest

import recipe_loader


def test_valid_chain_passes():
    steps = [
        {"id": "s1", "action": "sear", "inputs": ["chicken"]},
        {"id": "s2", "action": "simmer", "inputs": ["s1", "tomatoes"]},
        {"id": "s3", "action": "serve", "inputs": ["s2", "tortillas"]},
    ]

    recipe_loader.validate_step_order(steps)  # must not raise


def test_ingredient_references_are_not_checked():
    """Referencing an ingredient name is never a step-order problem, even
    when that name isn't in the current ingredient list -- that's tolerated
    elsewhere (recipe_steps.py's engineering table) and must stay tolerated
    here too."""
    steps = [{"id": "s1", "action": "sear", "inputs": ["butter", "an unrecognized thing"]}]

    recipe_loader.validate_step_order(steps)  # must not raise


def test_forward_reference_is_rejected():
    steps = [
        {"id": "s1", "action": "prep", "inputs": ["s2"]},
        {"id": "s2", "action": "cook", "inputs": ["s1"]},
    ]

    with pytest.raises(recipe_loader.StepOrderError) as exc:
        recipe_loader.validate_step_order(steps)

    assert "s2" in str(exc.value)
    assert "later" in str(exc.value)


def test_self_reference_is_rejected():
    steps = [{"id": "s1", "action": "prep", "inputs": ["s1"]}]

    with pytest.raises(recipe_loader.StepOrderError) as exc:
        recipe_loader.validate_step_order(steps)

    assert "itself" in str(exc.value)


def test_reference_to_a_nonexistent_step_is_rejected():
    steps = [{"id": "s1", "action": "prep", "inputs": ["s99"]}]

    with pytest.raises(recipe_loader.StepOrderError) as exc:
        recipe_loader.validate_step_order(steps)

    assert "s99" in str(exc.value)
    assert "doesn't exist" in str(exc.value)


def test_moving_a_step_earlier_than_its_prerequisite_is_rejected():
    """The plan's explicit test case: "simmer" depends on "prep vegetables"
    finishing first. Valid before a move: prep vegetables is s1, simmer is
    s2 depending on s1. After (invalidly) moving simmer earlier, ids are
    reassigned by the new display order -- simmer is now s1, prep
    vegetables is s2 -- but simmer's dependency still points at whichever
    id prep vegetables ends up with, which is now a *later* step."""
    valid = [
        {"id": "s1", "action": "prep vegetables", "inputs": ["onion"]},
        {"id": "s2", "action": "simmer", "inputs": ["s1", "stock"]},
    ]
    recipe_loader.validate_step_order(valid)  # sanity: valid before the move

    reordered = [
        {"id": "s1", "action": "simmer", "inputs": ["s2", "stock"]},
        {"id": "s2", "action": "prep vegetables", "inputs": ["onion"]},
    ]

    with pytest.raises(recipe_loader.StepOrderError) as exc:
        recipe_loader.validate_step_order(reordered)

    assert "s2" in str(exc.value)


def test_error_names_every_problem_step():
    steps = [
        {"id": "s1", "action": "a", "inputs": ["s2"]},
        {"id": "s2", "action": "b", "inputs": ["s1"]},
        {"id": "s3", "action": "c", "inputs": ["s99"]},
    ]

    with pytest.raises(recipe_loader.StepOrderError) as exc:
        recipe_loader.validate_step_order(steps)

    message = str(exc.value)
    assert "Step 1" in message
    assert "Step 3" in message
