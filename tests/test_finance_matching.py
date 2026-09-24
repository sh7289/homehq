"""Actual transactions supersede expected bills and paydays (spec §5.5)."""
from decimal import Decimal

import pytest

import finance_cashflow as C
import finance_ledger as L
import finance_recurring as R
from finance_store import FinanceStoreError
from test_finance_budget import CHECKING
from test_finance_funds import full_db, NOW, TODAY


def dated(conn, name, direction, amount, day, kind='exact', cadence='monthly'):
    return R.save_commitment(conn, name=name, direction=direction, amount=amount, amount_kind=kind, cadence=cadence,
                             next_date=day, end_date='', account_id=CHECKING, category_id='', status='active', notes='',
                             actor='s')


def fresh(tmp_path, **kw):
    conn = full_db(tmp_path, **kw)
    conn.execute('DELETE FROM finance_recurring')
    conn.commit()
    return conn


def test_match_removes_occurrence_from_forecast(tmp_path):
    conn = fresh(tmp_path, checking='4000')
    rid = dated(conn, 'Water', 'out', '130', '2026-10-17', kind='estimate', cadence='once')
    assert [e['label'] for e in C.forecast(conn, TODAY, now=NOW)['events']] == ['Water']
    t = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-14', amount='-141.20', description='CITY WATER', actor='s')
    assert [(c['name'], c['occurrence_date']) for c in R.match_candidates(conn, t)] == [('Water', '2026-10-17')]
    mid = R.match(conn, recurring_id=rid, occurrence_date='2026-10-17', txn_id=t, actor='s')
    assert C.forecast(conn, TODAY, now=NOW)['events'] == []
    assert R.latest_actuals(conn)[rid] == {'date': '2026-10-14', 'amount': Decimal('141.20')}
    assert R.txn_match(conn, t)['name'] == 'Water'
    R.unmatch(conn, mid, actor='s')
    assert [e['label'] for e in C.forecast(conn, TODAY, now=NOW)['events']] == ['Water']


def test_matched_past_item_leaves_past_due(tmp_path):
    conn = fresh(tmp_path, balance_at='2026-10-12T12:00:00Z')
    rid = dated(conn, 'Water', 'out', '130', '2026-10-10', cadence='once')
    assert [p['label'] for p in C.forecast(conn, TODAY, now=NOW)['past_due']] == ['Water']
    t = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-10', amount='-130', description='WATER', actor='s')
    R.match(conn, recurring_id=rid, occurrence_date='2026-10-10', txn_id=t, actor='s')
    assert C.forecast(conn, TODAY, now=NOW)['past_due'] == []


def test_tolerance_and_direction(tmp_path):
    conn = fresh(tmp_path)
    mortgage = dated(conn, 'Mortgage', 'out', '2910.21', '2026-10-01')
    dated(conn, 'Power', 'out', '215', '2026-10-12', kind='variable')
    off = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-01', amount='-2900', description='MORTGAGE', actor='s')
    assert R.match_candidates(conn, off) == []
    power = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-13', amount='-260', description='GA POWER', actor='s')
    assert [c['name'] for c in R.match_candidates(conn, power)] == ['Power']
    wild = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-13', amount='-300', description='GA POWER', actor='s')
    assert R.match_candidates(conn, wild) == []
    deposit = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-01', amount='2910.21', description='X', actor='s')
    assert R.match_candidates(conn, deposit) == []
    with pytest.raises(FinanceStoreError):
        R.match(conn, recurring_id=mortgage, occurrence_date='2026-10-01', txn_id=off, actor='s')
    with pytest.raises(FinanceStoreError):
        R.match(conn, recurring_id=mortgage, occurrence_date='2026-10-02', txn_id=deposit, actor='s')


def test_one_transaction_one_occurrence(tmp_path):
    conn = fresh(tmp_path)
    a = dated(conn, 'Gym', 'out', '50', '2026-10-10')
    b = dated(conn, 'Club', 'out', '50', '2026-10-11')
    t = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-10', amount='-50', description='GYM', actor='s')
    R.match(conn, recurring_id=a, occurrence_date='2026-10-10', txn_id=t, actor='s')
    assert R.match_candidates(conn, t) == []
    with pytest.raises(FinanceStoreError):
        R.match(conn, recurring_id=b, occurrence_date='2026-10-11', txn_id=t, actor='s')
    t2 = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-10', amount='-50', description='GYM', actor='s')
    with pytest.raises(FinanceStoreError):
        R.match(conn, recurring_id=a, occurrence_date='2026-10-10', txn_id=t2, actor='s')


def test_match_disappears_with_transaction(tmp_path):
    conn = fresh(tmp_path)
    rid = dated(conn, 'Gym', 'out', '50', '2026-10-10')
    t = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-10', amount='-50', description='GYM', actor='s')
    R.match(conn, recurring_id=rid, occurrence_date='2026-10-10', txn_id=t, actor='s')
    conn.execute('DELETE FROM finance_txns WHERE id=?', (t,))
    conn.commit()
    assert R.matched_dates(conn) == set()


def test_ending_soon(tmp_path):
    conn = fresh(tmp_path)
    for name, first, last in [('RAV4', '2026-10-05', '2026-10-05'), ('Fence', '2026-10-15', '2027-04-15'),
                              ('Old loan', '2026-01-01', '2026-06-01')]:
        R.save_commitment(conn, name=name, direction='out', amount='10', amount_kind='exact', cadence='monthly',
                          next_date=first, end_date=last, account_id=CHECKING, category_id='', status='active', notes='', actor='s')
    assert [(s['name'], s['past']) for s in R.ending_soon(conn, TODAY)] == [('Old loan', True), ('RAV4', True)]
    assert [s['name'] for s in R.ending_soon(conn, TODAY, days=200)][-1] == 'Fence'
