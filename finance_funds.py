"""Sinking funds: virtual allocations of cash the household already has.

A fund balance moves only when someone records a movement (opening, contribution,
release, adjustment) or when categorized spending and refunds hit the fund. A
scheduled contribution never credits a fund, and moving money into a fund is not
spending. Reserve status checks those allocations against the cash that backs them.
"""
from decimal import Decimal

import finance_budget
from finance_book import _text, _transaction
from finance_budget import money_text, parse_money
from finance_store import MANUAL_STALE_AFTER, STALE_AFTER, FinanceStoreError, _iso, _parse_iso, _utc

KINDS = ('opening', 'contribution', 'release', 'adjustment')
ZERO = Decimal('0.00')
RESERVE_ROLES = ('savings', 'reserve')


def record_movement(conn, *, category_id, kind, amount, movement_date, note, actor, today=None):
    if kind not in KINDS:
        raise FinanceStoreError('Choose opening balance, contribution, release or adjustment.')
    day = finance_budget._day(movement_date)
    if day > (today or _utc().date()):
        raise FinanceStoreError('Fund movements cannot be dated in the future. Record them when the money is set aside.')
    value = parse_money(amount if isinstance(amount, str) else str(amount))
    if (kind == 'adjustment' and value == 0) or (kind != 'adjustment' and value < 0) or (kind in ('contribution', 'release') and value == 0):
        raise FinanceStoreError('Enter a positive amount (adjustments may be negative, but not zero).')
    note, actor = finance_budget._long_text(note or '', 200), _text(actor, True)
    with _transaction(conn):
        category = finance_budget._category(conn, category_id)
        if category['type'] != 'sinking' or not category['active']:
            raise FinanceStoreError('Choose an active sinking fund.')
        if kind == 'opening':
            conn.execute("DELETE FROM finance_fund_movements WHERE category_id=? AND kind='opening'", (category['id'],))
        return conn.execute('INSERT INTO finance_fund_movements (category_id,kind,amount,movement_date,note,created_by,created_at) '
                            'VALUES (?,?,?,?,?,?,?)', (category['id'], kind, money_text(value), day.isoformat(), note, actor,
                                                       _iso(_utc()))).lastrowid


def _spending(conn, ids, start=None, end=None):
    marks = ','.join('?' * len(ids))
    query = ("SELECT COALESCE(a.amount, '0') AS amount FROM finance_txn_allocations a JOIN finance_txns t ON t.id=a.txn_id "
             f"WHERE a.category_id IN ({marks}) AND t.status IN ('posted','pending') "
             "AND t.kind IN ('expense','refund','reimbursement') AND t.currency=?")
    args = list(ids) + [finance_budget.primary_currency(conn)]
    if start:
        query += ' AND t.txn_date >= ?'
        args.append(start)
    if end:
        query += ' AND t.txn_date <= ?'
        args.append(end)
    return -sum((Decimal(r['amount']) for r in conn.execute(query, args)), ZERO)


def funds(conn, month):
    month = finance_budget._month(month)
    first = month + '-01'
    last = month + '-31'
    every = finance_budget.categories(conn)
    rows = []
    for category in every:
        if category['type'] != 'sinking' or not category['active'] or category['parent_id'] is not None:
            continue
        ids = [category['id']] + [c['id'] for c in every if c['parent_id'] == category['id']]
        moves = conn.execute('SELECT * FROM finance_fund_movements WHERE category_id=? ORDER BY movement_date, id',
                             (category['id'],)).fetchall()
        opening = next((m for m in moves if m['kind'] == 'opening'), None)
        added = ZERO
        month_added = ZERO
        for move in moves:
            if move['kind'] == 'opening':
                continue
            value = Decimal(move['amount']) * (-1 if move['kind'] == 'release' else 1)
            added += value
            if first <= move['movement_date'] <= last:
                month_added += value
        spent_total = balance = None
        if opening is not None:
            spent_total = _spending(conn, ids, start=opening['movement_date'])
            balance = Decimal(opening['amount']) + added - spent_total
        rows.append(dict(id=category['id'], name=category['name'], setup_needed=opening is None,
                         opened_on=opening['movement_date'] if opening else None, balance=balance, spent_total=spent_total,
                         month_spent=_spending(conn, ids, start=first, end=last), month_added=month_added,
                         notes=category['notes']))
    return rows


def movements(conn, category_id=None, limit=50):
    query = ('SELECT m.*, c.name AS fund FROM finance_fund_movements m JOIN finance_budget_categories c ON c.id=m.category_id')
    args = []
    if category_id is not None:
        query += ' WHERE m.category_id=?'
        args.append(category_id)
    query += ' ORDER BY m.movement_date DESC, m.id DESC LIMIT ?'
    return [dict(r, amount=Decimal(r['amount'])) for r in conn.execute(query, args + [limit])]


def reserve_status(conn, now=None):
    now = _utc(now)
    currency = finance_budget.primary_currency(conn)
    holds = finance_budget.money_setting(conn, 'reserve_holds') or ZERO
    allocated = sum((f['balance'] for f in funds(conn, now.strftime('%Y-%m')) if f['balance'] and f['balance'] > 0), ZERO)
    rows = conn.execute(
        "SELECT a.id, a.balance, a.balance_at, a.missing, a.source, a.currency, "
        "COALESCE(NULLIF(a.nickname,''),NULLIF(a.provider_name,''),a.label) AS name FROM finance_accounts a "
        "JOIN finance_budget_accounts b ON b.account_id=a.id WHERE b.role IN ('savings','reserve') AND a.currency=?",
        (currency,)).fetchall()
    base = dict(backing=None, allocated=allocated, holds=holds, unallocated=None, accounts=[r['name'] for r in rows])
    if not rows:
        return dict(base, state='unknown', reason='Choose which accounts hold reserve cash (role Savings or Reserve) in Budget settings.')
    for row in rows:
        if row['missing']:
            return dict(base, state='unknown', reason=f"{row['name']} is missing from the latest sync.")
        window = MANUAL_STALE_AFTER if row['source'] == 'manual' else STALE_AFTER
        if now - _parse_iso(row['balance_at']) > window:
            return dict(base, state='unknown', reason=f"{row['name']} balance is out of date.")
    backing = sum((Decimal(r['balance']) for r in rows), ZERO).quantize(Decimal('0.01'))
    unallocated = backing - allocated - holds
    state = 'shortfall' if unallocated < 0 else 'ok'
    reason = (f'Fund allocations and holds exceed reserve cash by {money_text(-unallocated)}.' if state == 'shortfall'
              else 'Fund allocations are backed by reserve cash.')
    return dict(base, backing=backing, unallocated=unallocated, state=state, reason=reason)

