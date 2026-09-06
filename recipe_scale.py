"""Scale a recipe's ingredient quantities to a different yield.

Only the numbers move. Units are never converted (the sibling pantry plan
declined unit conversion for the same reason), and an ingredient with no
recorded amount keeps having no recorded amount -- doubling an unknown is
still unknown, and printing a number there would be a fabrication.
"""


def factor_for(serves, target):
    """Multiplier to go from a recipe's own yield to the target yield."""
    try:
        serves = float(serves or 0)
        target = float(target or 0)
    except (TypeError, ValueError):
        return 1
    if serves <= 0 or target <= 0:
        return 1
    return target / serves


def _round(value):
    """Two decimals is plenty for a kitchen; whole numbers stay whole."""
    rounded = round(value, 2)
    return int(rounded) if float(rounded).is_integer() else rounded


def scale(ingredients, factor):
    if not factor or factor <= 0:
        raise ValueError("scale factor must be positive")

    scaled = []
    for ingredient in ingredients or []:
        copy = dict(ingredient)
        quantity = copy.get("quantity")
        if isinstance(quantity, (int, float)) and not isinstance(quantity, bool):
            copy["quantity"] = _round(quantity * factor)
        scaled.append(copy)
    return scaled
