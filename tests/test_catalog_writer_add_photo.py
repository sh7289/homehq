import os

import yaml


def _write_item(content_dir, category, slug, frontmatter, body=""):
    category_dir = os.path.join(content_dir, category)
    os.makedirs(category_dir, exist_ok=True)
    path = os.path.join(category_dir, f"{slug}.md")
    with open(path, "w") as f:
        f.write("---\n")
        yaml.safe_dump(frontmatter, f, sort_keys=False)
        f.write("---\n")
        f.write(body)
    return path


def test_add_photo_to_item_with_no_existing_photos(tmp_path):
    from catalog_writer import add_photo_to_item

    content_dir = tmp_path / "content"
    photos_dir = tmp_path / "photos"
    _write_item(content_dir, "valuables", "turntable", {"name": "Turntable", "category": "valuables"})
    source_image = tmp_path / "uploads" / "photo1.jpg"
    os.makedirs(source_image.parent)
    source_image.write_bytes(b"photo-one")

    photo_rel_path = add_photo_to_item(
        str(content_dir), str(photos_dir), "valuables", "turntable", str(source_image)
    )

    assert photo_rel_path.startswith("valuables/")
    assert (photos_dir / photo_rel_path).read_bytes() == b"photo-one"
    text = (content_dir / "valuables" / "turntable.md").read_text()
    parsed = yaml.safe_load(text.split("---")[1])
    assert parsed["photos"] == [photo_rel_path]


def test_add_photo_to_item_appends_to_existing_photos(tmp_path):
    from catalog_writer import add_photo_to_item

    content_dir = tmp_path / "content"
    photos_dir = tmp_path / "photos"
    _write_item(
        content_dir,
        "valuables",
        "turntable",
        {"name": "Turntable", "category": "valuables", "photos": ["valuables/turntable-1.jpg"]},
    )
    source_image = tmp_path / "uploads" / "photo2.jpg"
    os.makedirs(source_image.parent)
    source_image.write_bytes(b"photo-two")

    photo_rel_path = add_photo_to_item(
        str(content_dir), str(photos_dir), "valuables", "turntable", str(source_image)
    )

    text = (content_dir / "valuables" / "turntable.md").read_text()
    parsed = yaml.safe_load(text.split("---")[1])
    assert parsed["photos"] == ["valuables/turntable-1.jpg", photo_rel_path]
    assert photo_rel_path != "valuables/turntable-1.jpg"


def test_add_photo_to_item_preserves_body_and_other_frontmatter(tmp_path):
    from catalog_writer import add_photo_to_item

    content_dir = tmp_path / "content"
    photos_dir = tmp_path / "photos"
    _write_item(
        content_dir,
        "valuables",
        "turntable",
        {"name": "Turntable", "category": "valuables", "brand": "Technics"},
        body="Some care notes here.",
    )
    source_image = tmp_path / "uploads" / "photo1.jpg"
    os.makedirs(source_image.parent)
    source_image.write_bytes(b"photo-one")

    add_photo_to_item(str(content_dir), str(photos_dir), "valuables", "turntable", str(source_image))

    text = (content_dir / "valuables" / "turntable.md").read_text()
    frontmatter_text, body = text.split("---")[1], text.split("---", 2)[2]
    parsed = yaml.safe_load(frontmatter_text)
    assert parsed["brand"] == "Technics"
    assert body.strip() == "Some care notes here."
