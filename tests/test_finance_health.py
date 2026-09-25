"""Status panel: six independent dimensions, three distinct numbers, real exceptions only (Test 16, review Finding 8)."""
from decimal import Decimal

import finance_budget as B
import finance_funds as F
import finance_health as H
import finance_ledger as L
import finance_recurring as R
from test_finance_budget import cat, CHECKING, CARD_S, CARD_H
from test_finance_funds import full_db, SAVINGS, HSA, NOW, TODAY


def dims(d):
    return {x['key']: x['state'] for x in d['dimensions']}


def clear_seeds(conn):
    conn.execute('DELETE FROM finance_recurring')
    conn.commit()


def test_budget_is_not_checking_cash(tmp_path):  # Test 16
    conn = full_db(tmp_path, checking='600')
    clear_seeds(conn)
    t = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-03', amount='-50', description='POTS', actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, 'Household wants'), person='shared', amount='-50')], actor='s')
    R.save_commitment(conn, name='Bill', direction='out', amount='500', amount_kind='exact', cadence='once', next_date='2026-10-20',
                      end_date='', account_id=CHECKING, category_id='', status='active', notes='', actor='s')
    d = H.dashboard(conn, '2026-10', TODAY, now=NOW)
    wants = next(a for a in d['allowances'] if a['name'] == 'Household wants')
    assert wants['remaining'] == Decimal('250.00')
    assert d['numbers']['projected_cash'] == Decimal('100.00') and d['numbers']['available_cash'] == Decimal('600.00')
    assert [x['key'] for x in d['dimensions']] == ['affordability', 'discretionary', 'reserve', 'savings', 'liquidity', 'reliability']


def test_good_dimension_does_not_hide_others(tmp_path):
    conn = full_db(tmp_path, savings='5000')
    for name, amount in [('Gifts and Christmas', '4000'), ('Travel', '3000')]:
        F.record_movement(conn, category_id=cat(conn, name), kind='opening', amount=amount, movement_date='2026-10-01',
                          note='', actor='s', today=TODAY)
    d = H.dashboard(conn, '2026-10', TODAY, now=NOW)
    states = dims(d)
    assert states['discretionary'] == 'unknown'
    assert states['reserve'] == 'attention' and states['savings'] == 'unknown' and states['affordability'] == 'unknown'
    assert states['liquidity'] == 'unknown' and states['reliability'] == 'unknown'
    assert any('exceed' in e for e in d['exceptions'])


def test_small_overspend_is_not_an_exception(tmp_path):
    conn = full_db(tmp_path, checking='50000')
    clear_seeds(conn)
    for a in (CHECKING, CARD_S, CARD_H, SAVINGS, HSA):
        B.declare_coverage(conn, account_id=a, start='2026-10-01', end='2026-10-15', actor='s', today=TODAY)
    t = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-03', amount='-670', description='DINNERS', actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, 'Shared dining and entertainment'), person='shared', amount='-670')], actor='s')
    d = H.dashboard(conn, '2026-10', TODAY, now=NOW)
    assert dims(d)['discretionary'] == 'attention' and dims(d)['liquidity'] == 'good'
    assert d['exceptions'] == []


def test_projected_shortfall_is_an_exception(tmp_path):
    conn = full_db(tmp_path, checking='100')
    clear_seeds(conn)
    R.save_commitment(conn, name='Mortgage', direction='out', amount='2910.21', amount_kind='exact', cadence='once',
                      next_date='2026-11-01', end_date='', account_id=CHECKING, category_id='', status='active', notes='', actor='s')
    d = H.dashboard(conn, '2026-10', TODAY, now=NOW)
    assert dims(d)['liquidity'] == 'attention'
    assert any('2026-11-01' in e for e in d['exceptions'])


def test_stale_card_is_an_exception(tmp_path):
    conn = full_db(tmp_path)
    conn.execute("UPDATE finance_accounts SET balance_at='2026-10-01T00:00:00Z' WHERE id=?", (CARD_H,))
    conn.commit()
    d = H.dashboard(conn, '2026-10', TODAY, now=NOW)
    assert any('Heather card' in e for e in d['exceptions'])


def test_remaining_budget_total_only_counts_targets(tmp_path):
    conn = full_db(tmp_path)
    d = H.dashboard(conn, '2026-10', TODAY, now=NOW)
    assert d['numbers']['remaining_budget'] == Decimal('1725.00')
    assert H.dashboard(conn, '2026-09', TODAY, now=NOW)['numbers']['remaining_budget'] is None
