"""Recurring commitments produce dated forecast occurrences, never transactions (spec §5.5, Test 10)."""
from datetime import date
from decimal import Decimal

import pytest

import finance_budget as B
import finance_recurring as R
from finance_store import FinanceStoreError
from test_finance_budget import ledger_db, cat, CHECKING


def c(**kw):
    base = dict(cadence='monthly', next_date='2026-10-31', end_date=None, status='active')
    base.update(kw)
    return base


def test_month_end_anchor_clamps():
    assert R.occurrences(c(), date(2026, 10, 1), date(2027, 3, 31)) == [
        date(2026, 10, 31), date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)]


def test_end_date_stops_occurrences():  # Test 10
    fence = c(next_date='2026-10-15', end_date='2027-04-15')
    dates = R.occurrences(fence, date(2026, 10, 1), date(2027, 12, 31))
    assert dates[-1] == date(2027, 4, 15) and len(dates) == 7


def test_other_cadences_and_status():
    assert R.occurrences(c(cadence='biweekly', next_date='2026-10-02'), date(2026, 10, 1), date(2026, 10, 31)) == [
        date(2026, 10, 2), date(2026, 10, 16), date(2026, 10, 30)]
    assert R.occurrences(c(cadence='weekly', next_date='2026-09-30'), date(2026, 10, 1), date(2026, 10, 15)) == [
        date(2026, 10, 7), date(2026, 10, 14)]
    assert R.occurrences(c(cadence='quarterly', next_date='2026-08-20'), date(2026, 10, 1), date(2027, 3, 1)) == [
        date(2026, 11, 20), date(2027, 2, 20)]
    assert R.occurrences(c(cadence='annual', next_date='2026-02-28'), date(2026, 10, 1), date(2028, 3, 1)) == [
        date(2027, 2, 28), date(2028, 2, 28)]
    assert R.occurrences(c(cadence='once', next_date='2026-10-10'), date(2026, 10, 1), date(2026, 12, 1)) == [date(2026, 10, 10)]
    assert R.occurrences(c(status='paused'), date(2026, 10, 1), date(2026, 12, 1)) == []
    assert R.occurrences(c(next_date=None), date(2026, 10, 1), date(2026, 12, 1)) == []


def test_seeded_commitments_need_dates(tmp_path):
    conn = ledger_db(tmp_path)
    rows = {r['name']: r for r in R.commitments(conn)}
    assert rows['Mortgage']['amount'] == Decimal('2910.21') and rows['Mortgage']['needs_date']
    assert rows['Income']['direction'] == 'in' and rows['Mattress financing']['amount_kind'] == 'placeholder'
    assert rows['Tirzepatide']['cadence'] == 'quarterly' and rows['Tirzepatide']['amount'] == Decimal('599.00')
    assert rows['Mortgage']['category_name'] == 'Mortgage'
    B.initialize(conn)
    assert len(R.commitments(conn)) == len(rows)


def test_save_commitment_validation_and_target_untouched(tmp_path):
    conn = ledger_db(tmp_path)
    mortgage = next(r for r in R.commitments(conn) if r['name'] == 'Mortgage')
    R.save_commitment(conn, commitment_id=mortgage['id'], name='Mortgage', direction='out', amount='2950', amount_kind='exact',
                      cadence='monthly', next_date='2026-11-01', end_date='', account_id=CHECKING,
                      category_id=mortgage['category_id'], status='active', notes='', actor='s')
    saved = next(r for r in R.commitments(conn) if r['name'] == 'Mortgage')
    assert saved['amount'] == Decimal('2950.00') and not saved['needs_date'] and saved['account_name'] == 'Checking'
    assert B.target_for(conn, cat(conn, 'Mortgage'), '2026-11')['amount'] == Decimal('2910.21')
    bad = [dict(direction='out', end_date='2026-10-01', next_date='2026-11-01'),
           dict(direction='sideways', end_date='', next_date=''),
           dict(direction='out', end_date='', next_date='', amount='0'),
           dict(direction='out', end_date='', next_date='', account_id='nope')]
    for fields in bad:
        args = dict(name='X', amount='10', amount_kind='exact', cadence='monthly', account_id='', category_id='',
                    status='active', notes='', actor='s')
        args.update(fields)
        with pytest.raises(FinanceStoreError):
            R.save_commitment(conn, **args)
