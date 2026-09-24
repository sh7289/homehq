"""Bank/card CSV import: stage the file, map its columns, preview, then commit.

Staged cells live in the private DB only until the batch is committed, discarded,
or a day old; the uploaded file itself is never written anywhere. Committed rows
become canonical transactions whose source records keep masked columns and a
fingerprint, so re-importing the same or an overlapping export adds only rows
that were not already imported, while identical purchases inside one file are
kept apart by their occurrence number. Error messages never echo file content.
"""
import csv
import hashlib
import io
import json
from datetime import datetime, timedelta

import finance_budget
import finance_ledger
from finance_book import _text, _transaction
from finance_budget import mask_digits, money_text, parse_money
from finance_store import FinanceStoreError, _iso, _utc

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 30
MAX_CELL = 500
STAGE_TTL = timedelta(days=1)
SIGNS = ('outflow_negative', 'outflow_positive', 'debit_credit')
FIELDS = ('date', 'description', 'amount', 'debit', 'credit', 'posted_date', 'status', 'id')
DATE_FORMATS = ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y')
GUESSES = {
    'date': ('date', 'transaction date', 'trans date', 'trans. date'),
    'description': ('description', 'payee', 'merchant', 'name', 'memo'),
    'amount': ('amount',),
    'debit': ('debit', 'withdrawal', 'withdrawals'),
    'credit': ('credit', 'deposit', 'deposits'),
    'posted_date': ('post date', 'posted date', 'posting date'),
    'status': ('status',),
    'id': ('id', 'transaction id', 'reference'),
}
TOO_LARGE = 'The file is too large (limit 2 MB / 5,000 rows).'
UNREADABLE = 'The file could not be read as a CSV.'


def _batch(conn, batch_id, state=None):
    batch_id = finance_ledger._id(batch_id, 'Import was not found.')
    row = conn.execute('SELECT * FROM finance_import_batches WHERE id=?', (batch_id,)).fetchone()
    if row is None or (state and row['state'] != state):
        raise FinanceStoreError('Import was not found.')
    return row


def stage(conn, *, account_id, data, label, actor, now=None):
    now = _utc(now)
    actor, label = _text(actor, True), _text(label or 'Import', True)
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise FinanceStoreError(TOO_LARGE)
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = data.decode('cp1252', errors='replace')
    try:
        parsed = list(csv.reader(io.StringIO(text, newline='')))
    except csv.Error:
        raise FinanceStoreError(UNREADABLE) from None
    rows = [(number, [cell.strip() for cell in row]) for number, row in enumerate(parsed, start=1)
            if any(cell.strip() for cell in row)]
    if len(rows) - 1 > MAX_ROWS:
        raise FinanceStoreError(TOO_LARGE)
    if len(rows) < 2 or any(len(cells) > MAX_COLUMNS or any(len(c) > MAX_CELL for c in cells) for _, cells in rows):
        raise FinanceStoreError(UNREADABLE)
    with _transaction(conn):
        if account_id not in finance_budget.account_settings(conn):
            raise FinanceStoreError('Select an account set up for budgeting.')
        cutoff = _iso(now - STAGE_TTL)
        conn.execute("DELETE FROM finance_import_batches WHERE state='staged' AND created_at < ?", (cutoff,))
        batch_id = conn.execute(
            "INSERT INTO finance_import_batches (account_id,state,label,imported_by,created_at,header_json,row_count) "
            "VALUES (?,'staged',?,?,?,?,?)", (account_id, label, actor, _iso(now), json.dumps(rows[0][1]), len(rows) - 1)).lastrowid
        conn.executemany('INSERT INTO finance_staged_rows (batch_id, idx, cells_json) VALUES (?,?,?)',
                         [(batch_id, number, json.dumps(cells)) for number, cells in rows[1:]])
    return batch_id


def header(conn, batch_id):
    return json.loads(_batch(conn, batch_id)['header_json'])


def _valid_mapping(mapping, columns):
    if not isinstance(mapping, dict) or mapping.get('sign') not in SIGNS or set(mapping) - set(FIELDS) - {'sign'}:
        return False
    for field in FIELDS:
        value = mapping.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < columns):
            return False
    money = ('debit', 'credit') if mapping['sign'] == 'debit_credit' else ('amount',)
    return all(mapping.get(field) is not None for field in ('date', 'description') + money)


