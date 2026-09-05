import sqlite3

import db
import sections


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    return conn


def test_pantry_sections_cover_the_agreed_sub_areas():
    keys = [key for key, _label in sections.sections_for("pantry")]
    assert keys == ["bulk-dry", "canned", "sauces-oils", "baking", "spices", "other"]


def test_freezer_has_its_own_section_list():
    pantry_keys = {key for key, _ in sections.sections_for("pantry")}
    freezer_keys = {key for key, _ in sections.sections_for("freezer")}
    assert freezer_keys != pantry_keys
    assert "meat" in freezer_keys
    assert "canned" not in freezer_keys


def test_normalize_accepts_a_valid_key():
    assert sections.normalize("pantry", "spices") == "spices"


def test_normalize_falls_back_to_other_for_unknown_key():
    assert sections.normalize("pantry", "nonsense") == "other"


def test_normalize_falls_back_to_other_for_none_or_blank():
    assert sections.normalize("pantry", None) == "other"
    assert sections.normalize("pantry", "") == "other"


def test_normalize_rejects_a_pantry_key_used_on_the_freezer():
    assert sections.normalize("freezer", "canned") == "other"


def test_group_items_follows_declared_section_order():
    items = [
        {"name": "cumin", "section": "spices"},
        {"name": "black beans", "section": "canned"},
        {"name": "pasta", "section": "bulk-dry"},
    ]

    groups = sections.group_items(items, "pantry")

    assert [g["key"] for g in groups] == ["bulk-dry", "canned", "spices"]
    assert groups[0]["label"] == "Bulk & Dry Goods"
    assert [i["name"] for i in groups[1]["rows"]] == ["black beans"]


def test_group_items_omits_empty_sections():
    groups = sections.group_items([{"name": "cumin", "section": "spices"}], "pantry")

    assert [g["key"] for g in groups] == ["spices"]


def test_group_items_puts_unsectioned_items_in_other():
    items = [{"name": "mystery jar", "section": None}, {"name": "rice", "section": "bulk-dry"}]

    groups = sections.group_items(items, "pantry")

    assert [g["key"] for g in groups] == ["bulk-dry", "other"]
    assert [i["name"] for i in groups[1]["rows"]] == ["mystery jar"]


# --- database layer -------------------------------------------------------


def test_section_column_is_added_to_a_pre_existing_database():
    """The live DB has 106 backfilled rows and no section column."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE pantry_items (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
        "quantity REAL NOT NULL, unit TEXT, location TEXT, updated_at TEXT)"
    )
    conn.execute("INSERT INTO pantry_items (name, quantity) VALUES ('old rice', 2)")
    conn.commit()

    db.init_db(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(pantry_items)")}
    assert "section" in columns
    row = conn.execute("SELECT name, section FROM pantry_items").fetchone()
    assert row["name"] == "old rice"
    assert row["section"] is None


def test_staging_table_has_a_section_column():
    columns = {
        row["name"] for row in _conn().execute("PRAGMA table_info(import_staging_items)")
    }
    assert "section" in columns


def test_add_item_persists_section():
    conn = _conn()

    db.add_item(
        conn, name="chickpeas", quantity=2, unit="can", location="", section="canned"
    )

    assert db.list_items(conn)[0]["section"] == "canned"


def test_add_item_without_section_stores_null():
    conn = _conn()

    db.add_item(conn, name="mystery", quantity=1, unit="", location="")

    assert db.list_items(conn)[0]["section"] is None


def test_update_item_changes_section():
    conn = _conn()
    item_id = db.add_item(
        conn, name="cumin", quantity=1, unit="jar", location="", section="canned"
    )

    db.update_item(
        conn,
        item_id,
        quantity=1,
        unit="jar",
        location="",
        expiry_date=None,
        acquired_date=None,
        shelf_life_days=None,
        section="spices",
    )

    assert db.list_items(conn)[0]["section"] == "spices"


def test_set_section_updates_only_the_named_item():
    conn = _conn()
    first = db.add_item(conn, name="rice", quantity=1, unit="bag", location="")
    second = db.add_item(conn, name="oregano", quantity=1, unit="jar", location="")

    db.set_section(conn, first, "bulk-dry")

    by_id = {item["id"]: item for item in db.list_items(conn)}
    assert by_id[first]["section"] == "bulk-dry"
    assert by_id[second]["section"] is None
