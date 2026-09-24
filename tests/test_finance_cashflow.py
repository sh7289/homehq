"""Checking forecast: the cash path never counts card purchases, only settlements (Tests 7, 8, 15)."""
from datetime import date
from decimal import Decimal

import finance_budget as B
import finance_cashflow as C
import finance_ledger as L
import finance_payments
import finance_recurring as R
from finance_ledger_math import month_summary
from test_finance_budget import cat, CHECKING, CARD_S
from test_finance_funds import full_db, HSA, NOW, TODAY


def commit(conn, name, direction, amount, next_date, account=CHECKING, cadence='once'):
    return R.save_commitment(conn, name=name, direction=direction, amount=amount, amount_kind='exact', cadence=cadence,
                             next_date=next_date, end_date='', account_id=account, category_id='', status='active',
                             notes='', actor='s')


def clear_seeds(conn):
    conn.execute('DELETE FROM finance_recurring')
    conn.commit()


def test_forecast_without_double_counting(tmp_path):  # Test 15
    conn = full_db(tmp_path, checking='4000')
    clear_seeds(conn)
    commit(conn, 'Paycheck', 'in', '3000', '2026-10-20')
    commit(conn, 'Other bill', 'out', '1000', '2026-10-25')
    finance_payments.save(conn, card_id=CARD_S, funding_id=CHECKING, amount='2000', payment_date='2026-10-22', status='scheduled')
    t = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-10', amount='-2000', description='STUFF', actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, 'Household wants'), person='shared', amount='-2000')], actor='s')
    f = C.forecast(conn, TODAY, now=NOW)
    assert f['start_balance'] == Decimal('4000.00') and f['end_balance'] == Decimal('4000.00')
    assert [e['kind'] for e in f['events']] == ['income', 'card_payment', 'bill']
    assert f['state'] == 'ok'


def test_card_payment_before_paycheck_is_flagged(tmp_path):  # review test
    conn = full_db(tmp_path, checking='1500')
    clear_seeds(conn)
    finance_payments.save(conn, card_id=CARD_S, funding_id=CHECKING, amount='2000', payment_date='2026-10-18', status='scheduled')
    commit(conn, 'Paycheck', 'in', '3000', '2026-10-20')
    f = C.forecast(conn, TODAY, now=NOW)
    assert f['below_minimum'] == {'date': '2026-10-18', 'balance': Decimal('-500.00')}
    assert f['uncovered_card_payments'][0]['date'] == '2026-10-18'
    assert f['end_balance'] == Decimal('2500.00')
    assert f['lowest'] == {'date': '2026-10-18', 'balance': Decimal('-500.00')}


def test_hsa_purchase_leaves_checking_forecast_alone(tmp_path):  # Test 7
    conn = full_db(tmp_path, checking='4000')
    clear_seeds(conn)
    t = L.create_txn(conn, account_id=HSA, txn_date='2026-10-12', amount='-599', description='PHARMACY', actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, 'Tirzepatide'), person='shared', amount='-599')], actor='s')
    assert C.forecast(conn, TODAY, now=NOW)['end_balance'] == Decimal('4000.00')
    s = month_summary(conn, '2026-10', TODAY)
    assert next(c for c in s['categories'] if c['name'] == 'Health and medical')['spent'] == Decimal('599.00')


def test_hsa_reimbursement_is_transfer_not_wages(tmp_path):  # Test 8
    conn = full_db(tmp_path)
    clear_seeds(conn)
    exp = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-28', amount='-200', description='DOCTOR', actor='s')
    L.classify(conn, exp, kind='expense', allocations=[dict(category_id=cat(conn, 'Health and medical'), person='shared', amount='-200')], actor='s')
    out = L.create_txn(conn, account_id=HSA, txn_date='2026-11-03', amount='-200', description='HSA REIMB', actor='s')
    inn = L.create_txn(conn, account_id=CHECKING, txn_date='2026-11-03', amount='200', description='HSA REIMB', actor='s')
    L.link(conn, kind='transfer', from_id=out, to_id=inn, actor='s')
    oct_, nov = month_summary(conn, '2026-10', date(2026, 12, 1)), month_summary(conn, '2026-11', date(2026, 12, 1))
    assert next(c for c in oct_['categories'] if c['name'] == 'Health and medical')['spent'] == Decimal('200.00')
    assert nov['income'] == Decimal('0.00') and nov['known_spending']['total'] == Decimal('0.00')


def test_items_before_balance_date_are_not_resubtracted(tmp_path):
    conn = full_db(tmp_path, balance_at='2026-10-12T12:00:00Z')
    clear_seeds(conn)
    commit(conn, 'Water', 'out', '130', '2026-10-10')
    commit(conn, 'Gas', 'out', '35', '2026-10-13')
    f = C.forecast(conn, TODAY, now=NOW)
    assert [p['label'] for p in f['past_due']] == ['Water']
    assert [e['label'] for e in f['events']] == ['Gas']
    assert f['state'] == 'provisional' and f['as_of'] == '2026-10-12'


def test_needs_date_and_unknown(tmp_path):
    conn = full_db(tmp_path)
    f = C.forecast(conn, TODAY, now=NOW)
    assert 'Mortgage' in f['needs_date'] and f['state'] == 'provisional'
    B.set_account(conn, CHECKING, included=False, role='checking')
    u = C.forecast(conn, TODAY, now=NOW)
    assert u['state'] == 'unknown' and u['start_balance'] is None and u['end_balance'] is None


def test_minimum_and_available_cash(tmp_path):
    conn = full_db(tmp_path, checking='4000')
    clear_seeds(conn)
    B.set_money_setting(conn, 'checking_minimum', '1000')
    commit(conn, 'Bill', 'out', '3500', '2026-10-20')
    f = C.forecast(conn, TODAY, now=NOW)
    assert f['available_cash'] == Decimal('3000.00') and f['minimum'] == Decimal('1000.00')
    assert f['below_minimum'] == {'date': '2026-10-20', 'balance': Decimal('500.00')}


def test_paid_or_linked_card_payment_excluded(tmp_path):
    conn = full_db(tmp_path, checking='4000')
    clear_seeds(conn)
    pid = finance_payments.save(conn, card_id=CARD_S, funding_id=CHECKING, amount='700', payment_date='2026-10-22', status='scheduled')
    finance_payments.save(conn, card_id=CARD_S, funding_id=CHECKING, amount='50', payment_date='2026-10-23', status='paid')
    out = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-14', amount='-700', description='PAY', actor='s')
    inn = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-14', amount='700', description='PAY', actor='s')
    L.link(conn, kind='card_payment', from_id=out, to_id=inn, actor='s', payment_id=pid)
    assert C.forecast(conn, TODAY, now=NOW)['events'] == []


def test_window_and_recurring_occurrences(tmp_path):
    conn = full_db(tmp_path, checking='4000')
    clear_seeds(conn)
    commit(conn, 'Mortgage', 'out', '2910.21', '2026-10-01', cadence='monthly')
    f = C.forecast(conn, TODAY, now=NOW)
    assert f['end_date'] == '2026-11-30'
    assert [e['date'] for e in f['events']] == ['2026-11-01']
    assert f['past_due'] == []  # the October payment was before the balance date and is a normal past occurrence
