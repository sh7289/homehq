import sqlite3
from datetime import datetime, timedelta, timezone


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
    if "section" not in existing_columns:
        # Nullable on purpose: existing backfilled rows stay untouched and
        # render under "Other" until they're sorted.
        conn.execute("ALTER TABLE pantry_items ADD COLUMN section TEXT")

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

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_staging_items (
            id INTEGER PRIMARY KEY,
            target_type TEXT NOT NULL,
            name TEXT NOT NULL,
            quantity REAL,
            unit TEXT,
            storage TEXT,
            location TEXT,
            category TEXT,
            brand TEXT,
            model TEXT,
            serial_number TEXT,
            notes TEXT,
            estimated_value TEXT,
            source_image_path TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT
        )
        """
    )
    staging_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(import_staging_items)")
    }
    if "estimated_value" not in staging_columns:
        conn.execute("ALTER TABLE import_staging_items ADD COLUMN estimated_value TEXT")
    if "section" not in staging_columns:
        conn.execute("ALTER TABLE import_staging_items ADD COLUMN section TEXT")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS login_attempts (
            identifier TEXT PRIMARY KEY,
            failures INTEGER NOT NULL DEFAULT 0,
            locked_until TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()


# Login throttling. Five wrong tries is well clear of ordinary fat-fingering
# for a two-person household, and the backoff doubles from there so a script
# gets slower far faster than a person does.
LOCKOUT_THRESHOLD = 5
LOCKOUT_BASE_SECONDS = 60
LOCKOUT_MAX_SECONDS = 15 * 60


def _now():
    return datetime.now(timezone.utc).isoformat()


def record_login_failure(conn, identifier, now=None):
    """Count one failed attempt against an identifier and extend its lockout."""
    now = now or datetime.now(timezone.utc)
    with conn:
        row = conn.execute(
            "SELECT failures FROM login_attempts WHERE identifier = ?", (identifier,)
        ).fetchone()
        failures = (row["failures"] if row else 0) + 1

        over = failures - LOCKOUT_THRESHOLD
        if over >= 0:
            seconds = min(LOCKOUT_BASE_SECONDS * (2**over), LOCKOUT_MAX_SECONDS)
            locked_until = (now + timedelta(seconds=seconds)).isoformat()
        else:
            locked_until = None

        conn.execute(
            "INSERT INTO login_attempts (identifier, failures, locked_until, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(identifier) DO UPDATE SET "
            "failures = excluded.failures, locked_until = excluded.locked_until, "
            "updated_at = excluded.updated_at",
            (identifier, failures, locked_until, now.isoformat()),
        )


def lockout_remaining(conn, identifier, now=None):
    """Seconds until this identifier may try again; 0 if it may try now."""
    now = now or datetime.now(timezone.utc)
    row = conn.execute(
        "SELECT locked_until FROM login_attempts WHERE identifier = ?", (identifier,)
    ).fetchone()
    if row is None or not row["locked_until"]:
        return 0
    remaining = (datetime.fromisoformat(row["locked_until"]) - now).total_seconds()
    return int(remaining) if remaining > 0 else 0


def clear_login_failures(conn, identifier):
    with conn:
        conn.execute("DELETE FROM login_attempts WHERE identifier = ?", (identifier,))


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
    section=None,
):
    with conn:
        cursor = conn.execute(
            "INSERT INTO pantry_items "
            "(name, quantity, unit, location, updated_at, storage, expiry_date, "
            "acquired_date, shelf_life_days, section) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                section,
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
    conn,
    item_id,
    quantity,
    unit,
    location,
    expiry_date,
    acquired_date,
    shelf_life_days,
    section=None,
):
    with conn:
        conn.execute(
            "UPDATE pantry_items SET quantity = ?, unit = ?, location = ?, expiry_date = ?, "
            "acquired_date = ?, shelf_life_days = ?, section = ?, updated_at = ? WHERE id = ?",
            (
                quantity,
                unit,
                location,
                expiry_date,
                acquired_date,
                shelf_life_days,
                section,
                _now(),
                item_id,
            ),
        )


def set_section(conn, item_id, section):
    """Set just the section, for the bulk-assignment screen."""
    with conn:
        conn.execute(
            "UPDATE pantry_items SET section = ?, updated_at = ? WHERE id = ?",
            (section, _now(), item_id),
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


_STAGING_FIELDS = (
    "target_type",
    "name",
    "quantity",
    "unit",
    "storage",
    "location",
    "category",
    "brand",
    "model",
    "serial_number",
    "notes",
    "estimated_value",
    "source_image_path",
    "section",
)


def add_staging_item(conn, target_type, name, **fields):
    values = {field: fields.get(field) for field in _STAGING_FIELDS}
    values["target_type"] = target_type
    values["name"] = name
    columns = list(values.keys()) + ["status", "created_at"]
    placeholders = ", ".join("?" for _ in columns)
    with conn:
        cursor = conn.execute(
            f"INSERT INTO import_staging_items ({', '.join(columns)}) VALUES ({placeholders})",
            [*values.values(), "pending", _now()],
        )
        return cursor.lastrowid


def list_staging_items(conn, status=None):
    if status is None:
        rows = conn.execute(
            "SELECT * FROM import_staging_items ORDER BY created_at"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM import_staging_items WHERE status = ? ORDER BY created_at",
            (status,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_staging_item(conn, item_id):
    row = conn.execute(
        "SELECT * FROM import_staging_items WHERE id = ?", (item_id,)
    ).fetchone()
    return dict(row) if row else None


def update_staging_item(conn, item_id, **fields):
    updates = {k: v for k, v in fields.items() if k in _STAGING_FIELDS}
    if not updates:
        return
    set_clause = ", ".join(f"{col} = ?" for col in updates)
    with conn:
        conn.execute(
            f"UPDATE import_staging_items SET {set_clause} WHERE id = ?",
            [*updates.values(), item_id],
        )


def set_staging_item_status(conn, item_id, status):
    with conn:
        conn.execute(
            "UPDATE import_staging_items SET status = ? WHERE id = ?", (status, item_id)
        )


def delete_staging_item(conn, item_id):
    with conn:
        conn.execute("DELETE FROM import_staging_items WHERE id = ?", (item_id,))
