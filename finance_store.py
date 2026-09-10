"""Private SQLite storage for normalized finance balances.

``dashboard`` returns the exact Task 3 data contract::

    {
        "accounts": [{
            "id": str,
            "label": str,
            "currency": str,
            "balance": Decimal,
            "balance_at": str,   # provider time, UTC ISO-8601 with Z
            "observed_at": str,  # local sync time, UTC ISO-8601 with Z
            "missing": bool,
            "stale": bool,       # provider time is more than 48 hours old
            "nonfinancial": bool,
        }],
        "totals": {
            "by_currency": {str: Decimal},  # excludes missing/nonfinancial
            "complete": bool,
        },
        "history": [{
            "date": "YYYY-MM-DD",           # UTC observation date
            "by_currency": {str: Decimal},
            "complete": bool,
        }],
        "last_attempt": {"at": str, "status": str} | None,
        "last_success": str | None,          # last complete sync only
        "warnings": [str],                   # fixed safe category names
    }

Only normalized current accounts, daily balance totals, and safe sync status
are persisted. There is no transaction storage.
"""

import json
import os
import re
import sqlite3
import stat
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path


STALE_AFTER = timedelta(hours=48)
# Manual balances (home value, vehicle value, etc.) are refreshed by hand, not
# by a daily sync — a much longer window avoids flagging them stale within days.
MANUAL_STALE_AFTER = timedelta(days=180)
MAX_ABS_BALANCE = Decimal("1e24")
SAFE_WARNINGS = frozenset(
    {
        "connection_authentication",
        "connection_unavailable",
        "account_unavailable",
        "account_incomplete",
        "provider_authentication",
        "provider_warning",
    }
)


class FinanceStoreError(ValueError):
    """Safe validation or storage configuration error."""


def _utc(value=None):
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise FinanceStoreError("Finance time must include a timezone.")
    return value.astimezone(timezone.utc)


def _iso(value):
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_iso(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise FinanceStoreError("Finance payload is invalid.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise FinanceStoreError("Finance payload is invalid.")
    return parsed.astimezone(timezone.utc)


def _inside(path, directory):
    try:
        return os.path.commonpath((path, directory)) == directory
    except ValueError:
        return False


def _validate_path(path, repo_dir):
    if not isinstance(path, str) or not os.path.isabs(path):
        raise FinanceStoreError("Finance database path must be absolute.")
    normalized = os.path.abspath(path)
    if os.path.realpath(normalized) != normalized:
        raise FinanceStoreError("Finance database path may not use symbolic links.")
    repository = os.path.realpath(repo_dir or os.path.dirname(__file__))
    if _inside(normalized, repository) or any((parent / ".git").exists() for parent in Path(normalized).parents):
        raise FinanceStoreError("Finance database must be outside the application repository.")
    return normalized


def _prepare_file(path):
    parent = os.path.dirname(path)
    if not os.path.exists(parent):
        grandparent = os.path.dirname(parent)
        if not os.path.isdir(grandparent):
            raise FinanceStoreError("Finance database parent directory is invalid.")
        os.mkdir(parent, 0o700)
    parent_info = os.lstat(parent)
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise FinanceStoreError("Finance database parent directory is invalid.")
    if parent_info.st_mode & 0o077:
        raise FinanceStoreError("Finance database parent directory is not private.")
    if os.path.lexists(path):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise FinanceStoreError("Finance database path is invalid.")
    else:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        os.close(descriptor)
    os.chmod(path, 0o600)


def connect(path, repo_dir=None):
    """Open or initialize a private finance database with ``sqlite3.Row`` rows."""
    path = _validate_path(path, repo_dir)
    _prepare_file(path)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS finance_accounts (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            currency TEXT NOT NULL,
            balance TEXT NOT NULL,
            balance_at TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            missing INTEGER NOT NULL CHECK (missing IN (0, 1))
        );
        CREATE TABLE IF NOT EXISTS finance_snapshot_days (
            day TEXT PRIMARY KEY,
            complete INTEGER NOT NULL CHECK (complete IN (0, 1))
        );
        CREATE TABLE IF NOT EXISTS finance_snapshot_totals (
            day TEXT NOT NULL REFERENCES finance_snapshot_days(day) ON DELETE CASCADE,
            currency TEXT NOT NULL,
            balance TEXT NOT NULL,
            PRIMARY KEY (day, currency)
        );
        CREATE TABLE IF NOT EXISTS finance_sync_status (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            last_attempt_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('success', 'partial', 'failure')),
            last_success_at TEXT,
            warnings TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS finance_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    connection.commit()
    import finance_book
    finance_book.initialize(connection)
    return connection


def _validate_account(account):
    expected = {"id", "label", "currency", "balance", "balance_at"}
    if not isinstance(account, dict) or not expected <= set(account) or set(account) - expected - {"provider_name", "institution"}:
        raise FinanceStoreError("Finance payload is invalid.")
    if not re.fullmatch(r"[0-9a-f]{64}", account["id"] or ""):
        raise FinanceStoreError("Finance payload is invalid.")
    label = account["label"]
    if (
        not isinstance(label, str)
        or not label.strip()
        or len(label) > 80
        or any(ord(character) < 32 or ord(character) == 127 for character in label)
    ):
        raise FinanceStoreError("Finance payload is invalid.")
    for key in ("provider_name", "institution"):
        value = account.get(key, "")
        if not isinstance(value, str) or len(value) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in value) or re.search(r"\d{4,}", value):
            raise FinanceStoreError("Finance payload is invalid.")
    currency = account["currency"]
    if currency != "NONFINANCIAL" and not re.fullmatch(r"[A-Z]{3}", currency or ""):
        raise FinanceStoreError("Finance payload is invalid.")
    balance = account["balance"]
    if not isinstance(balance, Decimal) or not balance.is_finite() or balance.copy_abs() > MAX_ABS_BALANCE:
        raise FinanceStoreError("Finance payload is invalid.")
    if balance.as_tuple().exponent < -18 or len(balance.as_tuple().digits) > 42:
        raise FinanceStoreError("Finance payload is invalid.")
    _parse_iso(account["balance_at"])
    return account


def _validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) != {"accounts", "warnings", "complete"}:
        raise FinanceStoreError("Finance payload is invalid.")
    if not isinstance(payload["accounts"], list) or not isinstance(payload["complete"], bool):
        raise FinanceStoreError("Finance payload is invalid.")
    if not isinstance(payload["warnings"], list) or any(
        warning not in SAFE_WARNINGS for warning in payload["warnings"]
    ):
        raise FinanceStoreError("Finance payload is invalid.")
    accounts = [_validate_account(account) for account in payload["accounts"]]
    ids = [account["id"] for account in accounts]
    if len(ids) != len(set(ids)):
        raise FinanceStoreError("Finance payload is invalid.")
    if payload["complete"] != (not payload["warnings"]):
        raise FinanceStoreError("Finance payload is invalid.")
    return accounts


