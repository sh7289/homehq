"""Budget month summary: coverage, data quality, spending and plan completeness."""
from datetime import date
from decimal import Decimal

import finance_budget as B
import finance_ledger as L
from finance_ledger_math import month_summary
from test_finance_budget import ledger_db, cat, CHECKING, CARD_S, CARD_H

LATER = date(2026, 12, 31)


def cover_all(conn, start='2026-11-01', end='2026-11-30'):
    for a in (CHECKING, CARD_S, CARD_H):
        B.declare_coverage(conn, account_id=a, start=start, end=end, actor='s', today=LATER)


def row(summary, name):
    return next(c for c in summary['categories'] if c['name'] == name)


def spend(conn, account, amount, name, person='shared', day='2026-11-10', status='posted'):
    t = L.create_txn(conn, account_id=account, txn_date=day, amount=amount, description=name, actor='s', status=status)
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, name), person=person, amount=amount)], actor='s')
    return t


def test_dining_spent_independent_of_card(tmp_path):  # Test 1 (spent side), Test 4
    conn = ledger_db(tmp_path)
    cover_all(conn)
    spend(conn, CARD_S, '-140', 'Shared dining and entertainment')
    spend(conn, CARD_H, '-100', 'Shared dining and entertainment')
    s = month_summary(conn, '2026-11', date(2026, 12, 2))
    r = row(s, 'Shared dining and entertainment')
    assert (r['target'], r['spent'], r['pct_used']) == (Decimal('650.00'), Decimal('240.00'), 37)
    assert s['quality'] == 'complete'


def test_card_payment_not_spending_and_refund_nets(tmp_path):  # Tests 6, 9
    conn = ledger_db(tmp_path)
    cover_all(conn)
    hotel = spend(conn, CARD_S, '-500', 'Travel')
    out = L.create_txn(conn, account_id=CHECKING, txn_date='2026-11-20', amount='-500', description='PAY', actor='s')
    inn = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-20', amount='500', description='PAY', actor='s')
    L.link(conn, kind='card_payment', from_id=out, to_id=inn, actor='s')
    refund = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-25', amount='500', description='Travel', actor='s')
    L.link(conn, kind='refund', from_id=refund, to_id=hotel, actor='s')
    s = month_summary(conn, '2026-11', date(2026, 12, 2))
    assert row(s, 'Travel')['spent'] == Decimal('0.00')
    assert s['known_spending']['total'] == Decimal('0.00')


def test_pending_replacement_counts_once(tmp_path):  # Test 11
    conn = ledger_db(tmp_path)
    cover_all(conn)
    p = spend(conn, CARD_S, '-50', 'Shared dining and entertainment', status='pending')
    q = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-11', amount='-60', description='x', actor='s')
    L.link(conn, kind='pending_posted', from_id=p, to_id=q, actor='s')
    assert row(month_summary(conn, '2026-11', date(2026, 12, 2)), 'Shared dining and entertainment')['spent'] == Decimal('60.00')


def test_pending_shown_separately_and_provisional(tmp_path):
    conn = ledger_db(tmp_path)
    cover_all(conn)
    spend(conn, CARD_S, '-50', 'Shared dining and entertainment', status='pending')
    s = month_summary(conn, '2026-11', date(2026, 12, 2))
    r = row(s, 'Shared dining and entertainment')
    assert (r['posted'], r['pending'], r['spent']) == (Decimal('0.00'), Decimal('50.00'), Decimal('50.00'))
    assert s['quality'] == 'provisional'


def test_split_hits_three_categories(tmp_path):  # Test 5
    conn = ledger_db(tmp_path)
    cover_all(conn)
    t = L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-04', amount='-200', description='TARGET', actor='h')
    L.classify(conn, t, kind='expense', allocations=[
        dict(category_id=cat(conn, 'Groceries and household essentials'), person='shared', amount='-80'),
        dict(category_id=cat(conn, 'Gifts and Christmas'), person='shared', amount='-70'),
        dict(category_id=cat(conn, 'Heather personal'), person='Heather', amount='-50')], actor='h')
    s = month_summary(conn, '2026-11', date(2026, 12, 2))
    names = ['Groceries and household essentials', 'Gifts and Christmas', 'Heather personal']
    assert [row(s, n)['spent'] for n in names] == [Decimal('80.00'), Decimal('70.00'), Decimal('50.00')]
    assert s['known_spending']['total'] == Decimal('200.00')


def test_parent_includes_children(tmp_path):
    conn = ledger_db(tmp_path)
    cover_all(conn)
    spend(conn, CHECKING, '-2910.21', 'Mortgage')
    s = month_summary(conn, '2026-11', date(2026, 12, 2))
    assert row(s, 'Housing and fixed obligations')['spent'] == Decimal('2910.21')
    assert row(s, 'Housing and fixed obligations')['target'] == Decimal('5829.49')


