import logging
import os
from dataclasses import dataclass, field

import yaml

logger = logging.getLogger(__name__)

_FRONTMATTER_DELIM = "---"


@dataclass
class Item:
    slug: str
    category: str
    name: str
    frontmatter: dict = field(default_factory=dict)
    photos: list = field(default_factory=list)
    body: str = ""


def _stringify_scalars(value):
    if isinstance(value, dict):
        return {k: _stringify_scalars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify_scalars(v) for v in value]
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _parse_file(path):
    text = open(path, encoding="utf-8").read()
    if not text.startswith(_FRONTMATTER_DELIM):
        raise ValueError("missing frontmatter delimiter")
    _, raw_frontmatter, body = text.split(_FRONTMATTER_DELIM, 2)
    frontmatter = yaml.safe_load(raw_frontmatter) or {}
    if not isinstance(frontmatter, dict):
        raise ValueError("frontmatter did not parse to a mapping")
    frontmatter = _stringify_scalars(frontmatter)
    name = frontmatter.get("name")
    if not name:
        raise ValueError("item is missing required 'name' field")
    photos = frontmatter.get("photos") or []
    return name, frontmatter, photos, body.strip()


def load_catalog(content_dir):
    catalog = {}
    if not os.path.isdir(content_dir):
        return catalog

    for category in sorted(os.listdir(content_dir)):
        category_dir = os.path.join(content_dir, category)
        if not os.path.isdir(category_dir):
            continue

        items = []
        seen_slugs = set()
        for filename in sorted(os.listdir(category_dir)):
            if not filename.endswith(".md"):
                continue
            slug = filename[: -len(".md")]
            path = os.path.join(category_dir, filename)

            if slug.lower() in seen_slugs:
                logger.warning(
                    "Skipping %s: duplicate slug '%s' in category '%s'",
                    path,
                    slug,
                    category,
                )
                continue

            try:
                name, frontmatter, photos, body = _parse_file(path)
            except Exception as exc:
                logger.warning("Skipping %s: %s", path, exc)
                continue

            seen_slugs.add(slug.lower())
            items.append(
                Item(
                    slug=slug,
                    category=category,
                    name=name,
                    frontmatter=frontmatter,
                    photos=photos,
                    body=body,
                )
            )

        catalog[category] = items

    return catalog
