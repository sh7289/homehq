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
from dataclasses import dataclass, field

import yaml

logger = logging.getLogger(__name__)

_FRONTMATTER_DELIM = "---"

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


def _parse_file(path):
    text = open(path, encoding="utf-8").read()
    if not text.startswith(_FRONTMATTER_DELIM):
        raise ValueError("missing frontmatter delimiter")
    _, raw_frontmatter, body = text.split(_FRONTMATTER_DELIM, 2)
    frontmatter = yaml.safe_load(raw_frontmatter) or {}
    if not isinstance(frontmatter, dict):
        raise ValueError("frontmatter did not parse to a mapping")

    name = frontmatter.get("name")
    if not name:
        raise ValueError("recipe is missing required 'name' field")

    for key in _NUMERIC_KEYS:
        if key in frontmatter:
            frontmatter[key] = _coerce_number(frontmatter[key])

    ingredients = [
        normalize_ingredient(raw) for raw in (frontmatter.pop("ingredients", None) or [])
    ]
    return str(name), frontmatter, ingredients, body.strip()


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
            name, frontmatter, ingredients, body = _parse_file(path)
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
                body=body,
            )
        )

    return recipes
