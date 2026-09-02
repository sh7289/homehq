import os


def _write_item(content_dir, category, slug, name):
    category_dir = os.path.join(content_dir, category)
    os.makedirs(category_dir, exist_ok=True)
    with open(os.path.join(category_dir, f"{slug}.md"), "w") as f:
        f.write(f"---\nname: {name}\ncategory: {category}\n---\n")


def test_all_items_returns_full_catalog_dict(tmp_path):
    from catalog_store import CatalogStore

    content_dir = str(tmp_path)
    _write_item(content_dir, "kitchen", "pan", "Pan")
    _write_item(content_dir, "tools", "drill", "Drill")

    store = CatalogStore(content_dir)
    store.reload()

    catalog = store.all_items()

    assert set(catalog.keys()) == {"kitchen", "tools"}
    assert catalog["kitchen"][0].name == "Pan"