def test_missing_card_makes_month_insufficient(tmp_path):  # Test 13
    conn = ledger_db(tmp_path)
    for a in (CHECKING, CARD_S):
        B.declare_coverage(conn, account_id=a, start='2026-11-01', end='2026-11-30', actor='s', today=LATER)
    B.declare_coverage(conn, account_id=CARD_H, start='2026-11-01', end='2026-11-10', actor='s', today=LATER)
    B.declare_coverage(conn, account_id=CARD_H, start='2026-11-21', end='2026-11-30', actor='s', today=LATER)
    s = month_summary(conn, '2026-11', date(2026, 12, 2))
    assert s['quality'] == 'insufficient'
    gap = next(c for c in s['coverage'] if c['account_id'] == CARD_H)
    assert gap['missing'] == [('2026-11-11', '2026-11-20')]
    assert next(c for c in s['coverage'] if c['account_id'] == CHECKING)['missing'] == []


def test_excluded_account_does_not_block(tmp_path):
    conn = ledger_db(tmp_path)
    B.set_account(conn, CARD_H, included=False, role='card')
    for a in (CHECKING, CARD_S):
        B.declare_coverage(conn, account_id=a, start='2026-11-01', end='2026-11-30', actor='s', today=LATER)
    assert month_summary(conn, '2026-11', date(2026, 12, 2))['quality'] == 'complete'


def test_uncategorized_counts_in_known_spending(tmp_path):  # adversarial review test
    conn = ledger_db(tmp_path)
    cover_all(conn)
    L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-12', amount='-250', description='AMAZON', actor='s')
    L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-13', amount='-150', description='TARGET', actor='s')
    L.create_txn(conn, account_id=CHECKING, txn_date='2026-11-13', amount='75', description='VENMO', actor='s')
    s = month_summary(conn, '2026-11', date(2026, 12, 2))
    assert s['uncategorized'] == {'count': 2, 'amount': Decimal('400.00')}
    assert s['unreviewed_inflows'] == {'count': 1, 'amount': Decimal('75.00')}
    assert s['known_spending']['total'] == Decimal('400.00')
    assert s['quality'] == 'provisional'


def test_plan_incomplete_names_missing_categories(tmp_path):
    conn = ledger_db(tmp_path)
    plan = month_summary(conn, '2026-11', date(2026, 11, 15))['plan']
    assert plan['label'] == 'incomplete'
    assert 'Groceries and household essentials' in plan['missing'] and 'Savings' in plan['missing']
    assert 'Housing and fixed obligations' not in plan['missing']


def test_plan_provisional_then_validated(tmp_path):
    conn = ledger_db(tmp_path)
    for c in B.categories(conn):
        if c['parent_id'] is None and c['type'] in ('operating', 'savings'):
            B.set_target(conn, category_id=c['id'], effective_month='2026-11', amount='100', basis='historical',
                         note='', actor='s', today=date(2026, 11, 1))
    plan = month_summary(conn, '2026-11', date(2026, 11, 15))['plan']
    assert plan['label'] == 'provisional' and plan['missing'] == []
    assert plan['surplus'] == plan['income'] - plan['outflows']
    for t in conn.execute('SELECT category_id, effective_month, amount FROM finance_budget_targets').fetchall():
        B.set_target(conn, category_id=t['category_id'], effective_month=t['effective_month'], amount=t['amount'],
                     basis='historical', note='', actor='s', today=date(2026, 11, 1))
    plan = month_summary(conn, '2026-11', date(2026, 11, 15))['plan']
    assert plan['label'] == 'validated' and plan['unvalidated'] == 0


def test_current_month_coverage_only_to_today(tmp_path):
    conn = ledger_db(tmp_path)
    cover_all(conn, end='2026-11-14')
    assert month_summary(conn, '2026-11', date(2026, 11, 14))['quality'] == 'complete'


def test_future_month_is_insufficient_without_division(tmp_path):
    conn = ledger_db(tmp_path)
    s = month_summary(conn, '2027-03', date(2026, 11, 14))
    assert s['quality'] == 'insufficient' and 'Month has not started.' in s['quality_reasons']
    assert all(c['pct_used'] in (None, 0) for c in s['categories'])


def test_other_currency_kept_separate(tmp_path):
    import finance_store
    conn = ledger_db(tmp_path)
    cover_all(conn)
    euro = 'f' * 64
    conn.execute("INSERT INTO finance_accounts (id,label,currency,balance,balance_at,observed_at,missing,source) "
                 "VALUES (?, 'Euro card', 'EUR', '0', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 0, 'manual')", (euro,))
    conn.commit()
    B.set_account(conn, euro, included=False, role='card')
    L.create_txn(conn, account_id=euro, txn_date='2026-11-03', amount='-20', description='CAFE', actor='s')
    s = month_summary(conn, '2026-11', date(2026, 12, 2))
    assert s['other_currencies'] == {'EUR': Decimal('20.00')}
    assert s['known_spending']['total'] == Decimal('0.00')
    assert finance_store
