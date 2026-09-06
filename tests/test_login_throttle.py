import sqlite3
from datetime import datetime, timedelta, timezone

import db

THRESHOLD = db.LOCKOUT_THRESHOLD


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    return conn


def _now():
    return datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def test_a_fresh_identifier_is_not_locked():
    assert db.lockout_remaining(_conn(), "user:steve", now=_now()) == 0


def test_failures_below_the_threshold_do_not_lock():
    conn = _conn()
    for _ in range(THRESHOLD - 1):
        db.record_login_failure(conn, "user:steve", now=_now())

    assert db.lockout_remaining(conn, "user:steve", now=_now()) == 0


def test_hitting_the_threshold_locks_out():
    conn = _conn()
    for _ in range(THRESHOLD):
        db.record_login_failure(conn, "user:steve", now=_now())

    assert db.lockout_remaining(conn, "user:steve", now=_now()) > 0


def test_the_lockout_expires():
    conn = _conn()
    for _ in range(THRESHOLD):
        db.record_login_failure(conn, "user:steve", now=_now())

    later = _now() + timedelta(hours=2)
    assert db.lockout_remaining(conn, "user:steve", now=later) == 0


def test_backoff_grows_with_repeated_failures():
    conn = _conn()
    for _ in range(THRESHOLD):
        db.record_login_failure(conn, "user:steve", now=_now())
    first = db.lockout_remaining(conn, "user:steve", now=_now())

    db.record_login_failure(conn, "user:steve", now=_now())
    second = db.lockout_remaining(conn, "user:steve", now=_now())

    assert second > first


def test_backoff_is_capped():
    conn = _conn()
    for _ in range(40):
        db.record_login_failure(conn, "user:steve", now=_now())

    assert db.lockout_remaining(conn, "user:steve", now=_now()) <= db.LOCKOUT_MAX_SECONDS


def test_success_clears_the_record():
    conn = _conn()
    for _ in range(THRESHOLD):
        db.record_login_failure(conn, "user:steve", now=_now())

    db.clear_login_failures(conn, "user:steve")

    assert db.lockout_remaining(conn, "user:steve", now=_now()) == 0


def test_lockouts_are_tracked_per_identifier():
    """Locking one account must not lock the other household user out."""
    conn = _conn()
    for _ in range(THRESHOLD):
        db.record_login_failure(conn, "user:steve", now=_now())

    assert db.lockout_remaining(conn, "user:steve", now=_now()) > 0
    assert db.lockout_remaining(conn, "user:wife", now=_now()) == 0
