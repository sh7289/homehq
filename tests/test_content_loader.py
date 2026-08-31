import logging

import pytest


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_loads_a_well_formed_item(tmp_path):
    from content_loader import load_catalog

    _write(
        tmp_path / "kitchen" / "dutch-oven.md",
        """---
name: Dutch Oven
category: kitchen
brand: Le Creuset
purchase_date: 2022-03-01
purchase_price: 380.00
location: Lower cabinet
photos:
  - kitchen/dutch-oven-1.jpg
---
Care instructions go here.
""",
    )

    catalog = load_catalog(str(tmp_path))

    items = catalog["kitchen"]
    assert len(items) == 1
    item = items[0]
    assert item.slug == "dutch-oven"
    assert item.name == "Dutch Oven"
    assert item.category == "kitchen"
    assert item.frontmatter["brand"] == "Le Creuset"
    assert item.frontmatter["purchase_date"] == "2022-03-01"
    assert item.frontmatter["purchase_price"] == "380.0"
    assert item.photos == ["kitchen/dutch-oven-1.jpg"]
    assert "Care instructions" in item.body


def test_categories_come_from_subdirectory_names(tmp_path):
    from content_loader import load_catalog

    _write(tmp_path / "kitchen" / "pan.md", "---\nname: Pan\ncategory: kitchen\n---\n")
    _write(tmp_path / "tools" / "drill.md", "---\nname: Drill\ncategory: tools\n---\n")

    catalog = load_catalog(str(tmp_path))

    assert set(catalog.keys()) == {"kitchen", "tools"}


def test_malformed_frontmatter_is_skipped_not_fatal(tmp_path, caplog):
    from content_loader import load_catalog

    _write(tmp_path / "kitchen" / "broken.md", "---\nname: [unclosed\n---\nbody\n")
    _write(tmp_path / "kitchen" / "good.md", "---\nname: Good Item\ncategory: kitchen\n---\n")

    with caplog.at_level(logging.WARNING):
        catalog = load_catalog(str(tmp_path))

    assert len(catalog["kitchen"]) == 1
    assert catalog["kitchen"][0].name == "Good Item"
    assert "broken.md" in caplog.text


def test_duplicate_slug_within_category_is_skipped_not_fatal(tmp_path, caplog):
    from content_loader import load_catalog

    _write(tmp_path / "kitchen" / "pan.md", "---\nname: Pan One\ncategory: kitchen\n---\n")
    # Second file with the same slug via a different extension-adjacent name is contrived;
    # instead simulate by writing then renaming is unnecessary -- test via two dirs is not
    # applicable since slugs are per-category filenames, which the filesystem already
    # prevents from colliding. So directly exercise the loader's dedup guard via a case
    # collision, which the filesystem *does* allow to differ on Linux.
    _write(tmp_path / "kitchen" / "PAN.md", "---\nname: Pan Two\ncategory: kitchen\n---\n")

    with caplog.at_level(logging.WARNING):
        catalog = load_catalog(str(tmp_path))

    slugs = [item.slug for item in catalog["kitchen"]]
    assert len(slugs) == len(set(s.lower() for s in slugs))
