"""Budget accounting for one month: what was earned or consumed, by category.

This is deliberately separate from cash forecasting. Budget accounting counts a
purchase in the month it happened, whatever account paid for it, and never counts
transfers or card payments. Every figure here comes with the coverage and review
state that qualifies it, and an unknown plan line stays unknown instead of $0.
"""
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import finance_budget
from finance_ledger import MOVEMENT_KINDS, _display

ZERO = Decimal('0.00')
SPENDING_KINDS = ('expense', 'refund', 'reimbursement')
PLAN_OUTFLOW_TYPES = ('operating', 'capped', 'sinking', 'savings')
BASIS_STRENGTH = {'planning': 0, 'estimate': 1, 'historical': 2}
# Allocation lines in the built-in excluded category are kept on record but never counted.
COUNTED = 'a.category_id NOT IN (SELECT id FROM finance_budget_categories WHERE excluded=1)'


def _month_bounds(month):
    start = date.fromisoformat(finance_budget._month(month) + '-01')
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return start, end


def _missing_ranges(conn, account_id, start, end):
    if start > end:
        return []
    ranges = [(date.fromisoformat(r['start_date']), date.fromisoformat(r['end_date'])) for r in conn.execute(
        'SELECT start_date, end_date FROM finance_coverage WHERE account_id=?', (account_id,))]
    missing, run, day = [], None, start
    while day <= end:
        covered = any(low <= day <= high for low, high in ranges)
        if not covered and run is None:
            run = day
        if covered and run is not None:
            missing.append((run.isoformat(), (day - timedelta(days=1)).isoformat()))
            run = None
        day += timedelta(days=1)
    if run is not None:
        missing.append((run.isoformat(), end.isoformat()))
    return missing


def month_summary(conn, month, today):
    own = not conn.in_transaction
    if own:
        conn.execute('BEGIN')
    try:
        return _summary(conn, month, today)
    finally:
        if own:
            conn.rollback()


