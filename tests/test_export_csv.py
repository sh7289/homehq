def test_pantry_table_returns_header_and_escaped_rows():
    from export_csv import pantry_table

    items = [
        {
            "name": "=evil",
            "quantity": 1,
            "unit": "bag",
            "location": "shelf",
            "expiry_date": "2026-12-01",
            "updated_at": "t",
        }
    ]

    header, rows = pantry_table(items)

    assert header == ["name", "quantity", "unit", "location", "expiry_date", "updated_at"]
    assert rows == [["'=evil", "1", "bag", "shelf", "2026-12-01", "t"]]


def test_full_catalog_table_flattens_all_categories_with_value():
    from content_loader import Item
    from export_csv import full_catalog_table

    catalog = {
        "kitchen": [Item(slug="pan", category="kitchen", name="Pan", frontmatter={})],
        "valuables": [
            Item(
                slug="turntable",
                category="valuables",
                name="Turntable",
                frontmatter={"brand": "Technics", "estimated_value": 1000},
            )
        ],
    }

    header, rows = full_catalog_table(catalog)

    assert header == [
        "category",
        "name",
        "slug",
        "brand",
        "model",
        "serial_number",
        "estimated_value",
        "location",
    ]
    assert ["kitchen", "Pan", "pan", "", "", "", "", ""] in rows
    assert ["valuables", "Turntable", "turntable", "Technics", "", "", "1000", ""] in rows


def test_catalog_table_returns_header_and_rows():
    from content_loader import Item
    from export_csv import catalog_table

    items = [
        Item(
            slug="pan",
            category="kitchen",
            name="Pan",
            frontmatter={"brand": "Lodge"},
        )
    ]

    header, rows = catalog_table(items)

    assert header == ["name", "slug", "brand", "model", "serial_number", "location"]
    assert rows == [["Pan", "pan", "Lodge", "", "", ""]]
