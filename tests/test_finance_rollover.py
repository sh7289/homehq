"""Remaining amounts and rollover for monthly capped categories (spec Tests 1-3)."""
from datetime import date
from decimal import Decimal

import finance_budget as B
import finance_ledger as L
from finance_ledger_math import allowances, month_summary
from test_finance_budget import ledger_db, cat, CARD_S


def spend(conn, name, amount, day):
    t = L.create_txn(conn, account_id=CARD_S, txn_date=day, amount=amount, description=name, actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, name), person='shared', amount=amount)], actor='s')


def row(conn, name, month):
    return next(a for a in allowances(conn, month, date(2027, 1, 31)) if a['name'] == name)


def test_dining_remaining_and_reset(tmp_path):  # Tests 1 and 2
    conn = ledger_db(tmp_path)
    spend(conn, 'Shared dining and entertainment', '-240', '2026-10-05')
    oct_ = row(conn, 'Shared dining and entertainment', '2026-10')
    assert (oct_['available'], oct_['spent'], oct_['remaining']) == (Decimal('650.00'), Decimal('240.00'), Decimal('410.00'))
    nov = row(conn, 'Shared dining and entertainment', '2026-11')
    assert (nov['carry_in'], nov['available']) == (Decimal('0.00'), Decimal('650.00'))


def test_household_wants_capped_rollover(tmp_path):  # Test 3
    conn = ledger_db(tmp_path)
    spend(conn, 'Household wants', '-200', '2026-10-05')
    assert row(conn, 'Household wants', '2026-11')['available'] == Decimal('400.00')
    assert row(conn, 'Household wants', '2026-12')['available'] == Decimal('700.00')
    assert row(conn, 'Household wants', '2027-01')['available'] == Decimal('900.00')
    assert conn.execute('SELECT COUNT(*) FROM finance_txns').fetchone()[0] == 1


def test_overspend_carries_for_capped_rollover(tmp_path):
    conn = ledger_db(tmp_path)
    spend(conn, 'Household wants', '-350', '2026-10-05')
    assert row(conn, 'Household wants', '2026-11')['available'] == Decimal('250.00')


def test_carry_rollover_unbounded(tmp_path):
    conn = ledger_db(tmp_path)
    B.save_category(conn, category_id=cat(conn, 'Steve personal'), name='Steve personal', type='capped',
                    default_person='Steve', rollover='carry')
    assert row(conn, 'Steve personal', '2027-01')['available'] == Decimal('1400.00')


def test_chain_starts_at_budget_start(tmp_path):
    conn = ledger_db(tmp_path)
    B.set_target(conn, category_id=cat(conn, 'Household wants'), effective_month='2026-09', amount='300',
                 basis='planning', note='', actor='s', today=date(2026, 9, 1))
    assert row(conn, 'Household wants', '2026-10')['carry_in'] == Decimal('0.00')


def test_no_target_means_no_remaining(tmp_path):
    conn = ledger_db(tmp_path)
    a = row(conn, 'Shared dining and entertainment', '2026-09')
    assert a['no_target'] and a['remaining'] is None and a['available'] is None


def test_summary_includes_allowances(tmp_path):
    conn = ledger_db(tmp_path)
    s = month_summary(conn, '2026-10', date(2026, 10, 15))
    assert [a['name'] for a in s['allowances']][:2] == ['Shared dining and entertainment', 'Household wants']
