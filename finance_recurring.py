"""Recurring income and bills: dated schedules that generate forecast occurrences.

An occurrence is an expectation, never a transaction. A due date alone is not proof
of payment. Editing a commitment never changes a category target: schedules and
intentions stay separate.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

import finance_budget
from finance_book import _text, _transaction
from finance_budget import money_text, parse_money
from finance_ledger import _display
from finance_store import FinanceStoreError, _iso, _utc

DIRECTIONS = ('in', 'out')
AMOUNT_KINDS = ('exact', 'estimate', 'variable', 'placeholder')
CADENCES = ('weekly', 'biweekly', 'monthly', 'quarterly', 'semiannual', 'annual', 'once')
STATUSES = ('active', 'paused', 'ended')
MONTH_STEPS = {'monthly': 1, 'quarterly': 3, 'semiannual': 6, 'annual': 12}
DAY_STEPS = {'weekly': 7, 'biweekly': 14}


def _add_months(anchor, months):
    index = anchor.month - 1 + months
    year, month = anchor.year + index // 12, index % 12 + 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def occurrences(commitment, start, end):
    """Dates of a commitment's occurrences within [start, end], respecting its end date."""
    if commitment.get('status') != 'active' or not commitment.get('next_date'):
        return []
    anchor = date.fromisoformat(commitment['next_date'])
    stop = end
    if commitment.get('end_date'):
        stop = min(stop, date.fromisoformat(commitment['end_date']))
    cadence = commitment['cadence']
    if cadence == 'once':
        return [anchor] if start <= anchor <= stop else []
    result = []
    if cadence in DAY_STEPS:
        step = DAY_STEPS[cadence]
        current = anchor
        if current < start:
            current += timedelta(days=-(-(start - current).days // step) * step)
        while current <= stop:
            result.append(current)
            current += timedelta(days=step)
        return result
    step, index = MONTH_STEPS[cadence], 0
    while True:
        current = _add_months(anchor, index * step)
        if current > stop:
            return result
        if current >= start:
            result.append(current)
        index += 1


def _optional_day(value):
    if value in (None, ''):
        return None
    return finance_budget._day(value).isoformat()


def save_commitment(conn, *, commitment_id=None, name, direction, amount, amount_kind, cadence, next_date, end_date,
                    account_id, category_id, status, notes, actor):
    name, actor = _text(name, True), _text(actor, True)
    if direction not in DIRECTIONS or amount_kind not in AMOUNT_KINDS or cadence not in CADENCES or status not in STATUSES:
        raise FinanceStoreError('Commitment settings are invalid.')
    value = parse_money(amount if isinstance(amount, str) else str(amount), signed=False)
    if value == 0:
        raise FinanceStoreError('Enter an amount greater than zero.')
    first, last = _optional_day(next_date), _optional_day(end_date)
    if first and last and last < first:
        raise FinanceStoreError('The end date must be on or after the next date.')
    notes = finance_budget._long_text(notes or '', 300)
    with _transaction(conn):
        account = account_id or None
        if account and account not in finance_budget.account_settings(conn):
            raise FinanceStoreError('Choose an account set up for budgeting.')
        category = finance_budget._category(conn, category_id)['id'] if category_id not in (None, '') else None
        values = (name, direction, money_text(value), amount_kind, cadence, first, last, account, category, status, notes)
        now = _iso(_utc())
        if commitment_id in (None, ''):
            return conn.execute('INSERT INTO finance_recurring (name,direction,amount,amount_kind,cadence,next_date,end_date,'
                                'account_id,category_id,status,notes,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                values + (actor, now)).lastrowid
        from finance_ledger import _id
        commitment_id = _id(commitment_id, 'Commitment was not found.')
        if not conn.execute('SELECT 1 FROM finance_recurring WHERE id=?', (commitment_id,)).fetchone():
            raise FinanceStoreError('Commitment was not found.')
        conn.execute('UPDATE finance_recurring SET name=?,direction=?,amount=?,amount_kind=?,cadence=?,next_date=?,end_date=?,'
                     'account_id=?,category_id=?,status=?,notes=?,updated_by=?,updated_at=? WHERE id=?',
                     values + (actor, now, commitment_id))
        return commitment_id


def commitments(conn):
    names = _display(conn)
    categories = {r['id']: r['name'] for r in conn.execute('SELECT id, name FROM finance_budget_categories')}
    rows = []
    for row in conn.execute("SELECT * FROM finance_recurring ORDER BY status='ended', direction='out', name"):
        item = dict(row, amount=Decimal(row['amount']))
        item.update(account_name=names.get(row['account_id'], ''), category_name=categories.get(row['category_id'], ''),
                    needs_date=row['status'] == 'active' and not row['next_date'])
        rows.append(item)
    return rows
