"""Bonus planner: the household brief's proposed split as an editable suggestion.

Nothing here runs by itself. A bonus is counted when it arrives, the split is only a
proposal, and each fund line is recorded only when someone ticks it. The savings line
is a bank transfer the household makes; it shows up in savings progress once imported.
"""
from datetime import timedelta
from decimal import Decimal

import finance_funds
from finance_book import _transaction
from finance_budget import parse_money
from finance_store import FinanceStoreError

DEFAULT_SHARES = [('savings', 'Liquid savings / future house', 35), ('Travel', 'Travel', 25),
                  ('Gifts and Christmas', 'Gifts and holidays', 15), ('Celebrations', 'Couple/family celebrations', 10),
                  ('Home/car/pet reserve', 'Home/car/pet reserve', 15)]
CENT = Decimal('0.01')


def propose(amount, shares):
    if not isinstance(amount, Decimal) or amount <= 0:
        raise FinanceStoreError('Enter the bonus amount you actually received.')
    if len(shares) != len(DEFAULT_SHARES) or any(s < 0 for s in shares) or sum(shares) != 100:
        raise FinanceStoreError('The shares must be zero or more and add up to 100.')
    lines, assigned = [], Decimal('0')
    for index, ((key, label, _), percent) in enumerate(zip(DEFAULT_SHARES, shares)):
        value = (amount - assigned) if index == len(shares) - 1 else (amount * percent / 100).quantize(CENT)
        assigned += value
        lines.append(dict(key=key, label=label, percent=percent, amount=value))
    return lines


def record(conn, *, lines, actor, today):
    """Record fund contributions for the ticked lines, all or nothing. Returns how many were recorded."""
    funds = finance_funds.funds(conn, today.strftime('%Y-%m'), today=today)
    fund_ids = {f['name']: f['id'] for f in funds}
    opened = {f['name'] for f in funds if not f['setup_needed']}
    count = 0
    with _transaction(conn):
        for line in lines:
            key = line.get('key')
            if key == 'savings':
                continue
            if key not in fund_ids:
                raise FinanceStoreError('Choose one of the proposed funds.')
            if key not in opened:
                raise FinanceStoreError(f'Record a starting amount for {key} first.')
            finance_funds.record_movement(conn, category_id=fund_ids[key], kind='contribution',
                                          amount=str(parse_money(str(line.get('amount', '')), signed=False)),
                                          movement_date=today.isoformat(), note='Bonus allocation', actor=actor, today=today)
            count += 1
    return count


def recent_income(conn, today, days=120):
    since = (today - timedelta(days=days)).isoformat()
    return [dict(id=r['id'], date=r['txn_date'], merchant=r['merchant'], amount=Decimal(r['amount'])) for r in conn.execute(
        "SELECT id, txn_date, merchant, amount FROM finance_txns WHERE kind='income' AND status='posted' AND txn_date >= ? "
        'ORDER BY txn_date DESC, id DESC LIMIT 20', (since,))]
