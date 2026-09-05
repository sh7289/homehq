"""Write recipe markdown files.

Slug generation is imported from catalog_writer rather than reimplemented --
two slug functions that drift apart is a bug waiting to happen.
"""

import os

import yaml

from catalog_writer import _unique_path, slugify


def write_recipe(recipes_dir, name, frontmatter, ingredients, body):
    """Write a recipe file and return its path.

    Never overwrites: a colliding slug gets -2, -3, ... like catalog items.
    """
    os.makedirs(recipes_dir, exist_ok=True)
    slug = _unique_path(recipes_dir, slugify(name), ".md")
    path = os.path.join(recipes_dir, f"{slug}.md")

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
