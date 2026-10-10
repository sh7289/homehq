"""Bank-sync transactions: posted SimpleFIN rows for budget accounts, stored once.

The daily sync passes the feed from ``simplefin.fetch_accounts`` here after the
balances are saved. Only accounts included in the budget are stored. Each account
with new rows gets a committed import batch labelled "Bank sync", so its rows and
coverage can be undone like a CSV import. A sync that finds nothing new extends
coverage under that account's latest batch instead of adding an empty one.
"""
from datetime import date, timedelta

import finance_budget
import finance_ledger
from finance_book import _transaction
from finance_budget import money_text
from finance_store import _iso, _utc

ACTOR = 'bank-sync'
LABEL = 'Bank sync'
OVERLAP_DAYS = 7
MAX_HISTORY_DAYS = 60


def _included(conn):
    return [r['id'] for r in conn.execute(
        "SELECT a.id FROM finance_accounts a JOIN finance_budget_accounts b ON b.account_id=a.id "
        "WHERE b.included=1 AND a.source='provider' AND a.currency != 'NONFINANCIAL' ORDER BY a.id")]


def _budget_start(conn):
    return date.fromisoformat(finance_budget.get_setting(conn, 'budget_start') or finance_budget.SEED_START_MONTH + '-01')


def _latest_batch(conn, account_id):
    return conn.execute("SELECT id FROM finance_import_batches WHERE account_id=? AND imported_by=? AND state='committed' "
                        'ORDER BY id DESC LIMIT 1', (account_id, ACTOR)).fetchone()


def _account_start(conn, account_id, today):
    last = conn.execute(
        'SELECT MAX(c.end_date) FROM finance_coverage c JOIN finance_import_batches b ON b.id=c.batch_id '
        "WHERE c.account_id=? AND b.imported_by=? AND b.state='committed'", (account_id, ACTOR)).fetchone()[0]
    start = max(_budget_start(conn), today - timedelta(days=MAX_HISTORY_DAYS))
    if last:
        start = max(start, date.fromisoformat(last) - timedelta(days=OVERLAP_DAYS))
    return start


def start_date(conn, today):
    """The earliest date the next sync must ask for, or None when no budget account is connected."""
    starts = [_account_start(conn, account_id, today) for account_id in _included(conn)]
    return min(starts).isoformat() if starts else None


def record(conn, feed, *, complete, today):
    """Store new posted rows for each included account; returns per-account counts."""
    budget_start = _budget_start(conn).isoformat()
    end = today - timedelta(days=1)
    results = {}
    for account_id in _included(conn):
        entry = feed.get(account_id)
        if entry is None:
            continue
        with _transaction(conn):
            start = _account_start(conn, account_id, today)
            rows, duplicate = [], 0
            for txn in entry['transactions']:
                if txn['txn_date'] < budget_start or txn['amount'] == 0:
                    continue
                if conn.execute("SELECT 1 FROM finance_source_records WHERE account_id=? AND source='sync' "
                                'AND source_txn_hash=?', (account_id, txn['id_hash'])).fetchone():
                    duplicate += 1
                else:
                    rows.append(txn)
            batch = _latest_batch(conn, account_id)
            if rows or batch is None:
                now = _iso(_utc())
                batch_id = conn.execute(
                    "INSERT INTO finance_import_batches (account_id,state,label,imported_by,created_at,header_json,row_count,"
                    "new_count,duplicate_count,committed_at) VALUES (?,'committed',?,?,?,'[]',?,?,?,?)",
                    (account_id, LABEL, ACTOR, now, len(rows) + duplicate, len(rows), duplicate, now)).lastrowid
            else:
                batch_id = batch['id']
            for txn in rows:
                columns = dict(date=txn['txn_date'], description=txn['description'], amount=money_text(txn['amount']),
                               posted_date=txn['posted_date'])
                finance_ledger.create_txn(conn, account_id=account_id, txn_date=txn['txn_date'], amount=txn['amount'],
                                          description=txn['description'], actor=ACTOR, posted_date=txn['posted_date'],
                                          source='sync', batch_id=batch_id, source_txn_hash=txn['id_hash'],
                                          columns=columns)
            covered = bool(complete and entry['complete'] and start <= end)
            if covered:
                finance_budget.declare_coverage(conn, account_id=account_id, start=start.isoformat(), end=end.isoformat(),
                                                actor=ACTOR, today=today, source='import', batch_id=batch_id)
        results[account_id] = dict(new=len(rows), duplicate=duplicate, covered=covered)
    return results
