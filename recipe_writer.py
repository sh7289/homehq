"""Write recipe markdown files.

Slug generation is imported from catalog_writer rather than reimplemented --
two slug functions that drift apart is a bug waiting to happen.
"""

import os

import yaml

import recipe_loader
from catalog_writer import _unique_path, slugify


def _write(path, name, frontmatter, ingredients, body):
    document = {"name": name}
    document.update({k: v for k, v in (frontmatter or {}).items() if v not in (None, "")})
    if ingredients:
        document["ingredients"] = ingredients

    with open(path, "w", encoding="utf-8") as f:
        f.write("---\n")
        # sort_keys=False keeps `name` first so the file reads sensibly when
        # edited by hand, which is the whole point of markdown storage.
        yaml.safe_dump(document, f, sort_keys=False, allow_unicode=True, width=100)
        f.write("---\n")
        f.write((body or "").strip() + "\n")
    return path


def write_recipe(recipes_dir, name, frontmatter, ingredients, body):
    """Write a new recipe file and return its path.

    Never overwrites: a colliding slug gets -2, -3, ... like catalog items.
    """
    os.makedirs(recipes_dir, exist_ok=True)
    slug = _unique_path(recipes_dir, slugify(name), ".md")
    return _write(
        os.path.join(recipes_dir, f"{slug}.md"), name, frontmatter, ingredients, body
    )


def set_ingredients(recipes_dir, slug, ingredients):
    """Replace one recipe's ingredient list in place.

    Everything else in the file -- frontmatter and body -- is round-tripped
    unchanged, so the imported metadata and notes survive.
    """
    path = os.path.join(recipes_dir, f"{slug}.md")
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    recipes = {r.slug: r for r in recipe_loader.load_recipes(recipes_dir)}
    recipe = recipes.get(slug)
    if recipe is None:
        raise FileNotFoundError(path)

    return _write(path, recipe.name, recipe.frontmatter, ingredients, recipe.body)


def ingredients_to_lines(ingredients):
    """Render ingredients into the `name | qty | unit | flags` textarea format."""
    lines = []
    for ingredient in ingredients or []:
        flags = []
        if ingredient.get("fresh"):
            flags.append("fresh")
        if ingredient.get("staple"):
            flags.append("staple")
        quantity = ingredient.get("quantity")
        parts = [
            str(ingredient.get("name", "")),
            "" if quantity is None else str(quantity),
            ingredient.get("unit") or "",
            ",".join(flags),
        ]
        # Trim trailing empties so a bare ingredient is just its name.
        while parts and not parts[-1]:
            parts.pop()
        lines.append(" | ".join(parts))
    return "\n".join(lines)
