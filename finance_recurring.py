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


MATCH_WINDOW = timedelta(days=7)
TOLERANCE = Decimal('0.25')


def _fits(commitment, amount):
    """Does an actual amount (positive, in the commitment's direction) fit the expectation?"""
    if commitment['amount_kind'] == 'exact':
        return amount == commitment['amount']
    return abs(amount - commitment['amount']) <= commitment['amount'] * TOLERANCE


def matched_dates(conn):
    return {(r['recurring_id'], r['occurrence_date']) for r in conn.execute(
        'SELECT recurring_id, occurrence_date FROM finance_recurring_matches')}


def match_candidates(conn, txn_id):
    from finance_ledger import _txn
    txn = _txn(conn, txn_id)
    if txn['status'] in ('void', 'replaced') or conn.execute(
            'SELECT 1 FROM finance_recurring_matches WHERE txn_id=?', (txn['id'],)).fetchone():
        return []
    amount, day = Decimal(txn['amount']), date.fromisoformat(txn['txn_date'])
    direction = 'in' if amount > 0 else 'out'
    taken = matched_dates(conn)
    found = []
    for commitment in commitments(conn):
        if commitment['account_id'] != txn['account_id'] or commitment['direction'] != direction:
            continue
        if not _fits(commitment, abs(amount)):
            continue
        for when in occurrences(commitment, day - MATCH_WINDOW, day + MATCH_WINDOW):
            if (commitment['id'], when.isoformat()) not in taken:
                found.append(dict(recurring_id=commitment['id'], name=commitment['name'], occurrence_date=when.isoformat(),
                                  expected=commitment['amount'], distance=abs((when - day).days)))
    found.sort(key=lambda c: (c['distance'], c['name']))
    return [{k: v for k, v in c.items() if k != 'distance'} for c in found[:5]]


def match(conn, *, recurring_id, occurrence_date, txn_id, actor):
    actor = _text(actor, True)
    with _transaction(conn):
        from finance_ledger import _id, _txn
        txn = _txn(conn, txn_id)
        recurring_id = _id(recurring_id, 'That expected bill or payday was not found.')
        if not any(c['recurring_id'] == recurring_id and c['occurrence_date'] == occurrence_date
                   for c in match_candidates(conn, txn['id'])):
            raise FinanceStoreError('That expected bill or payday does not match this transaction.')
        return conn.execute('INSERT INTO finance_recurring_matches (recurring_id,occurrence_date,txn_id,created_by,created_at) '
                            'VALUES (?,?,?,?,?)', (recurring_id, occurrence_date, txn['id'], actor, _iso(_utc()))).lastrowid


def unmatch(conn, match_id, actor):
    _text(actor, True)
    from finance_ledger import _id
    with _transaction(conn):
        row = conn.execute('SELECT * FROM finance_recurring_matches WHERE id=?', (_id(match_id, 'Match was not found.'),)).fetchone()
        if row is None:
            raise FinanceStoreError('Match was not found.')
        conn.execute('DELETE FROM finance_recurring_matches WHERE id=?', (row['id'],))
        return dict(row)


def txn_match(conn, txn_id):
    row = conn.execute('SELECT m.*, r.name, r.amount FROM finance_recurring_matches m JOIN finance_recurring r '
                       'ON r.id=m.recurring_id WHERE m.txn_id=?', (txn_id,)).fetchone()
    return dict(row, amount=Decimal(row['amount'])) if row else None


def latest_actuals(conn):
    result = {}
    for row in conn.execute('SELECT m.recurring_id, t.txn_date, t.amount FROM finance_recurring_matches m JOIN finance_txns t '
                            'ON t.id=m.txn_id ORDER BY t.txn_date, m.id'):
        result[row['recurring_id']] = dict(date=row['txn_date'], amount=abs(Decimal(row['amount'])))
    return result


def ending_soon(conn, today, days=90):
    horizon = (today + timedelta(days=days)).isoformat()
    rows = conn.execute("SELECT name, end_date FROM finance_recurring WHERE status='active' AND end_date IS NOT NULL "
                        'AND end_date <= ? ORDER BY end_date, name', (horizon,)).fetchall()
    return [dict(name=r['name'], end_date=r['end_date'], past=r['end_date'] < today.isoformat()) for r in rows]
