"""CSV import: staging, normalization, idempotent dedupe and rollback (acceptance test 12)."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

import finance_import as I
import finance_ledger as L
from finance_store import FinanceStoreError
from test_finance_budget import ledger_db, cat, CHECKING, CARD_S

NOW = datetime(2026, 12, 31, tzinfo=timezone.utc)
CSV = b"Date,Description,Amount\n11/01/2026,COFFEE,-4.50\n11/01/2026,COFFEE,-4.50\n11/02/2026,PAYROLL ACME 123456789,3000.00\n"
MAP = {'date': 0, 'description': 1, 'amount': 2, 'sign': 'outflow_negative'}


def run(conn, data, account=CHECKING, start='2026-11-01', end='2026-11-30'):
    b = I.stage(conn, account_id=account, data=data, label='nov', actor='s', now=NOW)
    return I.commit(conn, b, MAP, period_start=start, period_end=end, actor='s', now=NOW)


def test_reimport_is_idempotent_and_keeps_identical_rows(tmp_path):  # Test 12
    conn = ledger_db(tmp_path)
    assert run(conn, CSV) == {'new': 3, 'duplicate': 0}
    t = conn.execute("SELECT id FROM finance_txns WHERE amount='-4.50' ORDER BY id").fetchone()['id']
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, 'Shared dining and entertainment'), person='shared', amount='-4.50')], actor='s')
    assert run(conn, CSV) == {'new': 0, 'duplicate': 3}
    assert conn.execute('SELECT COUNT(*) FROM finance_txns').fetchone()[0] == 3
    assert L.get_txn(conn, t)['review'] == 'accepted'


def test_overlapping_export_adds_only_new_rows(tmp_path):
    conn = ledger_db(tmp_path)
    run(conn, CSV)
    later = CSV + b"11/03/2026,COFFEE,-4.50\n"
    assert run(conn, later) == {'new': 1, 'duplicate': 3}


def test_provider_id_takes_precedence(tmp_path):
    conn = ledger_db(tmp_path)
    data = b"Id,Date,Description,Amount\nA1,2026-11-01,SHOP,-5\nA2,2026-11-01,SHOP,-5\n"
    mapping = {'id': 0, 'date': 1, 'description': 2, 'amount': 3, 'sign': 'outflow_negative'}
    b = I.stage(conn, account_id=CHECKING, data=data, label='x', actor='s', now=NOW)
    assert I.commit(conn, b, mapping, period_start='2026-11-01', period_end='2026-11-01', actor='s', now=NOW)['new'] == 2
    renamed = b"Id,Date,Description,Amount\nA1,2026-11-01,SHOP INC,-5\n"
    b = I.stage(conn, account_id=CHECKING, data=renamed, label='x', actor='s', now=NOW)
    assert I.preview(conn, b, mapping)['duplicate'] == 1
    assert 'A1' not in str(conn.execute('SELECT columns_json, source_txn_hash FROM finance_source_records').fetchall()[0][:])


def test_parse_amount_formats(tmp_path):
    conn = ledger_db(tmp_path)
    data = b'Date,Payee,Debit,Credit\n2026-11-04,SHOP,"$1,020.00",\n2026-11-05,REFUND,,(3.25)\n'
    b = I.stage(conn, account_id=CARD_S, data=data, label='x', actor='s', now=NOW)
    rows, errors = I.normalize(conn, b, {'date': 0, 'description': 1, 'debit': 2, 'credit': 3, 'sign': 'debit_credit'})
    assert errors == [] and [r['amount'] for r in rows] == [Decimal('-1020.00'), Decimal('3.25')]


def test_outflow_positive_and_pending_status(tmp_path):
    conn = ledger_db(tmp_path)
    data = b'Date,Description,Amount,Status\n11/4/26,SHOP,12.00,Pending\n11/5/26,RETURN,-3.00,Posted\n'
    b = I.stage(conn, account_id=CARD_S, data=data, label='x', actor='s', now=NOW)
    rows, errors = I.normalize(conn, b, {'date': 0, 'description': 1, 'amount': 2, 'status': 3, 'sign': 'outflow_positive'})
    assert errors == []
    assert [(r['txn_date'], r['amount'], r['status']) for r in rows] == [
        ('2026-11-04', Decimal('-12.00'), 'pending'), ('2026-11-05', Decimal('3.00'), 'posted')]


def test_bom_and_blank_rows(tmp_path):
    conn = ledger_db(tmp_path)
    b = I.stage(conn, account_id=CHECKING, data=b'\xef\xbb\xbfDate,Description,Amount\n2026-11-01,A,-1\n\n,,\n', label='x', actor='s', now=NOW)
    rows, errors = I.normalize(conn, b, MAP)
    assert len(rows) == 1 and errors == []
    assert I.default_mapping(conn, b) == MAP


def test_errors_do_not_echo_content_and_block_commit(tmp_path):
    conn = ledger_db(tmp_path)
    b = I.stage(conn, account_id=CHECKING, data=b'Date,Description,Amount\nnot-a-date,SECRET 999,-1\n', label='x', actor='s', now=NOW)
    rows, errors = I.normalize(conn, b, MAP)
    assert errors == ['Row 2: date not recognized.']
    with pytest.raises(FinanceStoreError):
        I.commit(conn, b, MAP, period_start='2026-11-01', period_end='2026-11-30', actor='s', now=NOW)


def test_period_must_contain_rows(tmp_path):
    conn = ledger_db(tmp_path)
    b = I.stage(conn, account_id=CHECKING, data=CSV, label='x', actor='s', now=NOW)
    with pytest.raises(FinanceStoreError):
        I.commit(conn, b, MAP, period_start='2026-11-02', period_end='2026-11-30', actor='s', now=NOW)


def test_descriptions_are_masked_and_raw_file_not_kept(tmp_path):
    conn = ledger_db(tmp_path)
    run(conn, CSV)
    descs = [r[0] for r in conn.execute('SELECT original_description FROM finance_txns')]
    assert 'PAYROLL ACME ••••' in descs
    assert '123456789' not in str([tuple(r) for r in conn.execute('SELECT * FROM finance_source_records')])
    assert conn.execute('SELECT COUNT(*) FROM finance_staged_rows').fetchone()[0] == 0
    profile = conn.execute('SELECT csv_profile FROM finance_budget_accounts WHERE account_id=?', (CHECKING,)).fetchone()[0]
    assert '"amount": 2' in profile


def test_size_limits_and_garbage(tmp_path):
    conn = ledger_db(tmp_path)
    with pytest.raises(FinanceStoreError):
        I.stage(conn, account_id=CHECKING, data=b'a,b\n' + b'1,2\n' * 5001, label='x', actor='s', now=NOW)
    with pytest.raises(FinanceStoreError):
        I.stage(conn, account_id=CHECKING, data=b'x' * (I.MAX_BYTES + 1), label='x', actor='s', now=NOW)
    with pytest.raises(FinanceStoreError):
        I.stage(conn, account_id=CHECKING, data=b'only a header\n', label='x', actor='s', now=NOW)


def test_rollback_requires_confirmation_for_reviewed(tmp_path):
    conn = ledger_db(tmp_path)
    run(conn, CSV)
    batch = conn.execute("SELECT id FROM finance_import_batches WHERE state='committed'").fetchone()['id']
    t = conn.execute("SELECT id FROM finance_txns WHERE amount='3000.00'").fetchone()['id']
    L.classify(conn, t, kind='income', allocations=[dict(category_id=cat(conn, 'Income'), person='shared', amount='3000')], actor='s')
    with pytest.raises(FinanceStoreError):
        I.rollback(conn, batch, actor='s')
    assert I.rollback(conn, batch, actor='s', confirm_edited=True) == {'removed': 3, 'edited': 1}
    assert conn.execute('SELECT COUNT(*) FROM finance_coverage').fetchone()[0] == 0
    assert run(conn, CSV)['new'] == 3


def test_rollback_restores_replaced_pending(tmp_path):
    conn = ledger_db(tmp_path)
    p = L.create_txn(conn, account_id=CHECKING, txn_date='2026-11-01', amount='-4.50', description='COFFEE', actor='s', status='pending')
    run(conn, CSV)
    q = conn.execute("SELECT id FROM finance_txns WHERE amount='-4.50' AND status='posted' ORDER BY id").fetchone()['id']
    L.link(conn, kind='pending_posted', from_id=p, to_id=q, actor='s')
    batch = conn.execute("SELECT id FROM finance_import_batches WHERE state='committed'").fetchone()['id']
    I.rollback(conn, batch, actor='s', confirm_edited=True)
    assert L.get_txn(conn, p)['status'] == 'pending'


def test_manual_entry_flags_possible_duplicate(tmp_path):
    conn = ledger_db(tmp_path)
    m = L.create_txn(conn, account_id=CHECKING, txn_date='2026-11-02', amount='3000', description='paycheck', actor='s')
    run(conn, CSV)
    dup = conn.execute("SELECT possible_duplicate_of FROM finance_txns WHERE amount='3000.00' AND id != ?", (m,)).fetchone()[0]
    assert dup == m


def test_discard_and_stale_staging_purged(tmp_path):
    conn = ledger_db(tmp_path)
    old = I.stage(conn, account_id=CHECKING, data=CSV, label='x', actor='s', now=datetime(2026, 12, 1, tzinfo=timezone.utc))
    b = I.stage(conn, account_id=CHECKING, data=CSV, label='x', actor='s', now=NOW)
    assert conn.execute('SELECT COUNT(*) FROM finance_import_batches WHERE id=?', (old,)).fetchone()[0] == 0
    I.discard(conn, b)
    assert conn.execute('SELECT COUNT(*) FROM finance_staged_rows').fetchone()[0] == 0
