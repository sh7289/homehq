"""Bonus planner: an editable proposal; only lines a person ticks are recorded (spec §6.6, review bonus test)."""
from decimal import Decimal

import pytest

import finance_bonus as Bo
import finance_funds as F
import finance_ledger as L
from finance_store import FinanceStoreError
from test_finance_budget import cat, CHECKING
from test_finance_funds import full_db, TODAY


def test_default_proposal_and_rounding():
    lines = Bo.propose(Decimal('10000.01'), [35, 25, 15, 10, 15])
    assert [l['amount'] for l in lines] == [Decimal('3500.00'), Decimal('2500.00'), Decimal('1500.00'),
                                            Decimal('1000.00'), Decimal('1500.01')]
    assert sum(l['amount'] for l in lines) == Decimal('10000.01')
    assert [l['key'] for l in lines] == ['savings', 'Travel', 'Gifts and Christmas', 'Celebrations', 'Home/car/pet reserve']


def test_shares_must_total_100():
    with pytest.raises(FinanceStoreError):
        Bo.propose(Decimal('100'), [35, 25, 15, 10, 10])
    with pytest.raises(FinanceStoreError):
        Bo.propose(Decimal('100'), [135, -25, -10, 0, 0])
    with pytest.raises(FinanceStoreError):
        Bo.propose(Decimal('0'), [35, 25, 15, 10, 15])


def test_record_only_ticked_fund_lines(tmp_path):  # review bonus test
    conn = full_db(tmp_path)
    for name in ('Travel', 'Gifts and Christmas'):
        F.record_movement(conn, category_id=cat(conn, name), kind='opening', amount='0', movement_date='2026-10-01',
                          note='', actor='s', today=TODAY)
    before = conn.execute('SELECT COUNT(*) FROM finance_fund_movements').fetchone()[0]
    n = Bo.record(conn, lines=[dict(key='savings', amount='3500'), dict(key='Travel', amount='2500')], actor='s', today=TODAY)
    assert n == 1
    assert conn.execute('SELECT COUNT(*) FROM finance_fund_movements').fetchone()[0] == before + 1
    travel = next(f for f in F.funds(conn, '2026-10', today=TODAY) if f['name'] == 'Travel')
    assert travel['balance'] == Decimal('2500.00')
    assert F.movements(conn)[0]['note'] == 'Bonus allocation'


def test_record_refuses_unopened_fund_atomically(tmp_path):
    conn = full_db(tmp_path)
    F.record_movement(conn, category_id=cat(conn, 'Travel'), kind='opening', amount='0', movement_date='2026-10-01',
                      note='', actor='s', today=TODAY)
    with pytest.raises(FinanceStoreError):
        Bo.record(conn, lines=[dict(key='Travel', amount='100'), dict(key='Celebrations', amount='100')], actor='s', today=TODAY)
    assert conn.execute("SELECT COUNT(*) FROM finance_fund_movements WHERE kind='contribution'").fetchone()[0] == 0
    with pytest.raises(FinanceStoreError):
        Bo.record(conn, lines=[dict(key='Nonsense', amount='100')], actor='s', today=TODAY)


def test_recent_income(tmp_path):
    conn = full_db(tmp_path)
    t = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-01', amount='8000', description='BONUS', actor='s')
    L.classify(conn, t, kind='income', allocations=[dict(category_id=cat(conn, 'Income'), person='shared', amount='8000')], actor='s')
    L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-02', amount='50', description='VENMO', actor='s')
    assert [(r['id'], r['amount']) for r in Bo.recent_income(conn, TODAY)] == [(t, Decimal('8000.00'))]
