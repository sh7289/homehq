"""Cash path: what will enter or leave checking, and when.

This module deliberately shares no aggregation with budget accounting. A card
purchase never appears here; only the card *payment* that settles it does. Pending
items, categories and targets never touch the forecast. Starting cash is the synced
checking balance, so anything dated on or before that balance date is not subtracted
again.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

import finance_budget
import finance_recurring
from finance_ledger import _display
from finance_store import MANUAL_STALE_AFTER, STALE_AFTER, _parse_iso, _utc

ZERO = Decimal('0.00')
RECENT_PAST = timedelta(days=7)


def _window_end(today):
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    return max(last, today + timedelta(days=35))


def forecast(conn, today, now=None):
    now = _utc(now)
    currency = finance_budget.primary_currency(conn)
    minimum = finance_budget.money_setting(conn, 'checking_minimum') or ZERO
    end = _window_end(today)
    names = _display(conn)
    rows = conn.execute(
        "SELECT a.id, a.balance, a.balance_at, a.missing, a.source FROM finance_accounts a JOIN finance_budget_accounts b "
        "ON b.account_id=a.id WHERE b.included=1 AND b.role='checking' AND a.currency=? ORDER BY a.id", (currency,)).fetchall()
    commitments = finance_recurring.commitments(conn)
    needs = [c['name'] for c in commitments if c['status'] == 'active' and (not c['next_date'] or not c['account_id'])]
    result = dict(state='ok', reasons=[], accounts=[names.get(r['id'], '') for r in rows], as_of=None, start_balance=None,
                  minimum=minimum, available_cash=None, end_date=end.isoformat(), events=[], lowest=None,
                  below_minimum=None, uncovered_card_payments=[], end_balance=None, past_due=[], needs_date=needs)
    if not rows:
        return dict(result, state='unknown', reasons=['Include a checking account in Budget settings to see a forecast.'])
    for row in rows:
        if row['missing']:
            return dict(result, state='unknown', reasons=[f"{names.get(row['id'], 'A checking account')} is missing from the latest sync."])
        window = MANUAL_STALE_AFTER if row['source'] == 'manual' else STALE_AFTER
        if now - _parse_iso(row['balance_at']) > window:
            result['state'] = 'provisional'
            result['reasons'].append(f"{names.get(row['id'], 'A checking account')} balance is out of date.")
    ids = {r['id'] for r in rows}
    as_of = min(_parse_iso(r['balance_at']).date() for r in rows)
    start = sum((Decimal(r['balance']) for r in rows), ZERO).quantize(Decimal('0.01'))
    events, past = [], []

    for commitment in commitments:
        if commitment['account_id'] not in ids:
            continue
        for day in finance_recurring.occurrences(commitment, as_of - RECENT_PAST, end):
            amount = commitment['amount'] if commitment['direction'] == 'in' else -commitment['amount']
            item = dict(date=day.isoformat(), label=commitment['name'], amount=amount,
                        kind='income' if commitment['direction'] == 'in' else 'bill')
            (past if day <= as_of else events).append(item)

    for payment in conn.execute(
            "SELECT p.* FROM finance_payments p WHERE p.status IN ('planned','scheduled') AND p.currency=? AND p.payment_date <= ? "
            "AND NOT EXISTS (SELECT 1 FROM finance_txn_links l WHERE l.payment_id=p.id)", (currency, end.isoformat())):
        if payment['funding_id'] not in ids:
            continue
        item = dict(date=payment['payment_date'], label='Card payment: ' + names.get(payment['card_id'], 'card'),
                    amount=-Decimal(payment['amount']), kind='card_payment')
        (past if date.fromisoformat(payment['payment_date']) <= as_of else events).append(item)

    events.sort(key=lambda e: (e['date'], e['amount'] < 0))
    running, lowest, below = start, None, None
    for event in events:
        running += event['amount']
        event['running'] = running
        if lowest is None or running < lowest['balance']:
            lowest = dict(date=event['date'], balance=running)
        if below is None and running < minimum:
            below = dict(date=event['date'], balance=running)
    if needs:
        result['state'] = 'provisional'
        result['reasons'].append(f'{len(needs)} commitments need a date or account before they can be forecast.')
    if past:
        result['reasons'].append(f'{len(past)} expected items are dated on or before the balance date. Confirm they went through.')
    past.sort(key=lambda e: e['date'])
    return dict(result, as_of=as_of.isoformat(), start_balance=start, available_cash=start - minimum, events=events,
                lowest=lowest, below_minimum=below, end_balance=running, past_due=past,
                uncovered_card_payments=[e for e in events if e['kind'] == 'card_payment' and e['running'] < 0])
