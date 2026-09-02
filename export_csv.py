import csv
import io

_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _escape_cell(value):
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _table_to_csv(header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def pantry_table(items):
    header = ["name", "quantity", "unit", "location", "expiry_date", "updated_at"]
    rows = [
        [
            _escape_cell(item["name"]),
            _escape_cell(item["quantity"]),
            _escape_cell(item["unit"]),
            _escape_cell(item["location"]),
            _escape_cell(item.get("expiry_date")),
            _escape_cell(item["updated_at"]),
        ]
        for item in items
    ]
    return header, rows


def catalog_table(items):
    header = ["name", "slug", "brand", "model", "serial_number", "location"]
    rows = [
        [
            _escape_cell(item.name),
            _escape_cell(item.slug),
            _escape_cell(item.frontmatter.get("brand", "")),
            _escape_cell(item.frontmatter.get("model", "")),
            _escape_cell(item.frontmatter.get("serial_number", "")),
            _escape_cell(item.frontmatter.get("location", "")),
        ]
        for item in items
    ]
    return header, rows


def full_catalog_table(catalog):
    """catalog: dict[category] -> list[Item]. Flattens every category into
    one table, used for the insurance/report CSV export."""
    header = [
        "category",
        "name",
        "slug",
        "brand",
        "model",
        "serial_number",
        "estimated_value",
        "location",
    ]
    rows = []
    for category in sorted(catalog.keys()):
        for item in catalog[category]:
            rows.append(
                [
                    _escape_cell(category),
                    _escape_cell(item.name),
                    _escape_cell(item.slug),
                    _escape_cell(item.frontmatter.get("brand", "")),
                    _escape_cell(item.frontmatter.get("model", "")),
                    _escape_cell(item.frontmatter.get("serial_number", "")),
                    _escape_cell(item.frontmatter.get("estimated_value", "")),
                    _escape_cell(item.frontmatter.get("location", "")),
                ]
            )
    return header, rows


def pantry_csv(items):
    return _table_to_csv(*pantry_table(items))


def catalog_csv(items):
    return _table_to_csv(*catalog_table(items))


def full_catalog_csv(catalog):
    return _table_to_csv(*full_catalog_table(catalog))
