import sqlite3
from datetime import datetime, timezone


def get_connection(db_path):
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pantry_items (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT,
            location TEXT,
            updated_at TEXT
        )
        """
    )
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(pantry_items)")}
    if "expiry_date" not in existing_columns:
        conn.execute("ALTER TABLE pantry_items ADD COLUMN expiry_date TEXT")
    if "storage" not in existing_columns:
        conn.execute("ALTER TABLE pantry_items ADD COLUMN storage TEXT NOT NULL DEFAULT 'pantry'")
    if "acquired_date" not in existing_columns:
        conn.execute("ALTER TABLE pantry_items ADD COLUMN acquired_date TEXT")
    if "shelf_life_days" not in existing_columns:
        conn.execute("ALTER TABLE pantry_items ADD COLUMN shelf_life_days INTEGER")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shopping_list_items (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            storage TEXT NOT NULL DEFAULT 'pantry',
            quantity_to_buy REAL,
            created_at TEXT
        )
        """
    )
    conn.commit()


def _now():
    return datetime.now(timezone.utc).isoformat()


def add_item(
    conn,
    name,
    quantity,
    unit,
    location,
    storage="pantry",
    expiry_date=None,
    acquired_date=None,
    shelf_life_days=None,
):
    with conn:
        cursor = conn.execute(
            "INSERT INTO pantry_items "
            "(name, quantity, unit, location, updated_at, storage, expiry_date, "
            "acquired_date, shelf_life_days) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                quantity,
                unit,
                location,
                _now(),
                storage,
                expiry_date,
                acquired_date,
                shelf_life_days,
            ),
        )
        return cursor.lastrowid


def list_items(conn, storage=None):
    if storage is None:
        rows = conn.execute("SELECT * FROM pantry_items ORDER BY name").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM pantry_items WHERE storage = ? ORDER BY name", (storage,)
        ).fetchall()
    return [dict(row) for row in rows]


def adjust_quantity(conn, item_id, delta):
    with conn:
        conn.execute(
            "UPDATE pantry_items SET quantity = MAX(0, quantity + ?), updated_at = ? "
            "WHERE id = ?",
            (delta, _now(), item_id),
        )


def update_item(
    conn, item_id, quantity, unit, location, expiry_date, acquired_date, shelf_life_days
):
    with conn:
        conn.execute(
            "UPDATE pantry_items SET quantity = ?, unit = ?, location = ?, expiry_date = ?, "
            "acquired_date = ?, shelf_life_days = ?, updated_at = ? WHERE id = ?",
            (
                quantity,
                unit,
                location,
                expiry_date,
                acquired_date,
                shelf_life_days,
                _now(),
                item_id,
            ),
        )


def delete_item(conn, item_id):
    with conn:
        conn.execute("DELETE FROM pantry_items WHERE id = ?", (item_id,))


def add_shopping_list_item(conn, name, storage="pantry", quantity_to_buy=None):
    with conn:
        cursor = conn.execute(
            "INSERT INTO shopping_list_items (name, storage, quantity_to_buy, created_at) "
            "VALUES (?, ?, ?, ?)",
            (name, storage, quantity_to_buy, _now()),
        )
        return cursor.lastrowid


def list_shopping_list_items(conn):
    rows = conn.execute("SELECT * FROM shopping_list_items ORDER BY created_at").fetchall()
    return [dict(row) for row in rows]


def get_shopping_list_item(conn, item_id):
    row = conn.execute(
        "SELECT * FROM shopping_list_items WHERE id = ?", (item_id,)
    ).fetchone()
    return dict(row) if row else None


def delete_shopping_list_item(conn, item_id):
    with conn:
        conn.execute("DELETE FROM shopping_list_items WHERE id = ?", (item_id,))
