"""CSV exports of household budgeting data.

Exports leave the private store, so they carry only what a person needs in a
spreadsheet: no source hashes, raw import columns or account identifiers. Text cells
that a spreadsheet would run as a formula get a leading apostrophe.
"""
import csv
import io

import finance_budget
from finance_budget import money_text
from finance_ledger import _display
from finance_ledger_math import _month_bounds, month_summary

FORMULA_START = ('=', '+', '-', '@', '\t', '\r')
TRANSACTION_COLUMNS = ['id', 'date', 'posted_date', 'account', 'amount', 'currency', 'kind', 'status', 'review',
                       'merchant', 'category', 'person', 'allocation_amount', 'linked_to', 'note']
CATEGORY_COLUMNS = ['category', 'parent', 'type', 'target', 'basis', 'posted', 'pending', 'spent', 'remaining']


def _cell(value):
    text = '' if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def _write(columns, rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator='\r\n')
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def transactions_csv(conn, month):
    start, end = _month_bounds(month)
    names = _display(conn)
    categories = {r['id']: r['name'] for r in conn.execute('SELECT id, name FROM finance_budget_categories')}
    links = {}
    for link in conn.execute('SELECT kind, from_txn_id, to_txn_id FROM finance_txn_links ORDER BY id'):
        links.setdefault(link['from_txn_id'], []).append(f"{link['kind']}:{link['to_txn_id']}")
        links.setdefault(link['to_txn_id'], []).append(f"{link['kind']}:{link['from_txn_id']}")
    rows = []
    for txn in conn.execute('SELECT * FROM finance_txns WHERE txn_date BETWEEN ? AND ? ORDER BY txn_date, id',
                            (start.isoformat(), end.isoformat())):
        base = dict(id=txn['id'], date=txn['txn_date'], posted_date=txn['posted_date'] or '',
                    account=_cell(names.get(txn['account_id'], '')), amount=txn['amount'], currency=txn['currency'],
                    kind=txn['kind'], status=txn['status'], review=txn['review'], merchant=_cell(txn['merchant']),
                    linked_to=' '.join(links.get(txn['id'], [])), note=_cell(txn['note']))
        lines = conn.execute('SELECT * FROM finance_txn_allocations WHERE txn_id=? ORDER BY id', (txn['id'],)).fetchall()
        if not lines:
            rows.append(dict(base, category='', person='', allocation_amount=''))
        for line in lines:
            rows.append(dict(base, category=_cell(categories.get(line['category_id'], '')), person=_cell(line['person']),
                             allocation_amount=line['amount']))
    return _write(TRANSACTION_COLUMNS, rows)


def categories_csv(conn, month, today):
    summary = month_summary(conn, month, today)
    parents = {r['id']: r['name'] for r in finance_budget.categories(conn)}
    remaining = {a['id']: a['remaining'] for a in summary['allowances']}
    rows = []
    for row in summary['categories']:
        rows.append(dict(category=_cell(row['name']), parent=_cell(parents.get(row['parent_id'], '')), type=row['type'],
                         target=money_text(row['target']) if row['target'] is not None else '', basis=row['basis'] or '',
                         posted=money_text(row['posted']), pending=money_text(row['pending']), spent=money_text(row['spent']),
                         remaining=money_text(remaining[row['id']]) if remaining.get(row['id']) is not None else ''))
    return _write(CATEGORY_COLUMNS, rows)