def _stored_balance(value):
    """Reject malformed/nonfinite persisted balances without leaking their text."""
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise FinanceStoreError("Finance database contains invalid balance data.") from None
    if not number.is_finite():
        raise FinanceStoreError("Finance database contains invalid balance data.")
    return number


def _current_totals(conn):
    totals = {}
    rows = conn.execute(
        "SELECT currency, balance FROM finance_accounts "
        "WHERE source = 'provider' AND missing = 0 AND currency != 'NONFINANCIAL' ORDER BY currency"
    )
    for row in rows:
        value = _stored_balance(row["balance"])
        with localcontext() as context:
            # The provider admits up to 40 significant digits and 18 decimals.
            # 64 digits also covers aggregate growth within the bounded payload.
            context.prec = 64
            totals[row["currency"]] = totals.get(row["currency"], Decimal("0")) + value
    return totals


def record_sync(conn, payload, now=None):
    """Transactionally ingest one validated normalized provider result."""
    accounts = _validate_payload(payload)
    observed = _utc(now)
    observed_text = _iso(observed)
    status = "success" if payload["complete"] else "partial"
    from finance_book import _transaction
    with _transaction(conn):
        previous_attempt = conn.execute(
            "SELECT last_attempt_at FROM finance_sync_status WHERE singleton = 1"
        ).fetchone()
        if previous_attempt and observed < _parse_iso(previous_attempt["last_attempt_at"]):
            raise FinanceStoreError("Finance observations may not be backdated.")
        conn.execute("UPDATE finance_accounts SET missing = 1 WHERE source = 'provider'")
        for account in accounts:
            existing = conn.execute(
                "SELECT balance_at FROM finance_accounts WHERE id = ?", (account["id"],)
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO finance_accounts "
                    "(id, label, currency, balance, balance_at, observed_at, missing) "
                    "VALUES (?, ?, ?, ?, ?, ?, 0)",
                    (
                        account["id"],
                        account["label"],
                        account["currency"],
                        str(account["balance"]),
                        account["balance_at"],
                        observed_text,
                    ),
                )
            elif _parse_iso(account["balance_at"]) >= _parse_iso(existing["balance_at"]):
                conn.execute(
                    "UPDATE finance_accounts SET label = ?, currency = ?, balance = ?, "
                    "balance_at = ?, observed_at = ?, missing = 0 WHERE id = ?",
                    (
                        account["label"],
                        account["currency"],
                        str(account["balance"]),
                        account["balance_at"],
                        observed_text,
                        account["id"],
                    ),
                )
            else:
                conn.execute(
                    "UPDATE finance_accounts SET observed_at = ?, missing = 0 WHERE id = ?",
                    (observed_text, account["id"]),
                )

            conn.execute(
                "UPDATE finance_accounts SET provider_name=?,institution=? WHERE id=?",
                (account.get("provider_name", ""), account.get("institution", ""), account["id"]),
            )
            # Import pre-existing alias configuration only until locally customized.
            if existing is None and not re.fullmatch(r"Account [0-9A-F]{8}", account["label"]):
                conn.execute("UPDATE finance_accounts SET nickname=? WHERE id=? AND nickname=''", (account["label"],account["id"]))

        missing = conn.execute("SELECT 1 FROM finance_accounts WHERE missing = 1 LIMIT 1").fetchone()
        complete = payload["complete"] and not missing
        status = "success" if complete else "partial"
        warnings = list(payload["warnings"])
        if missing and "account_unavailable" not in warnings:
            warnings.append("account_unavailable")
        previous = conn.execute(
            "SELECT last_success_at FROM finance_sync_status WHERE singleton = 1"
        ).fetchone()
        last_success = observed_text if complete else (
            previous["last_success_at"] if previous else None
        )
        conn.execute(
            "INSERT INTO finance_sync_status "
            "(singleton, last_attempt_at, status, last_success_at, warnings) "
            "VALUES (1, ?, ?, ?, ?) ON CONFLICT(singleton) DO UPDATE SET "
            "last_attempt_at = excluded.last_attempt_at, status = excluded.status, "
            "last_success_at = excluded.last_success_at, warnings = excluded.warnings",
            (observed_text, status, last_success, json.dumps(warnings)),
        )

        day = observed.date().isoformat()
        conn.execute(
            "INSERT INTO finance_snapshot_days (day, complete) VALUES (?, ?) "
            "ON CONFLICT(day) DO UPDATE SET complete = excluded.complete",
            (day, int(complete)),
        )
        conn.execute("DELETE FROM finance_snapshot_totals WHERE day = ?", (day,))
        for currency, balance in _current_totals(conn).items():
            conn.execute(
                "INSERT INTO finance_snapshot_totals (day, currency, balance) VALUES (?, ?, ?)",
                (day, currency, str(balance)),
            )


