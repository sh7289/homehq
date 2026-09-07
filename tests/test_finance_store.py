import os
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import finance_store


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def normalized(accounts=None, warnings=None, complete=True):
    return {
        "accounts": accounts
        if accounts is not None
        else [
            {
                "id": "a" * 64,
                "label": "Account A1B2C3D4",
                "currency": "USD",
                "balance": Decimal("100.10"),
                "balance_at": "2026-09-07T10:00:00Z",
            }
        ],
        "warnings": [] if warnings is None else warnings,
        "complete": complete,
    }


def open_store(tmp_path):
    return finance_store.connect(str(tmp_path / "private" / "finance.db"))


def test_connect_creates_private_store_with_row_factory_and_no_transaction_table(tmp_path):
    path = tmp_path / "private" / "finance.db"

    conn = finance_store.connect(str(path))

    assert conn.row_factory is sqlite3.Row
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    assert os.stat(path).st_mode & 0o777 == 0o600
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert not any("transaction" in name for name in tables)


def test_connect_rejects_relative_symlink_and_repo_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "target.db"
    symlink = outside / "link.db"
    symlink.symlink_to(target)

    with pytest.raises(finance_store.FinanceStoreError):
        finance_store.connect("relative.db")
    with pytest.raises(finance_store.FinanceStoreError):
        finance_store.connect(str(symlink))
    with pytest.raises(finance_store.FinanceStoreError):
        finance_store.connect(str(repo / "finance.db"), repo_dir=str(repo))


def test_dashboard_totals_exact_decimals_by_currency_and_excludes_nonfinancial(tmp_path):
    conn = open_store(tmp_path)
    accounts = [
        normalized()["accounts"][0],
        {
            "id": "b" * 64,
            "label": "Account B1B2C3D4",
            "currency": "USD",
            "balance": Decimal("-20.05"),
            "balance_at": "2026-09-07T10:00:00Z",
        },
        {
            "id": "c" * 64,
            "label": "Account C1B2C3D4",
            "currency": "EUR",
            "balance": Decimal("7.00"),
            "balance_at": "2026-09-07T10:00:00Z",
        },
        {
            "id": "d" * 64,
            "label": "Account D1B2C3D4",
            "currency": "NONFINANCIAL",
            "balance": Decimal("5000"),
            "balance_at": "2026-09-07T10:00:00Z",
        },
    ]

    finance_store.record_sync(conn, normalized(accounts=accounts), now=NOW)
    result = finance_store.dashboard(conn, now=NOW)

    assert result["totals"] == {
        "by_currency": {"EUR": Decimal("7.00"), "USD": Decimal("80.05")},
        "complete": True,
    }
    assert all(isinstance(a["balance"], Decimal) for a in result["accounts"])
    assert result["history"] == [
        {
            "date": "2026-09-07",
            "by_currency": {"EUR": Decimal("7.00"), "USD": Decimal("80.05")},
            "complete": True,
        }
    ]


def test_omitted_accounts_remain_visible_as_missing_and_provider_time_cannot_regress(tmp_path):
    conn = open_store(tmp_path)
    first = normalized()["accounts"][0]
    finance_store.record_sync(conn, normalized(), now=NOW)
    older = dict(first, balance=Decimal("1.00"), balance_at="2026-09-06T10:00:00Z")
    finance_store.record_sync(conn, normalized(accounts=[older]), now=NOW + timedelta(hours=1))
    finance_store.record_sync(conn, normalized(accounts=[]), now=NOW + timedelta(hours=2))

    account = finance_store.dashboard(conn, now=NOW + timedelta(hours=2))["accounts"][0]

    assert account["balance"] == Decimal("100.10")
    assert account["balance_at"] == "2026-09-07T10:00:00Z"
    assert account["observed_at"] == "2026-09-07T13:00:00Z"
    assert account["missing"] is True


def test_stale_flags_use_provider_balance_age_over_48_hours(tmp_path):
    conn = open_store(tmp_path)
    current = dict(normalized()["accounts"][0], balance_at="2026-09-07T12:00:00Z")
    finance_store.record_sync(conn, normalized(accounts=[current]), now=NOW)

    fresh = finance_store.dashboard(conn, now=NOW + timedelta(hours=48))["accounts"][0]
    stale = finance_store.dashboard(
        conn, now=NOW + timedelta(hours=48, seconds=1)
    )["accounts"][0]

    assert fresh["stale"] is False
    assert stale["stale"] is True