def _summary(conn, month, today):
    start, end = _month_bounds(month)
    # Today is still posting, so a month is complete once covered through yesterday.
    window_end = min(end, today - timedelta(days=1))
    currency = finance_budget.primary_currency(conn)
    names = _display(conn)
    reasons = []

    included = [r['account_id'] for r in conn.execute(
        'SELECT account_id, role FROM finance_budget_accounts WHERE included=1 ORDER BY account_id')]
    roles = {r['account_id']: r['role'] for r in conn.execute('SELECT account_id, role FROM finance_budget_accounts')}
    coverage = [dict(account_id=a, name=names.get(a, ''), role=roles[a], missing=_missing_ranges(conn, a, start, window_end))
                for a in included]
    insufficient = False
    if start > today:
        insufficient = True
        reasons.append('Month has not started.')
    if not included:
        insufficient = True
        reasons.append('No accounts are included in budgeting.')
    for item in coverage:
        if item['missing']:
            insufficient = True
            reasons.append(f"{item['name']} has no transactions recorded for part of this month.")

    txns = conn.execute("SELECT * FROM finance_txns WHERE txn_date BETWEEN ? AND ? AND status IN ('posted','pending')",
                        (start.isoformat(), end.isoformat())).fetchall()
    allocations = {}
    for line in conn.execute(
            "SELECT a.* FROM finance_txn_allocations a JOIN finance_txns t ON t.id=a.txn_id "
            "WHERE t.txn_date BETWEEN ? AND ? AND t.status IN ('posted','pending')", (start.isoformat(), end.isoformat())):
        allocations.setdefault(line['txn_id'], []).append(line)

    direct = category_spending(conn, month)
    excluded = {r[0] for r in conn.execute('SELECT id FROM finance_budget_categories WHERE excluded=1')}
    roles = {r['account_id']: r['role'] for r in conn.execute('SELECT account_id, role FROM finance_budget_accounts')}
    funding = {'hsa': ZERO, 'everyday': ZERO}
    for line in conn.execute(
            "SELECT t.account_id, a.amount FROM finance_txn_allocations a JOIN finance_txns t ON t.id=a.txn_id "
            "WHERE t.txn_date BETWEEN ? AND ? AND t.status IN ('posted','pending') AND t.kind IN ('expense','refund','reimbursement') "
            f"AND t.currency=? AND {COUNTED}", (start.isoformat(), end.isoformat(), currency)):
        funding['hsa' if roles.get(line['account_id']) == 'hsa' else 'everyday'] -= Decimal(line['amount'])
    known = {'posted': sum((d['posted'] for d in direct.values()), ZERO),
             'pending': sum((d['pending'] for d in direct.values()), ZERO)}
    uncategorized = {'count': 0, 'amount': ZERO}
    inflows = {'count': 0, 'amount': ZERO}
    other = {}
    income = ZERO
    linked = {r[0] for r in conn.execute(
        "SELECT from_txn_id FROM finance_txn_links WHERE kind IN ('transfer','card_payment') "
        "UNION SELECT to_txn_id FROM finance_txn_links WHERE kind IN ('transfer','card_payment')")}
    unreviewed = pending = duplicates = unmatched = 0
    for txn in txns:
        amount, lines = Decimal(txn['amount']), allocations.get(txn['id'], [])
        if txn['kind'] in MOVEMENT_KINDS:
            # A one-sided transfer removes money from spending with no evidence of where it went.
            unmatched += txn['id'] not in linked
            continue
        if txn['currency'] != currency:
            if txn['kind'] in SPENDING_KINDS or (not lines and amount < 0):
                other[txn['currency']] = other.get(txn['currency'], ZERO) - amount
            continue
        unreviewed += txn['review'] != 'accepted'
        pending += txn['status'] == 'pending'
        duplicates += txn['possible_duplicate_of'] is not None
        bucket = txn['status']
        if not lines:
            if amount < 0:
                uncategorized['count'] += 1
                uncategorized['amount'] -= amount
                known[bucket] -= amount
                funding['hsa' if roles.get(txn['account_id']) == 'hsa' else 'everyday'] -= amount
            else:
                inflows['count'] += 1
                inflows['amount'] += amount
            continue
        if txn['kind'] == 'income':
            income += sum((Decimal(line['amount']) for line in lines if line['category_id'] not in excluded), ZERO)

    if not insufficient:
        if uncategorized['count']:
            reasons.append(f"{uncategorized['count']} outflows have no category yet.")
        if unreviewed:
            reasons.append(f'{unreviewed} transactions are not reviewed.')
        if pending:
            reasons.append(f'{pending} pending transactions may still change.')
        if duplicates:
            reasons.append(f'{duplicates} possible duplicates need a decision.')
        if unmatched:
            reasons.append(f'{unmatched} transfers or card payments have no matching other side.')
    quality = 'insufficient' if insufficient else ('provisional' if reasons else 'complete')

    rows, plan = [], {'income': ZERO, 'outflows': ZERO, 'unvalidated': 0, 'missing': [], 'bases': []}
    all_categories = finance_budget.categories(conn)
    children = {}
    for category in all_categories:
        if category['parent_id'] is not None:
            children.setdefault(category['parent_id'], []).append(category)
    targets = {c['id']: finance_budget.target_for(conn, c['id'], month) for c in all_categories}

    for category in all_categories:
        if category['excluded']:
            continue
        spent_slot = direct.get(category['id'], {'posted': ZERO, 'pending': ZERO})
        if not category['active'] and spent_slot == {'posted': ZERO, 'pending': ZERO}:
            continue
        posted, pend = spent_slot['posted'], spent_slot['pending']
        own_target = targets[category['id']] if category['active'] else None
        target_parts = [own_target] if own_target else []
        for child in children.get(category['id'], []):
            child_slot = direct.get(child['id'], {'posted': ZERO, 'pending': ZERO})
            posted += child_slot['posted']
            pend += child_slot['pending']
            if child['active'] and targets[child['id']]:
                target_parts.append(targets[child['id']])
        target = sum((t['amount'] for t in target_parts), ZERO) if target_parts else None
        basis = min((t['basis'] for t in target_parts), key=BASIS_STRENGTH.get) if target_parts else None
        spent = posted + pend
        pct = None
        if target:
            pct = int((spent * 100 / target).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        rows.append(dict(id=category['id'], name=category['name'], parent_id=category['parent_id'], type=category['type'],
                         target=target, basis=basis, target_note=own_target['note'] if own_target else '',
                         posted=posted, pending=pend, spent=spent, pct_used=pct))

        if not category['active']:
            continue
        if own_target and category['type'] in PLAN_OUTFLOW_TYPES + ('income',):
            key = 'income' if category['type'] == 'income' else 'outflows'
            plan[key] += own_target['amount']
            plan['unvalidated'] += own_target['basis'] != 'historical'
        if category['parent_id'] is None and category['type'] in ('operating', 'savings') and not own_target:
            # With subcategories, name each line that has no amount; one known line
            # (tirzepatide) must not make the whole medical category look planned.
            lines = [c for c in children.get(category['id'], []) if c['active']]
            unplanned = [c['name'] for c in lines if not targets[c['id']]] if lines else [category['name']]
            plan['missing'].extend(unplanned)

    if plan['income'] == 0:
        plan['missing'].append('Income')
    label = 'incomplete' if plan['missing'] else ('provisional' if plan['unvalidated'] else 'validated')
    plan = dict(income=plan['income'], outflows=plan['outflows'], surplus=plan['income'] - plan['outflows'],
                label=label, unvalidated=plan['unvalidated'], missing=plan['missing'])

    return dict(month=month, currency=currency, coverage=coverage, allowances=allowances(conn, month, today), quality=quality, quality_reasons=reasons,
                known_spending=dict(posted=known['posted'], pending=known['pending'], total=known['posted'] + known['pending']),
                uncategorized=uncategorized, unreviewed_inflows=inflows,
                funding=dict(funding, total=funding['hsa'] + funding['everyday']), income=income, categories=rows, plan=plan,
                other_currencies={k: v for k, v in other.items() if v})


def category_spending(conn, month):
    """Direct spending per category for the month, net of refunds, primary currency only."""
    start, end = _month_bounds(month)
    result = {}
    for line in conn.execute(
            "SELECT a.category_id, a.amount, t.status FROM finance_txn_allocations a JOIN finance_txns t ON t.id=a.txn_id "
            "WHERE t.txn_date BETWEEN ? AND ? AND t.status IN ('posted','pending') AND t.kind IN ('expense','refund','reimbursement') "
            f"AND t.currency=? AND {COUNTED}", (start.isoformat(), end.isoformat(), finance_budget.primary_currency(conn))):
        slot = result.setdefault(line['category_id'], {'posted': ZERO, 'pending': ZERO})
        slot[line['status']] -= Decimal(line['amount'])
    return result


def _next_month(month):
    year, number = int(month[:4]), int(month[5:])
    return f'{year + number // 12}-{number % 12 + 1:02d}'


def allowances(conn, month, today):
    """Available and remaining amounts for monthly capped categories, with rollover.

    The chain starts at the budget start month (or the requested month, if earlier) and
    restarts after any month without a target, so there is never a fake remaining figure.
    """
    month = finance_budget._month(month)
    start = (finance_budget.get_setting(conn, 'budget_start') or '')[:7]
    months = [month]
    if start and start < month:
        months, cursor = [], start
        while cursor <= month:
            months.append(cursor)
            cursor = _next_month(cursor)
    spending = {m: category_spending(conn, m) for m in months}
    every = finance_budget.categories(conn)
    children = {}
    for category in every:
        if category['parent_id'] is not None:
            children.setdefault(category['parent_id'], []).append(category['id'])
    rows = []
    for category in every:
        if category['type'] != 'capped' or not category['active'] or category['parent_id'] is not None:
            continue
        ids = [category['id']] + children.get(category['id'], [])
        cap = Decimal(category['rollover_cap']) if category['rollover_cap'] else None
        previous = None
        for index, current in enumerate(months):
            target = finance_budget.target_for(conn, category['id'], current)
            spent = sum((spending[current].get(i, {}).get('posted', ZERO) + spending[current].get(i, {}).get('pending', ZERO)
                         for i in ids), ZERO)
            if target is None:
                row = dict(target=None, carry_in=None, available=None, remaining=None, pct_used=None, no_target=True)
                previous = None
            else:
                amount = target['amount']
                carry = ZERO
                if index and previous is not None and category['rollover'] != 'reset':
                    carry = previous if category['rollover'] == 'carry' or cap is None else min(cap, amount + previous) - amount
                available = amount + carry
                remaining = available - spent
                pct = int((spent * 100 / available).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if available > 0 else None
                row = dict(target=amount, carry_in=carry, available=available, remaining=remaining, pct_used=pct, no_target=False)
                previous = remaining
        rows.append(dict(row, id=category['id'], name=category['name'], rollover=category['rollover'], cap=cap, spent=spent))
    return rows