def record_failure(conn, now=None):
    """Record a safe generic failed attempt without touching balances/history."""
    attempt = _iso(_utc(now))
    from finance_book import _transaction
    with _transaction(conn):
        previous = conn.execute(
            "SELECT last_attempt_at, last_success_at FROM finance_sync_status WHERE singleton = 1"
        ).fetchone()
        if previous and _parse_iso(attempt) < _parse_iso(previous["last_attempt_at"]):
            raise FinanceStoreError("Finance observations may not be backdated.")
        conn.execute(
            "INSERT INTO finance_sync_status "
            "(singleton, last_attempt_at, status, last_success_at, warnings) "
            "VALUES (1, ?, 'failure', ?, '[\"sync_failed\"]') "
            "ON CONFLICT(singleton) DO UPDATE SET "
            "last_attempt_at = excluded.last_attempt_at, status = 'failure', "
            "last_success_at = excluded.last_success_at, warnings = excluded.warnings",
            (attempt, previous["last_success_at"] if previous else None),
        )


def set_store_mode(conn, mode):
    """Mark the database as ``demo`` or ``live`` for CLI overwrite protection."""
    if mode not in {"demo", "live"}:
        raise FinanceStoreError("Finance store mode is invalid.")
    with conn:
        conn.execute(
            "INSERT INTO finance_metadata (key, value) VALUES ('mode', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (mode,),
        )


def get_store_mode(conn):
    row = conn.execute("SELECT value FROM finance_metadata WHERE key = 'mode'").fetchone()
    return row["value"] if row else None


def dashboard(conn, now=None):
    """Return the documented immutable-value dashboard contract."""
    current = _utc(now)
    status_row = conn.execute(
        "SELECT last_attempt_at, status, last_success_at, warnings "
        "FROM finance_sync_status WHERE singleton = 1"
    ).fetchone()
    accounts = []
    for row in conn.execute(
        "SELECT id, label, currency, balance, balance_at, observed_at, missing "
        "FROM finance_accounts WHERE source = 'provider' ORDER BY label, id"
    ):
        balance_at = _parse_iso(row["balance_at"])
        accounts.append(
            {
                "id": row["id"],
                "label": row["label"],
                "currency": row["currency"],
                "balance": _stored_balance(row["balance"]),
                "balance_at": row["balance_at"],
                "observed_at": row["observed_at"],
                "missing": bool(row["missing"]),
                "stale": current - balance_at > STALE_AFTER,
                "nonfinancial": row["currency"] == "NONFINANCIAL",
            }
        )

    history = []
    for day_row in conn.execute(
        "SELECT day, complete FROM finance_snapshot_days ORDER BY day"
    ):
        values = {
            row["currency"]: _stored_balance(row["balance"])
            for row in conn.execute(
                "SELECT currency, balance FROM finance_snapshot_totals "
                "WHERE day = ? ORDER BY currency",
                (day_row["day"],),
            )
        }
        history.append(
            {
                "date": day_row["day"],
                "by_currency": values,
                "complete": bool(day_row["complete"]),
            }
        )

    latest_complete = bool(status_row and status_row["status"] == "success")
    return {
        "accounts": accounts,
        "totals": {"by_currency": _current_totals(conn), "complete": latest_complete},
        "history": history,
        "last_attempt": (
            {"at": status_row["last_attempt_at"], "status": status_row["status"]}
            if status_row
            else None
        ),
        "last_success": status_row["last_success_at"] if status_row else None,
        "warnings": json.loads(status_row["warnings"]) if status_row else [],
    }
