"""Savings progress: planned savings against money actually added to savings accounts.

Actual savings is the net posted activity on accounts whose budget role is Savings or
Reserve (deposits, transfers in, interest, minus withdrawals). Fund contributions are
allocations inside cash the household already has, so they are reported next to
savings but never counted as savings.
"""
from decimal import Decimal

import finance_budget
from finance_ledger import _display
from finance_ledger_math import _missing_ranges, _month_bounds

ZERO = Decimal('0.00')


def _money(value):
    return '$' + format(value.quantize(Decimal('0.01')), ',f')


def progress(conn, month, today):
    start, end = _month_bounds(month)
    currency = finance_budget.primary_currency(conn)
    names = _display(conn)
    targets = [finance_budget.target_for(conn, c['id'], month) for c in finance_budget.categories(conn, active_only=True)
               if c['type'] == 'savings']
    targets = [t for t in targets if t]
    planned = sum((t['amount'] for t in targets), ZERO) if targets else None
    fund_allocations = ZERO
    for move in conn.execute("SELECT kind, amount FROM finance_fund_movements WHERE kind != 'opening' AND movement_date BETWEEN ? AND ?",
                             (start.isoformat(), end.isoformat())):
        fund_allocations += Decimal(move['amount']) * (-1 if move['kind'] == 'release' else 1)
    accounts = [r['account_id'] for r in conn.execute(
        "SELECT b.account_id FROM finance_budget_accounts b JOIN finance_accounts a ON a.id=b.account_id "
        "WHERE b.role IN ('savings','reserve') AND a.currency=? ORDER BY b.account_id", (currency,))]
    result = dict(planned=planned, actual=None, fund_allocations=fund_allocations,
                  accounts=[names.get(a, '') for a in accounts])
    window_end = min(end, today)
    if not accounts:
        return dict(result, state='unknown', message='Choose which accounts hold savings (role Savings or Reserve) in Budget settings.')
    if start > today or any(_missing_ranges(conn, a, start, window_end) for a in accounts):
        message = ('Savings accounts need imported transactions for this month before savings can be measured.'
                   if planned is not None else 'Set a monthly savings goal on the Savings category.')
        return dict(result, state='unknown', message=message)
    marks = ','.join('?' * len(accounts))
    actual = sum((Decimal(r['amount']) for r in conn.execute(
        f"SELECT amount FROM finance_txns WHERE account_id IN ({marks}) AND status='posted' AND txn_date BETWEEN ? AND ?",
        accounts + [start.isoformat(), end.isoformat()])), ZERO)
    result['actual'] = actual
    if planned is None:
        return dict(result, state='unknown', message=f'Added {_money(actual)} to savings. Set a monthly savings goal to compare.')
    if actual >= planned:
        return dict(result, state='good', message=f'Saved {_money(actual)} of {_money(planned)} planned.')
    return dict(result, state='attention', message=f'Saved {_money(actual)} of {_money(planned)} planned.')
