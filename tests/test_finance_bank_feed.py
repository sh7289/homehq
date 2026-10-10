"""Bank-sync transactions: posted SimpleFIN rows land in the ledger once, with coverage and undo."""
from datetime import date
from decimal import Decimal

import finance_bank_feed as F
import finance_budget
import finance_import
import finance_ledger as L
import finance_store
from test_finance_budget import ledger_db, CHECKING, CARD_S, CARD_H

TODAY = date(2026, 10, 9)
SAVINGS = 'a' * 64


def row(h, day='2026-10-05', amount='-42.10', description='TRADER JOES', posted=None):
    return dict(id_hash=h * 64 if len(h) == 1 else h, txn_date=day, posted_date=posted or day,
                amount=Decimal(amount), description=description)


def feed(rows, account=CARD_S, complete=True):
    return {account: {'complete': complete, 'transactions': rows}}


def sync_txns(conn):
    return conn.execute("SELECT t.* FROM finance_txns t JOIN finance_source_records s ON s.txn_id=t.id "
                        "WHERE s.source='sync' ORDER BY t.id").fetchall()


def coverage(conn, account=CARD_S):
    return [(r['start_date'], r['end_date']) for r in conn.execute(
        'SELECT start_date, end_date FROM finance_coverage WHERE account_id=? ORDER BY id', (account,))]


def test_new_posted_transactions_become_unreviewed_ledger_rows_with_coverage(tmp_path):
    conn = ledger_db(tmp_path)
    result = F.record(conn, feed([row('1'), row('2', '2026-10-06', '-5.00', 'CAFE 5551234567')]), complete=True, today=TODAY)

    assert result == {CARD_S: {'new': 2, 'duplicate': 0, 'covered': True}}
    txns = sync_txns(conn)
    assert [(t['txn_date'], t['amount'], t['status'], t['review'] in ('unreviewed', 'suggested')) for t in txns] == [
        ('2026-10-05', '-42.10', 'posted', True), ('2026-10-06', '-5.00', 'posted', True)]
    assert txns[1]['original_description'] == 'CAFE ••••'
    assert coverage(conn) == [('2026-10-01', '2026-10-08')]
    batch = conn.execute('SELECT * FROM finance_import_batches').fetchone()
    assert (batch['label'], batch['state'], batch['imported_by'], batch['new_count']) == ('Bank sync', 'committed', 'bank-sync', 2)


def test_overlapping_sync_skips_rows_already_stored_and_adds_no_empty_batch(tmp_path):
    conn = ledger_db(tmp_path)
    F.record(conn, feed([row('1')]), complete=True, today=TODAY)
    result = F.record(conn, feed([row('1')]), complete=True, today=date(2026, 10, 10))

    assert result == {CARD_S: {'new': 0, 'duplicate': 1, 'covered': True}}
    assert len(sync_txns(conn)) == 1
    assert conn.execute('SELECT COUNT(*) FROM finance_import_batches').fetchone()[0] == 1
    assert coverage(conn)[-1] == ('2026-10-01', '2026-10-09')


def test_identical_purchases_with_distinct_bank_ids_are_both_kept(tmp_path):
    conn = ledger_db(tmp_path)
    F.record(conn, feed([row('1', amount='-3.00', description='COFFEE'), row('2', amount='-3.00', description='COFFEE')]),
             complete=True, today=TODAY)
    assert len(sync_txns(conn)) == 2


def test_only_included_accounts_and_dates_from_budget_start_are_stored(tmp_path):
    conn = ledger_db(tmp_path)
    finance_budget.set_account(conn, CARD_H, included=False, role='card')
    data = feed([row('1'), row('2', '2026-09-28')])
    data.update(feed([row('3')], account=CARD_H))
    data.update(feed([row('4')], account=SAVINGS))
    result = F.record(conn, data, complete=True, today=TODAY)

    assert set(result) == {CARD_S}
    assert [t['account_id'] for t in sync_txns(conn)] == [CARD_S]


def test_incomplete_account_or_payload_records_rows_but_no_coverage(tmp_path):
    conn = ledger_db(tmp_path)
    F.record(conn, feed([row('1')], complete=False), complete=True, today=TODAY)
    F.record(conn, feed([row('2')], account=CHECKING), complete=False, today=TODAY)
    assert len(sync_txns(conn)) == 2
    assert coverage(conn) == [] and coverage(conn, CHECKING) == []


def test_hand_entered_match_is_flagged_as_possible_duplicate(tmp_path):
    conn = ledger_db(tmp_path)
    manual = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-04', amount='-42.10', description='groceries', actor='s')
    F.record(conn, feed([row('1')]), complete=True, today=TODAY)
    assert sync_txns(conn)[0]['possible_duplicate_of'] == manual


def test_start_date_overlaps_last_sync_and_never_reaches_past_budget_start_or_sixty_days(tmp_path):
    conn = ledger_db(tmp_path)
    assert F.start_date(conn, TODAY) == '2026-10-01'
    F.record(conn, {a: {'complete': True, 'transactions': []} for a in (CHECKING, CARD_S, CARD_H)},
             complete=True, today=date(2026, 10, 20))
    assert F.start_date(conn, date(2026, 10, 21)) == '2026-10-12'
    finance_budget.set_account(conn, CHECKING, included=False, role='checking')
    finance_budget.set_account(conn, CARD_S, included=False, role='card')
    finance_budget.set_account(conn, CARD_H, included=False, role='card')
    assert F.start_date(conn, TODAY) is None


def test_start_date_is_clamped_to_sixty_days(tmp_path):
    conn = ledger_db(tmp_path)
    assert F.start_date(conn, date(2027, 3, 1)) == '2026-12-31'


def test_undoing_a_sync_batch_removes_its_rows_and_coverage(tmp_path):
    conn = ledger_db(tmp_path)
    F.record(conn, feed([row('1')]), complete=True, today=TODAY)
    batch = conn.execute('SELECT id FROM finance_import_batches').fetchone()['id']
    finance_import.rollback(conn, batch, actor='s')
    assert sync_txns(conn) == [] and coverage(conn) == []
    assert F.record(conn, feed([row('1')]), complete=True, today=TODAY)[CARD_S]['new'] == 1


def test_upgrade_allows_sync_source_and_keeps_existing_records(tmp_path):
    conn = ledger_db(tmp_path)
    kept = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-02', amount='-9.00', description='old', actor='s')
    old_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='finance_source_records'").fetchone()[0]
    conn.executescript(
        'PRAGMA foreign_keys=OFF; ALTER TABLE finance_source_records RENAME TO tmp_records;'
        + old_sql.replace("'csv','manual','sync'", "'csv','manual'") + ';'
        'INSERT INTO finance_source_records SELECT * FROM tmp_records; DROP TABLE tmp_records; PRAGMA foreign_keys=ON;')
    assert "'sync'" not in conn.execute("SELECT sql FROM sqlite_master WHERE name='finance_source_records'").fetchone()[0]

    conn.close()
    conn = finance_store.connect(str(tmp_path / 'private' / 'finance.db'))

    assert "'sync'" in conn.execute("SELECT sql FROM sqlite_master WHERE name='finance_source_records'").fetchone()[0]
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='finance_source_records'")}
    assert {'finance_source_fp', 'finance_source_id'} <= indexes
    assert conn.execute('SELECT txn_id FROM finance_source_records').fetchone()['txn_id'] == kept
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    F.record(conn, feed([row('1')]), complete=True, today=TODAY)
    assert len(sync_txns(conn)) == 1