def test_partial_sync_updates_available_accounts_but_retains_last_complete_success(tmp_path):
    conn = open_store(tmp_path)
    finance_store.record_sync(conn, normalized(), now=NOW)
    changed = dict(normalized()["accounts"][0], balance=Decimal("110.10"))

    finance_store.record_sync(
        conn,
        normalized(
            accounts=[changed], warnings=["connection_authentication"], complete=False
        ),
        now=NOW + timedelta(days=1),
    )
    result = finance_store.dashboard(conn, now=NOW + timedelta(days=1))

    assert result["accounts"][0]["balance"] == Decimal("110.10")
    assert result["totals"]["complete"] is False
    assert result["last_attempt"] == {
        "at": "2026-09-08T12:00:00Z",
        "status": "partial",
    }
    assert result["last_success"] == "2026-09-07T12:00:00Z"
    assert result["warnings"] == ["connection_authentication"]
    assert result["history"][-1]["complete"] is False


def test_same_utc_day_upserts_one_snapshot_without_backdating(tmp_path):
    conn = open_store(tmp_path)
    finance_store.record_sync(conn, normalized(), now=NOW)
    changed = dict(normalized()["accounts"][0], balance=Decimal("120.25"))

    finance_store.record_sync(
        conn, normalized(accounts=[changed]), now=NOW + timedelta(hours=3)
    )
    result = finance_store.dashboard(conn, now=NOW + timedelta(hours=3))

    assert result["history"] == [
        {
            "date": "2026-09-07",
            "by_currency": {"USD": Decimal("120.25")},
            "complete": True,
        }
    ]
    assert result["accounts"][0]["observed_at"] == "2026-09-07T15:00:00Z"


def test_record_failure_preserves_balances_and_last_success(tmp_path):
    conn = open_store(tmp_path)
    finance_store.record_sync(conn, normalized(), now=NOW)

    finance_store.record_failure(conn, now=NOW + timedelta(hours=1))
    result = finance_store.dashboard(conn, now=NOW + timedelta(hours=1))

    assert result["accounts"][0]["balance"] == Decimal("100.10")
    assert result["last_attempt"] == {
        "at": "2026-09-07T13:00:00Z",
        "status": "failure",
    }
    assert result["last_success"] == "2026-09-07T12:00:00Z"
    assert result["warnings"] == ["sync_failed"]


def test_record_sync_is_transactional_for_invalid_payload(tmp_path):
    conn = open_store(tmp_path)
    finance_store.record_sync(conn, normalized(), now=NOW)
    invalid = normalized(
        accounts=[dict(normalized()["accounts"][0], balance=Decimal("NaN"))]
    )

    with pytest.raises(finance_store.FinanceStoreError):
        finance_store.record_sync(conn, invalid, now=NOW + timedelta(hours=1))

    result = finance_store.dashboard(conn, now=NOW + timedelta(hours=1))
    assert result["accounts"][0]["balance"] == Decimal("100.10")
    assert result["last_attempt"]["at"] == "2026-09-07T12:00:00Z"


def test_record_sync_rejects_an_observation_older_than_the_last_attempt(tmp_path):
    conn = open_store(tmp_path)
    finance_store.record_sync(conn, normalized(), now=NOW)

    with pytest.raises(finance_store.FinanceStoreError):
        finance_store.record_sync(conn, normalized(), now=NOW - timedelta(seconds=1))

    assert finance_store.dashboard(conn, now=NOW)["last_attempt"] == {
        "at": "2026-09-07T12:00:00Z",
        "status": "success",
    }


def test_reject_database_inside_another_git_tree(tmp_path):
    other = tmp_path / 'other'
    other.mkdir()
    (other / '.git').mkdir()
    with pytest.raises(finance_store.FinanceStoreError):
        finance_store.connect(str(other / 'private' / 'finance.db'))


def test_missing_accounts_mark_totals_and_history_incomplete(tmp_path):
    from decimal import Decimal
    conn = finance_store.connect(str(tmp_path / 'private' / 'finance.db'))
    account = {'id': 'a' * 64, 'label': 'Checking', 'currency': 'USD',
               'balance': Decimal('5'), 'balance_at': '2026-09-07T12:00:00Z'}
    finance_store.record_sync(conn, {'accounts': [account], 'warnings': [], 'complete': True})
    finance_store.record_sync(conn, {'accounts': [], 'warnings': [], 'complete': True})
    result = finance_store.dashboard(conn)
    assert result['totals']['complete'] is False
    assert result['history'][-1]['complete'] is False
    assert result['accounts'][0]['missing'] is True
    conn.close()


def test_large_fractional_balances_keep_exact_total(tmp_path):
    conn = finance_store.connect(str(tmp_path / 'private' / 'finance.db'))
    accounts = [{'id': key * 64, 'label': key, 'currency': 'USD',
                 'balance': Decimal(amount), 'balance_at': '2026-09-07T12:00:00Z'}
                for key, amount in [('a', '999999999999999999999.123456789123456789'), ('b', '0.000000000000000001')]]
    finance_store.record_sync(conn, {'accounts': accounts, 'warnings': [], 'complete': True})
    assert finance_store.dashboard(conn)['totals']['by_currency']['USD'] == Decimal('999999999999999999999.123456789123456790')
    conn.close()
