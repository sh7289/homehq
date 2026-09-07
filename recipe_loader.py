"""Load recipes from markdown files with YAML frontmatter.

Deliberately a sibling of `content_loader`, not a reuse of it, for two
reasons:

1. `load_catalog` turns every subdirectory of the content dir into a catalog
   category. Recipes living there would show up as a nav tab, a section of
   the insurance report, and rows in the insurance CSV.
2. `content_loader._stringify_scalars` coerces every scalar to a string,
   which is right for insurance frontmatter and wrong here -- a recipe's
   quantities and effort need to stay numeric to be filtered and summed.
"""

import logging
import os
import re
from dataclasses import dataclass, field

import yaml

logger = logging.getLogger(__name__)

_FRONTMATTER_DELIM = "---"

# Matches the id shape this app assigns to steps (s1, s2, ...). Used only to
# decide which `inputs` entries are step references worth order-checking --
# anything else is assumed to be an ingredient name (or other free text) and
# is deliberately left alone, matching the existing tolerant handling of
# unrecognized ingredient references elsewhere (see recipe_steps.py).
_STEP_ID_PATTERN = re.compile(r"^s\d+$")

# Kept numeric rather than stringified, unlike the catalog loader.
_NUMERIC_KEYS = frozenset(
    {"quantity", "effort", "serves", "active_minutes", "total_minutes"}
)


@dataclass
class Recipe:
    slug: str
    name: str
    frontmatter: dict = field(default_factory=dict)
    ingredients: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    body: str = ""

    @property
    def kind(self):
        return self.frontmatter.get("kind") or "other"

    @property
    def cuisine(self):
        return self.frontmatter.get("cuisine")

    @property
    def is_favorite(self):
        return bool(self.frontmatter.get("favorite"))

    @property
    def effort(self):
        value = self.frontmatter.get("effort")
        return value if isinstance(value, (int, float)) else None


def _coerce_number(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else number


def normalize_ingredient(raw):
    """Accept either a mapping or a bare string, and fill in the defaults.

    `staple` means "we always have this, stop asking". `fresh` means "produce,
    dairy or fresh meat" -- fresh things are never netted against inventory
    because the pantry deliberately doesn't track them.
    """
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict):
        raise ValueError(f"ingredient is neither a string nor a mapping: {raw!r}")

    name = raw.get("name")
    if not name:
        raise ValueError("ingredient is missing a name")

    return {
        "name": str(name),
        "quantity": _coerce_number(raw.get("quantity")),
        "unit": raw.get("unit"),
        "staple": bool(raw.get("staple", False)),
        "fresh": bool(raw.get("fresh", False)),
    }


def normalize_step(raw):
    """A step is {id, action, inputs}. Model output, so validate it."""
    if not isinstance(raw, dict):
        raise ValueError(f"step is not a mapping: {raw!r}")
    step_id = raw.get("id")
    if not step_id:
        raise ValueError("step is missing an id")
    inputs = raw.get("inputs") or []
    if not isinstance(inputs, list):
        raise ValueError("step inputs must be a list")
    return {
        "id": str(step_id),
        "action": str(raw.get("action") or ""),
        "inputs": [str(i) for i in inputs if i],
    }


def normalize_steps(raw_steps):
    steps = []
    for raw in raw_steps or []:
        try:
            steps.append(normalize_step(raw))
        except (ValueError, TypeError) as exc:
            logger.warning("Skipping step: %s", exc)
    return steps


class StepOrderError(ValueError):
    """A step's inputs reference another step that isn't a valid, earlier step.

    Nothing enforced this before this class existed: a step could reference a
    step id that doesn't exist, a later step, or itself, and it would be
    written to disk unnoticed. This is the validation that closes that gap.
    """


def validate_step_order(steps):
    """Raise StepOrderError if any step references a same-or-later step, or
    a step id that doesn't exist among `steps`.

    Only inputs shaped like a step id (`s<digits>`) are checked -- anything
    else is assumed to be an ingredient reference, which is intentionally
    left unvalidated (an unrecognized ingredient name is tolerated
    elsewhere, e.g. recipe_steps.py's engineering table, not rejected).
    """
    all_ids = {step["id"] for step in steps}
    seen_ids = set()
    problems = []

    for index, step in enumerate(steps, start=1):
        label = step.get("action") or step["id"]
        for ref in step.get("inputs") or []:
            if not _STEP_ID_PATTERN.match(ref):
                continue
            if ref in seen_ids:
                continue  # a real, earlier step -- fine
            if ref == step["id"]:
                problems.append(f'Step {index} ("{label}") can\'t depend on itself.')
            elif ref in all_ids:
                problems.append(
                    f'Step {index} ("{label}") depends on {ref}, which comes later. '
                    "A step can only use ingredients or steps that come before it."
                )
            else:
                problems.append(
                    f'Step {index} ("{label}") depends on {ref}, which doesn\'t exist.'
                )
        seen_ids.add(step["id"])

    if problems:
        raise StepOrderError(" ".join(problems))


def _parse_file(path):
    text = open(path, encoding="utf-8").read()
    if not text.startswith(_FRONTMATTER_DELIM):
        raise ValueError("missing frontmatter delimiter")
    _, raw_frontmatter, body = text.split(_FRONTMATTER_DELIM, 2)
    frontmatter = yaml.safe_load(raw_frontmatter) or {}
    if not isinstance(frontmatter, dict):
        raise ValueError("frontmatter did not parse to a mapping")

    # Popped, not read: leaving it behind means a rename writes the frontmatter
    # copy back over the new name.
    name = frontmatter.pop("name", None)
    if not name:
        raise ValueError("recipe is missing required 'name' field")

    for key in _NUMERIC_KEYS:
        if key in frontmatter:
            frontmatter[key] = _coerce_number(frontmatter[key])

    ingredients = [
        normalize_ingredient(raw) for raw in (frontmatter.pop("ingredients", None) or [])
    ]
    steps = normalize_steps(frontmatter.pop("steps", None))
    return str(name), frontmatter, ingredients, steps, body.strip()


def load_recipes(recipes_dir):
    """Parse every .md in recipes_dir, skipping and logging bad files."""
    recipes = []
    if not os.path.isdir(recipes_dir):
        return recipes

    seen_slugs = set()
    for filename in sorted(os.listdir(recipes_dir)):
        if not filename.endswith(".md"):
            continue
        slug = filename[: -len(".md")]
        path = os.path.join(recipes_dir, filename)

        if slug.lower() in seen_slugs:
            logger.warning("Skipping %s: duplicate slug '%s'", path, slug)
            continue

        try:
            name, frontmatter, ingredients, steps, body = _parse_file(path)
        except Exception as exc:
            logger.warning("Skipping %s: %s", path, exc)
            continue

        seen_slugs.add(slug.lower())
        recipes.append(
            Recipe(
                slug=slug,
                name=name,
                frontmatter=frontmatter,
                ingredients=ingredients,
                steps=steps,
                body=body,
            )
        )

    return recipes
