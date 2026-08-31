import os

import yaml


def test_slugify_basic():
    from catalog_writer import slugify

    assert slugify("Kind of Blue") == "kind-of-blue"
    assert slugify("  Weird!! Name??  ") == "weird-name"


def test_write_catalog_item_creates_markdown_file_with_frontmatter(tmp_path):
    from catalog_writer import write_catalog_item

    content_dir = tmp_path / "content"
    photos_dir = tmp_path / "photos"

    md_path, photo_rel_path = write_catalog_item(
        str(content_dir),
        str(photos_dir),
        category="valuables",
        name="Kind of Blue",
        frontmatter={"brand": "Columbia Records", "model": None},
        body="Miles Davis, 1959 pressing.",
    )

    assert os.path.exists(md_path)
    text = open(md_path).read()
    assert text.startswith("---\n")
    _, raw_frontmatter, body = text.split("---", 2)
    parsed = yaml.safe_load(raw_frontmatter)
    assert parsed["name"] == "Kind of Blue"
    assert parsed["category"] == "valuables"
    assert parsed["brand"] == "Columbia Records"
    assert body.strip() == "Miles Davis, 1959 pressing."
    assert photo_rel_path is None


def test_write_catalog_item_copies_source_photo(tmp_path):
    from catalog_writer import write_catalog_item

    content_dir = tmp_path / "content"
    photos_dir = tmp_path / "photos"
    source_image = tmp_path / "uploads" / "record1.jpg"
    os.makedirs(source_image.parent)
    source_image.write_bytes(b"fake-jpeg-bytes")

    md_path, photo_rel_path = write_catalog_item(
        str(content_dir),
        str(photos_dir),
        category="valuables",
        name="Kind of Blue",
        frontmatter={},
        body="",
        source_image_path=str(source_image),
    )

    assert photo_rel_path.startswith("valuables/")
    assert (photos_dir / photo_rel_path).exists()
    assert (photos_dir / photo_rel_path).read_bytes() == b"fake-jpeg-bytes"


def test_write_catalog_item_dedupes_slug_collisions(tmp_path):
    from catalog_writer import write_catalog_item

    content_dir = tmp_path / "content"
    photos_dir = tmp_path / "photos"

    first_path, _ = write_catalog_item(
        str(content_dir), str(photos_dir), category="valuables", name="Test Item",
        frontmatter={}, body="",
    )
    second_path, _ = write_catalog_item(
        str(content_dir), str(photos_dir), category="valuables", name="Test Item",
        frontmatter={}, body="",
    )

    assert first_path != second_path
    assert os.path.exists(first_path)
    assert os.path.exists(second_path)
