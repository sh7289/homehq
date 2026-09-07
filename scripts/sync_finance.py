"""Synchronize SimpleFIN balances into the private Home HQ finance store."""

import argparse
import fcntl
import os
import sqlite3
import stat
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import finance_store
import simplefin


DEMO_PAYLOAD = {
    "accounts": [
        {
            "id": "1" * 64,
            "label": "Demo checking",
            "currency": "USD",
            "balance": Decimal("1234.56"),
            "balance_at": "2026-09-07T12:00:00Z",
        },
        {
            "id": "2" * 64,
            "label": "Demo savings",
            "currency": "USD",
            "balance": Decimal("2050.00"),
            "balance_at": "2026-09-07T12:00:00Z",
        },
    ],
    "warnings": [],
    "complete": True,
}
DEMO_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def _message(text, stream):
    print(text, file=stream)


def _lock(path):
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path + ".lock", flags, 0o600)
    os.fchmod(descriptor, 0o600)
    handle = os.fdopen(descriptor, "r+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        handle.close()
        raise BlockingIOError
    return handle


def main(argv=None, *, environ=None, fetch=None, stdout=None, stderr=None):
    parser = argparse.ArgumentParser(description="Synchronize private finance balances.")
    parser.add_argument("--demo", action="store_true", help="Load deterministic demo balances")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    db_path = env.get("HOMEHQ_FINANCE_DB_PATH", "")
    credential = env.get("HOMEHQ_SIMPLEFIN_ACCESS_URL", "")
    if not db_path or (not args.demo and not credential):
        _message("Finance sync configuration is incomplete.", stderr)
        return 2

    connection = None
    lock = None
    try:
        connection = finance_store.connect(db_path, repo_dir=str(ROOT))
        try:
            lock = _lock(db_path)
        except BlockingIOError:
            _message("Finance sync is already running.", stderr)
            return 2

        mode = finance_store.get_store_mode(connection)
        if args.demo and mode == "live":
            _message("Demo sync refused because this is a live finance store.", stderr)
            return 2

        if not args.demo and mode == "demo":
            _message("Live sync refused because this is a demo finance store.", stderr)
            return 2

        if args.demo:
            payload = DEMO_PAYLOAD
            finance_store.set_store_mode(connection, "demo")
        else:
            finance_store.set_store_mode(connection, "live")
            payload = (fetch or simplefin.fetch_balances)(credential)
        finance_store.record_sync(connection, payload, now=DEMO_NOW if args.demo else None)
    except (finance_store.FinanceStoreError, OSError, sqlite3.Error):
        _message("Finance sync configuration is invalid.", stderr)
        return 2
    except simplefin.SimpleFINError:
        if connection is not None:
            try:
                finance_store.record_failure(connection)
            except (finance_store.FinanceStoreError, sqlite3.Error):
                pass
        _message("Finance sync failed safely.", stderr)
        return 1
    finally:
        if lock is not None:
            lock.close()
        if connection is not None:
            connection.close()

    if payload["complete"]:
        _message("Finance sync completed.", stdout)
    else:
        _message("Finance sync completed with provider warnings.", stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
