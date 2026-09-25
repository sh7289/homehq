"""Savings progress is measured from savings accounts, never assumed (review Findings 2 and 10)."""
from datetime import date
from decimal import Decimal

import finance_budget as B
import finance_funds as F
import finance_health as H
import finance_ledger as L
import finance_savings as S
from finance_ledger_math import month_summary
from test_finance_budget import cat, CHECKING, CARD_S, CARD_H
from test_finance_funds import full_db, SAVINGS, HSA, NOW, TODAY


def cover(conn, accounts, end='2026-10-31', today=date(2026, 11, 2)):
    for a in accounts:
        B.declare_coverage(conn, account_id=a, start='2026-10-01', end=end, actor='s', today=today)


def savings_goal(conn, amount='1000'):
    B.set_target(conn, category_id=cat(conn, 'Savings'), effective_month='2026-10', amount=amount, basis='planning',
                 note='', actor='s', today=date(2026, 10, 1))


def test_savings_below_plan_is_attention_even_within_allowances(tmp_path):  # review test
    conn = full_db(tmp_path)
    savings_goal(conn)
    cover(conn, [CHECKING, CARD_S, CARD_H, SAVINGS, HSA])
    out = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-05', amount='-100', description='TO SAVINGS', actor='s')
    inn = L.create_txn(conn, account_id=SAVINGS, txn_date='2026-10-05', amount='100', description='FROM CHECKING', actor='s')
    L.link(conn, kind='transfer', from_id=out, to_id=inn, actor='s')
    p = S.progress(conn, '2026-10', date(2026, 11, 2))
    assert (p['state'], p['planned'], p['actual']) == ('attention', Decimal('1000.00'), Decimal('100.00'))
    d = H.dashboard(conn, '2026-10', date(2026, 11, 2), now=NOW)
    states = {x['key']: x['state'] for x in d['dimensions']}
    assert states['savings'] == 'attention' and states['discretionary'] == 'good'


def test_fund_contributions_are_not_savings(tmp_path):  # review: planned contribution not recorded as completed
    conn = full_db(tmp_path)
    savings_goal(conn, '300')
    cover(conn, [SAVINGS])
    travel = cat(conn, 'Travel')
    F.record_movement(conn, category_id=travel, kind='opening', amount='0', movement_date='2026-10-01', note='', actor='s', today=TODAY)
    F.record_movement(conn, category_id=travel, kind='contribution', amount='300', movement_date='2026-10-02', note='', actor='s', today=TODAY)
    p = S.progress(conn, '2026-10', date(2026, 11, 2))
    assert (p['state'], p['actual'], p['fund_allocations']) == ('attention', Decimal('0.00'), Decimal('300.00'))


def test_unknown_without_target_or_coverage(tmp_path):
    conn = full_db(tmp_path)
    assert S.progress(conn, '2026-10', date(2026, 11, 2))['state'] == 'unknown'
    savings_goal(conn)
    p = S.progress(conn, '2026-10', date(2026, 11, 2))
    assert p['state'] == 'unknown' and p['actual'] is None


def test_current_month_savings_coverage_to_today(tmp_path):
    conn = full_db(tmp_path)
    savings_goal(conn, '100')
    cover(conn, [SAVINGS], end='2026-10-15', today=TODAY)
    L.create_txn(conn, account_id=SAVINGS, txn_date='2026-10-10', amount='150', description='DEPOSIT', actor='s')
    assert S.progress(conn, '2026-10', TODAY)['state'] == 'good'


def test_funding_split(tmp_path):  # Test 7 / review Finding 10
    conn = full_db(tmp_path)
    t = L.create_txn(conn, account_id=HSA, txn_date='2026-10-12', amount='-599', description='PHARMACY', actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, 'Tirzepatide'), person='shared', amount='-599')], actor='s')
    L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-12', amount='-40', description='?', actor='s')
    f = month_summary(conn, '2026-10', TODAY)['funding']
    assert f == {'hsa': Decimal('599.00'), 'everyday': Decimal('40.00'), 'total': Decimal('639.00')}


def test_dashboard_savings_dimension(tmp_path):
    conn = full_db(tmp_path)
    savings_goal(conn)
    cover(conn, [SAVINGS], end='2026-10-15', today=TODAY)
    d = H.dashboard(conn, '2026-10', TODAY, now=NOW)
    assert next(x for x in d['dimensions'] if x['key'] == 'savings')['state'] == 'attention'
    assert d['savings']['planned'] == Decimal('1000.00')
