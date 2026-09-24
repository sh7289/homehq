"""The household dashboard: the budget path and the cash path side by side.

Each of the six sustainability dimensions gets its own status (good, attention
or unknown) so a healthy result in one never hides a problem or an unknown in another.
Exceptions are reserved for real financial risks, not small overspends.
"""
from decimal import Decimal

import finance_cashflow
import finance_funds
from finance_budget import money_text, primary_currency
from finance_ledger import _display
from finance_ledger_math import month_summary
from finance_store import MANUAL_STALE_AFTER, STALE_AFTER, _parse_iso, _utc

ZERO = Decimal('0.00')
LABELS = [('affordability', 'Operating affordability'), ('discretionary', 'Discretionary spending'),
          ('reserve', 'Reserve funding'), ('savings', 'Savings progress'), ('liquidity', 'Near-term cash'),
          ('reliability', 'Data reliability')]


def _money(value):
    return '$' + format(value.quantize(Decimal('0.01')), ',f')


def dashboard(conn, month, today, now=None):
    now = _utc(now)
    summary = month_summary(conn, month, today)
    allowances = summary['allowances']
    funds = finance_funds.funds(conn, month)
    reserve = finance_funds.reserve_status(conn, now=now)
    forecast = finance_cashflow.forecast(conn, today, now=now)
    targeted = [a for a in allowances if not a['no_target']]
    numbers = dict(remaining_budget=sum((a['remaining'] for a in targeted), ZERO) if targeted else None,
                   available_cash=forecast['available_cash'], projected_cash=forecast['end_balance'])
    status = {}

    plan = summary['plan']
    if plan['label'] == 'incomplete':
        status['affordability'] = ('unknown', f"The plan can't be judged yet: {len(plan['missing'])} lines have no amount.")
    elif plan['surplus'] < 0:
        status['affordability'] = ('attention', f"Planned costs exceed planned income by {_money(-plan['surplus'])}.")
    else:
        status['affordability'] = ('good', f"The plan leaves {_money(plan['surplus'])} a month ({plan['label']}).")

    over = [a['name'] for a in targeted if a['remaining'] < 0]
    if summary['quality'] == 'insufficient':
        status['discretionary'] = ('unknown', "Some accounts are missing transactions this month, so allowances can't be judged.")
    elif over:
        status['discretionary'] = ('attention', 'Over budget: ' + ', '.join(over) + '.')
    elif not targeted:
        status['discretionary'] = ('unknown', 'No allowances are set for this month.')
    else:
        note = ' (provisional until everything is reviewed)' if summary['quality'] == 'provisional' else ''
        status['discretionary'] = ('good', 'Every allowance is within its budget' + note + '.')

    status['reserve'] = ({'ok': 'good', 'shortfall': 'attention', 'unknown': 'unknown'}[reserve['state']], reserve['reason'])
    status['savings'] = ('unknown', 'A savings plan arrives in a later update.')

    if forecast['below_minimum']:
        low = forecast['below_minimum']
        status['liquidity'] = ('attention', f"Checking is projected to reach {_money(low['balance'])} on {low['date']}.")
    elif forecast['state'] == 'unknown':
        status['liquidity'] = ('unknown', forecast['reasons'][0])
    elif forecast['needs_date']:
        status['liquidity'] = ('unknown', f"{len(forecast['needs_date'])} bills or paydays need a date before the forecast is complete.")
    else:
        status['liquidity'] = ('good', f"Checking stays above the minimum through {forecast['end_date']}.")

    reliability = {'complete': 'good', 'provisional': 'attention', 'insufficient': 'unknown'}[summary['quality']]
    status['reliability'] = (reliability, summary['quality_reasons'][0] if summary['quality_reasons']
                             else 'Every included account is covered and reviewed.')

    dimensions = [dict(key=key, label=label, state=status[key][0], message=status[key][1]) for key, label in LABELS]
    return dict(summary=summary, allowances=allowances, funds=funds, reserve=reserve, forecast=forecast,
                numbers=numbers, dimensions=dimensions, exceptions=_exceptions(conn, forecast, reserve, now))


def _exceptions(conn, forecast, reserve, now):
    found = []
    low = forecast['below_minimum']
    if low:
        floor = 'zero' if forecast['minimum'] == 0 else f"your {_money(forecast['minimum'])} minimum"
        found.append(f"Checking is projected to fall to {_money(low['balance'])} on {low['date']}, below {floor}.")
    for payment in forecast['uncovered_card_payments']:
        found.append(f"{payment['label']} on {payment['date']} ({_money(-payment['amount'])}) would overdraw checking.")
    if reserve['state'] == 'shortfall':
        found.append(f"Fund allocations exceed reserve cash by {money_text(-reserve['unallocated'])}.")
    names = _display(conn)
    for row in conn.execute(
            "SELECT a.id, a.balance_at, a.missing, a.source FROM finance_accounts a JOIN finance_budget_accounts b "
            "ON b.account_id=a.id WHERE b.included=1 AND b.role IN ('checking','card') AND a.currency=? ORDER BY a.id",
            (primary_currency(conn),)):
        name = names.get(row['id'], 'An account')
        window = MANUAL_STALE_AFTER if row['source'] == 'manual' else STALE_AFTER
        if row['missing']:
            found.append(f'{name} is missing from the latest bank sync.')
        elif now - _parse_iso(row['balance_at']) > window:
            found.append(f"{name} balance hasn't updated since {row['balance_at'][:10]}.")
    return found