def default_mapping(conn, batch_id):
    batch = _batch(conn, batch_id)
    columns = json.loads(batch['header_json'])
    saved = finance_budget.account_settings(conn).get(batch['account_id'], {}).get('csv_profile', {})
    saved = {k: v for k, v in saved.items() if k in FIELDS or k == 'sign'}
    if _valid_mapping(saved, len(columns)):
        return saved
    names = [c.strip().lower() for c in columns]
    mapping = {}
    for field, options in GUESSES.items():
        for index, name in enumerate(names):
            if name in options:
                mapping[field] = index
                break
    has_amount = 'amount' in mapping
    mapping['sign'] = 'outflow_negative' if has_amount or not ('debit' in mapping and 'credit' in mapping) else 'debit_credit'
    return mapping


def _date(text):
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def normalize(conn, batch_id, mapping):
    batch = _batch(conn, batch_id)
    columns = json.loads(batch['header_json'])
    if not _valid_mapping(mapping, len(columns)):
        raise FinanceStoreError('Choose which columns hold the date, description and amount.')
    account_id = batch['account_id']
    rows, errors, seen = [], [], {}
    for staged in conn.execute('SELECT idx, cells_json FROM finance_staged_rows WHERE batch_id=? ORDER BY idx', (batch['id'],)):
        cells = json.loads(staged['cells_json'])

        def cell(field):
            index = mapping.get(field)
            return cells[index] if index is not None and index < len(cells) else ''

        number = staged['idx']
        day = _date(cell('date'))
        if day is None:
            errors.append(f'Row {number}: date not recognized.')
            continue
        try:
            if mapping['sign'] == 'debit_credit':
                debit, credit = cell('debit'), cell('credit')
                if not debit and not credit:
                    raise FinanceStoreError('missing')
                amount = (abs(parse_money(credit)) if credit else 0) - (abs(parse_money(debit)) if debit else 0)
            else:
                amount = parse_money(cell('amount'))
                if mapping['sign'] == 'outflow_positive':
                    amount = -amount
        except FinanceStoreError:
            errors.append(f'Row {number}: amount not recognized.')
            continue
        if amount == 0:
            continue
        posted = None
        if cell('posted_date'):
            posted = _date(cell('posted_date'))
            if posted is None:
                errors.append(f'Row {number}: posted date not recognized.')
                continue
        description = mask_digits(cell('description'))
        raw_id = cell('id')
        fingerprint = finance_ledger.import_fingerprint(account_id, day, amount, description)
        seen[fingerprint] = seen.get(fingerprint, 0) + 1
        rows.append(dict(idx=number, txn_date=day, posted_date=posted, amount=amount.quantize(finance_budget.CENT),
                         description=description, status='pending' if 'pend' in cell('status').lower() else 'posted',
                         source_txn_hash=hashlib.sha256(f'{account_id}|{raw_id}'.encode()).hexdigest() if raw_id else None,
                         fingerprint=fingerprint, occurrence=seen[fingerprint]))
    return rows, errors


def _duplicate(conn, account_id, row):
    if row['source_txn_hash']:
        return conn.execute("SELECT 1 FROM finance_source_records WHERE account_id=? AND source='csv' AND source_txn_hash=?",
                            (account_id, row['source_txn_hash'])).fetchone() is not None
    return conn.execute("SELECT 1 FROM finance_source_records WHERE account_id=? AND source='csv' AND fingerprint=? "
                        'AND occurrence=?', (account_id, row['fingerprint'], row['occurrence'])).fetchone() is not None


def _possible_manual_duplicate(conn, account_id, row):
    low = (datetime.fromisoformat(row['txn_date']) - timedelta(days=finance_ledger.DUPLICATE_WINDOW)).date().isoformat()
    high = (datetime.fromisoformat(row['txn_date']) + timedelta(days=finance_ledger.DUPLICATE_WINDOW)).date().isoformat()
    return conn.execute("SELECT 1 FROM finance_txns t JOIN finance_source_records s ON s.txn_id=t.id WHERE s.source='manual' "
                        "AND t.account_id=? AND t.amount=? AND t.txn_date BETWEEN ? AND ? AND t.status NOT IN ('void','replaced')",
                        (account_id, money_text(row['amount']), low, high)).fetchone() is not None


def preview(conn, batch_id, mapping):
    batch = _batch(conn, batch_id, 'staged')
    rows, errors = normalize(conn, batch['id'], mapping)
    new = [r for r in rows if not _duplicate(conn, batch['account_id'], r)]
    dates = [r['txn_date'] for r in rows]
    return dict(new=len(new), duplicate=len(rows) - len(new), errors=errors,
                possible_duplicate=sum(_possible_manual_duplicate(conn, batch['account_id'], r) for r in new),
                first_date=min(dates) if dates else None, last_date=max(dates) if dates else None,
                sample=[dict(txn_date=r['txn_date'], description=r['description'], amount=r['amount'], status=r['status'])
                        for r in new[:20]])


