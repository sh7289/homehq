"""Sinking funds hold only recorded allocations; reserve status checks backing (Test 14, review Finding 3)."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import finance_budget as B
import finance_funds as F
import finance_ledger as L
import finance_store
from finance_ledger_math import month_summary
from finance_store import FinanceStoreError
from test_finance_budget import ledger_db, cat, CHECKING, CARD_S, CARD_H

SAVINGS = 'a' * 64
HSA = 'b' * 64
TODAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 15, 13, tzinfo=timezone.utc)


def full_db(tmp_path, checking='4000', savings='5000', hsa='3000', balance_at='2026-10-15T12:00:00Z'):
    """ledger_db plus savings and HSA accounts, all with real balances as of balance_at."""
    conn = ledger_db(tmp_path)
    accounts = [(CHECKING, 'Checking', checking), (CARD_S, 'Steve card', '0'), (CARD_H, 'Heather card', '0'),
                (SAVINGS, 'Savings', savings), (HSA, 'HSA', hsa)]
    finance_store.record_sync(conn, {'accounts': [dict(id=i, label=l, currency='USD', balance=Decimal(b), balance_at=balance_at)
                                                  for i, l, b in accounts], 'warnings': [], 'complete': True},
                              now=datetime(2026, 10, 15, 12, tzinfo=timezone.utc))
    B.set_account(conn, SAVINGS, included=True, role='savings')
    B.set_account(conn, HSA, included=True, role='hsa')
    return conn


def opening(conn, name, amount, day='2026-10-01'):
    F.record_movement(conn, category_id=cat(conn, name), kind='opening', amount=amount, movement_date=day, note='',
                      actor='s', today=TODAY)


def test_contribution_is_allocation_not_spending(tmp_path):  # Test 14
    conn = full_db(tmp_path)
    xmas = cat(conn, 'Gifts and Christmas')
    opening(conn, 'Gifts and Christmas', '500')
    F.record_movement(conn, category_id=xmas, kind='contribution', amount='200', movement_date='2026-10-02', note='', actor='s', today=TODAY)
    t = L.create_txn(conn, account_id=CARD_H, txn_date='2026-10-05', amount='-150', description='GIFTS', actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=xmas, person='shared', amount='-150')], actor='s')
    fund = next(f for f in F.funds(conn, '2026-10') if f['id'] == xmas)
    assert fund['balance'] == Decimal('550.00')
    assert (fund['month_spent'], fund['month_added']) == (Decimal('150.00'), Decimal('200.00'))
    assert month_summary(conn, '2026-10', TODAY)['known_spending']['total'] == Decimal('150.00')


def test_split_and_refund_move_fund(tmp_path):  # Tests 5 and 9
    conn = full_db(tmp_path)
    travel, xmas = cat(conn, 'Travel'), cat(conn, 'Gifts and Christmas')
    opening(conn, 'Travel', '1000')
    opening(conn, 'Gifts and Christmas', '1000')
    t = L.create_txn(conn, account_id=CARD_H, txn_date='2026-10-04', amount='-200', description='TARGET', actor='h')
    L.classify(conn, t, kind='expense', allocations=[
        dict(category_id=cat(conn, 'Groceries and household essentials'), person='shared', amount='-80'),
        dict(category_id=xmas, person='shared', amount='-70'),
        dict(category_id=cat(conn, 'Heather personal'), person='Heather', amount='-50')], actor='h')
    hotel = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-06', amount='-500', description='HOTEL', actor='s')
    L.classify(conn, hotel, kind='expense', allocations=[dict(category_id=travel, person='shared', amount='-500')], actor='s')
    refund = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-09', amount='500', description='HOTEL', actor='s')
    L.link(conn, kind='refund', from_id=refund, to_id=hotel, actor='s')
    balances = {f['id']: f['balance'] for f in F.funds(conn, '2026-10')}
    assert balances[xmas] == Decimal('930.00') and balances[travel] == Decimal('1000.00')


def test_setup_needed_is_not_zero(tmp_path):
    conn = full_db(tmp_path)
    fund = next(f for f in F.funds(conn, '2026-10') if f['name'] == 'Travel')
    assert fund['setup_needed'] and fund['balance'] is None


def test_spending_before_opening_is_ignored(tmp_path):
    conn = full_db(tmp_path)
    travel = cat(conn, 'Travel')
    t = L.create_txn(conn, account_id=CARD_S, txn_date='2026-09-20', amount='-300', description='FLIGHT', actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=travel, person='shared', amount='-300')], actor='s')
    opening(conn, 'Travel', '800')
    assert next(f for f in F.funds(conn, '2026-10') if f['id'] == travel)['balance'] == Decimal('800.00')


def test_opening_replaces_and_release_subtracts(tmp_path):
    conn = full_db(tmp_path)
    opening(conn, 'Travel', '800')
    opening(conn, 'Travel', '900', day='2026-10-02')
    F.record_movement(conn, category_id=cat(conn, 'Travel'), kind='release', amount='100', movement_date='2026-10-03', note='', actor='s', today=TODAY)
    F.record_movement(conn, category_id=cat(conn, 'Travel'), kind='adjustment', amount='-25.50', movement_date='2026-10-03', note='fix', actor='s', today=TODAY)
    assert next(f for f in F.funds(conn, '2026-10') if f['name'] == 'Travel')['balance'] == Decimal('774.50')
    assert len([m for m in F.movements(conn) if m['kind'] == 'opening']) == 1


def test_movement_validation(tmp_path):
    conn = full_db(tmp_path)
    bad = [dict(category_id=cat(conn, 'Household wants'), kind='contribution', amount='5', movement_date='2026-10-01'),
           dict(category_id=cat(conn, 'Travel'), kind='contribution', amount='-5', movement_date='2026-10-01'),
           dict(category_id=cat(conn, 'Travel'), kind='contribution', amount='5', movement_date='2026-10-20'),
           dict(category_id=cat(conn, 'Travel'), kind='adjustment', amount='0', movement_date='2026-10-01'),
           dict(category_id=cat(conn, 'Travel'), kind='gift', amount='5', movement_date='2026-10-01')]
    for fields in bad:
        with pytest.raises(FinanceStoreError):
            F.record_movement(conn, note='', actor='s', today=TODAY, **fields)


def test_funds_exceeding_backing_show_shortfall(tmp_path):  # review test
    conn = full_db(tmp_path, savings='5000')
    for name, amount in [('Gifts and Christmas', '2000'), ('Travel', '3000'), ('Home/car/pet reserve', '2000')]:
        opening(conn, name, amount)
    r = F.reserve_status(conn, now=NOW)
    assert (r['state'], r['backing'], r['allocated'], r['unallocated']) == (
        'shortfall', Decimal('5000.00'), Decimal('7000.00'), Decimal('-2000.00'))


def test_reserve_unknown_without_accounts_or_when_stale(tmp_path):
    conn = full_db(tmp_path)
    B.set_account(conn, SAVINGS, included=True, role='checking')
    assert F.reserve_status(conn, now=NOW)['state'] == 'unknown'
    B.set_account(conn, SAVINGS, included=True, role='savings')
    assert F.reserve_status(conn, now=datetime(2026, 10, 25, tzinfo=timezone.utc))['state'] == 'unknown'


def test_holds_reduce_unallocated(tmp_path):
    conn = full_db(tmp_path, savings='5000')
    B.set_money_setting(conn, 'reserve_holds', '1000')
    assert B.money_setting(conn, 'reserve_holds') == Decimal('1000.00')
    r = F.reserve_status(conn, now=NOW)
    assert (r['state'], r['unallocated']) == ('ok', Decimal('4000.00'))
    B.set_money_setting(conn, 'reserve_holds', '')
    assert B.money_setting(conn, 'reserve_holds') is None