def commit(conn, batch_id, mapping, *, period_start, period_end, actor, now=None):
    now = _utc(now)
    actor = _text(actor, True)
    start, end = finance_budget._day(period_start), finance_budget._day(period_end)
    with _transaction(conn):
        batch = _batch(conn, batch_id, 'staged')
        account_id = batch['account_id']
        rows, errors = normalize(conn, batch['id'], mapping)
        if errors:
            raise FinanceStoreError('Fix the rows listed before importing.')
        if not rows:
            raise FinanceStoreError('The file has no transactions to import.')
        if start > end or end > now.date() or not all(start.isoformat() <= r['txn_date'] <= end.isoformat() for r in rows):
            raise FinanceStoreError('The coverage period must include every imported date and cannot extend past today.')
        new = duplicate = 0
        for row in rows:
            if _duplicate(conn, account_id, row):
                duplicate += 1
                continue
            occurrence = row['occurrence']
            while conn.execute("SELECT 1 FROM finance_source_records WHERE account_id=? AND source='csv' AND fingerprint=? "
                               'AND occurrence=?', (account_id, row['fingerprint'], occurrence)).fetchone():
                occurrence += 1
            columns = dict(date=row['txn_date'], description=row['description'], amount=money_text(row['amount']),
                           status=row['status'])
            if row['posted_date']:
                columns['posted_date'] = row['posted_date']
            finance_ledger.create_txn(conn, account_id=account_id, txn_date=row['txn_date'], amount=row['amount'],
                                      description=row['description'], actor=actor, status=row['status'],
                                      posted_date=row['posted_date'], source='csv', batch_id=batch['id'],
                                      fingerprint=row['fingerprint'], occurrence=occurrence,
                                      source_txn_hash=row['source_txn_hash'], columns=columns)
            new += 1
        finance_budget.save_csv_profile(conn, account_id, mapping)
        finance_budget.declare_coverage(conn, account_id=account_id, start=start.isoformat(), end=end.isoformat(),
                                        actor=actor, today=now.date(), source='import', batch_id=batch['id'])
        conn.execute("UPDATE finance_import_batches SET state='committed', mapping_json=?, new_count=?, duplicate_count=?, "
                     'committed_at=? WHERE id=?', (json.dumps(mapping, sort_keys=True), new, duplicate, _iso(now), batch['id']))
        conn.execute('DELETE FROM finance_staged_rows WHERE batch_id=?', (batch['id'],))
    return dict(new=new, duplicate=duplicate)


def discard(conn, batch_id):
    with _transaction(conn):
        batch = _batch(conn, batch_id, 'staged')
        conn.execute('DELETE FROM finance_import_batches WHERE id=?', (batch['id'],))


def rollback(conn, batch_id, *, actor, confirm_edited=False, now=None):
    actor = _text(actor, True)
    with _transaction(conn):
        batch = _batch(conn, batch_id, 'committed')
        ids = [r['txn_id'] for r in conn.execute('SELECT txn_id FROM finance_source_records WHERE batch_id=?', (batch['id'],))]
        marks = ','.join('?' * len(ids)) or 'NULL'
        edited = conn.execute(f"SELECT COUNT(*) FROM finance_txns WHERE id IN ({marks}) "
                              "AND (review='accepted' OR updated_by IS NOT NULL)", ids).fetchone()[0]
        if edited and not confirm_edited:
            raise FinanceStoreError('Some imported transactions were already reviewed. Confirm to remove them too.')
        conn.execute(f"UPDATE finance_txns SET status='pending' WHERE status='replaced' AND id IN (SELECT from_txn_id FROM "
                     f"finance_txn_links WHERE kind='pending_posted' AND to_txn_id IN ({marks}))", ids)
        conn.execute(f'DELETE FROM finance_txns WHERE id IN ({marks})', ids)
        conn.execute('DELETE FROM finance_coverage WHERE batch_id=?', (batch['id'],))
        conn.execute("UPDATE finance_import_batches SET state='rolled_back', rolled_back_at=? WHERE id=?",
                     (_iso(_utc(now)), batch['id']))
    return dict(removed=len(ids), edited=edited)


def batches(conn):
    names = finance_ledger._display(conn)
    return [dict(r, account_name=names.get(r['account_id'], '')) for r in conn.execute(
        'SELECT id, account_id, state, label, imported_by, created_at, row_count, new_count, duplicate_count, committed_at, '
        'rolled_back_at FROM finance_import_batches ORDER BY id DESC LIMIT 100')]
