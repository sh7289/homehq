"""Canonical household transactions: evidence, classification, splits and links.

A transaction records what happened on one account (``amount`` < 0 is money
leaving through that account). Allocations record what it was for; a classified
transaction's allocations sum exactly to its amount. Transfers and card payments
move money between accounts and never carry category allocations. Links keep
both sides of transfers, refunds and pending-to-posted replacements traceable.
"""
import hashlib
import json
import re
from datetime import date, timedelta
from decimal import Decimal

import finance_budget
from finance_book import _text, _transaction
from finance_budget import mask_digits, money_text, parse_money
from finance_store import FinanceStoreError, _iso, _utc

CLASSIFIED_KINDS = ('expense', 'income', 'refund', 'reimbursement', 'transfer', 'card_payment')
MOVEMENT_KINDS = ('transfer', 'card_payment')
INFLOW_KINDS = ('income', 'refund', 'reimbursement')
LINK_KINDS = ('transfer', 'card_payment', 'refund', 'pending_posted')
DUPLICATE_WINDOW = 3


def merchant_key(text):
    text = re.sub(r'[\d#*]', ' ', (text or '').lower())
    return ' '.join(text.split())


def import_fingerprint(account_id, txn_date, amount, description):
    raw = f'{account_id}|{txn_date}|{money_text(amount)}|{merchant_key(mask_digits(description))}'
    return hashlib.sha256(raw.encode()).hexdigest()


def _now():
    return _iso(_utc())


def _id(value, message='Transaction was not found.'):
    if isinstance(value, bool) or not re.fullmatch(r'\d{1,18}', str(value)):
        raise FinanceStoreError(message)
    return int(value)


def _txn(conn, txn_id):
    row = conn.execute('SELECT * FROM finance_txns WHERE id=?', (_id(txn_id),)).fetchone()
    if row is None:
        raise FinanceStoreError('Transaction was not found.')
    return row


def _touch(conn, txn_id, actor, **fields):
    fields.update(updated_by=actor, updated_at=_now())
    columns = ','.join(f'{name}=?' for name in fields)
    conn.execute(f'UPDATE finance_txns SET {columns} WHERE id=?', tuple(fields.values()) + (txn_id,))


def create_txn(conn, *, account_id, txn_date, amount, description, actor, status='posted', posted_date=None,
               source='manual', batch_id=None, fingerprint=None, occurrence=1, source_txn_hash=None, columns=None):
    day = finance_budget._day(txn_date).isoformat()
    posted = finance_budget._day(posted_date).isoformat() if posted_date else None
    value = amount if isinstance(amount, Decimal) else parse_money(amount)
    if value == 0:
        raise FinanceStoreError('Enter a non-zero amount.')
    if status not in ('pending', 'posted') or source not in ('csv', 'manual'):
        raise FinanceStoreError('Transaction settings are invalid.')
    actor = _text(actor, True)
    description = mask_digits(description) or '(no description)'
    with _transaction(conn):
        account = conn.execute('SELECT a.currency FROM finance_accounts a JOIN finance_budget_accounts b '
                               'ON b.account_id=a.id WHERE a.id=?', (account_id,)).fetchone()
        if account is None or account['currency'] == 'NONFINANCIAL':
            raise FinanceStoreError('Select an account set up for budgeting.')
        if fingerprint is None:
            fingerprint = import_fingerprint(account_id, day, value, description)
            occurrence = 1 + conn.execute('SELECT COUNT(*) FROM finance_source_records WHERE account_id=? AND source=? '
                                          'AND fingerprint=?', (account_id, source, fingerprint)).fetchone()[0]
        low = (date.fromisoformat(day) - timedelta(days=DUPLICATE_WINDOW)).isoformat()
        high = (date.fromisoformat(day) + timedelta(days=DUPLICATE_WINDOW)).isoformat()
        duplicate = conn.execute(
            "SELECT t.id FROM finance_txns t JOIN finance_source_records s ON s.txn_id=t.id "
            "WHERE t.account_id=? AND t.amount=? AND t.txn_date BETWEEN ? AND ? AND t.status NOT IN ('void','replaced') "
            "AND s.source != ? ORDER BY t.id LIMIT 1", (account_id, money_text(value), low, high, source)).fetchone()
        now = _now()
        txn_id = conn.execute(
            'INSERT INTO finance_txns (account_id,txn_date,posted_date,amount,currency,original_description,merchant,status,'
            "kind,review,possible_duplicate_of,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,'unclassified','unreviewed',?,?,?)",
            (account_id, day, posted, money_text(value), account['currency'], description, description, status,
             duplicate['id'] if duplicate else None, actor, now)).lastrowid
        conn.execute('INSERT INTO finance_source_records (batch_id,account_id,source,source_txn_hash,fingerprint,occurrence,'
                     'columns_json,txn_id) VALUES (?,?,?,?,?,?,?,?)',
                     (batch_id, account_id, source, source_txn_hash, fingerprint, occurrence,
                      json.dumps(columns if columns is not None else
                                 {'date': day, 'description': description, 'amount': money_text(value)}), txn_id))
        suggest(conn, txn_id)
    return txn_id


def _movement_link(conn, txn_id):
    return conn.execute("SELECT id FROM finance_txn_links WHERE kind IN ('transfer','card_payment') "
                        'AND (from_txn_id=? OR to_txn_id=?)', (txn_id, txn_id)).fetchone()


def _validated_allocations(conn, kind, amount, allocations):
    if not isinstance(allocations, list) or not allocations or len(allocations) > 20:
        raise FinanceStoreError('Add at least one category line.')
    allowed = ['shared'] + finance_budget.people(conn)
    cleaned, total = [], Decimal('0')
    for item in allocations:
        if not isinstance(item, dict):
            raise FinanceStoreError('Category lines are invalid.')
        category = finance_budget._category(conn, item.get('category_id'))
        if not category['active'] or category['type'] == 'transfer':
            raise FinanceStoreError('Select an active spending or income category.')
        if item.get('person') not in allowed:
            raise FinanceStoreError('Select shared or a household member for each line.')
        value = item['amount'] if isinstance(item.get('amount'), Decimal) else parse_money(item.get('amount'))
        if value == 0 or (value < 0) != (amount < 0):
            raise FinanceStoreError('Each category line must have the same sign as the transaction.')
        total += value
        cleaned.append((category['id'], item['person'], money_text(value),
                        finance_budget._long_text(item.get('note', '') or '', 200)))
    if total != amount:
        raise FinanceStoreError('Split amounts must add up to the transaction total.')
    return cleaned


def classify(conn, txn_id, *, kind, allocations, actor, merchant=None, note=None):
    actor = _text(actor, True)
    if kind not in CLASSIFIED_KINDS:
        raise FinanceStoreError('Select what kind of transaction this is.')
    with _transaction(conn):
        txn = _txn(conn, txn_id)
        if txn['status'] in ('void', 'replaced'):
            raise FinanceStoreError('Void or replaced transactions cannot be reclassified.')
        amount = Decimal(txn['amount'])
        if _movement_link(conn, txn['id']) and kind not in MOVEMENT_KINDS:
            raise FinanceStoreError('Unlink the transfer before giving it a category.')
        if kind in MOVEMENT_KINDS:
            if allocations:
                raise FinanceStoreError('Transfers and card payments do not use categories.')
            rows = []
        else:
            if (kind == 'expense') != (amount < 0):
                raise FinanceStoreError('Expenses are money out; income, refunds and reimbursements are money in.')
            rows = _validated_allocations(conn, kind, amount, allocations)
        conn.execute('DELETE FROM finance_txn_allocations WHERE txn_id=?', (txn['id'],))
        for category_id, person, value, line_note in rows:
            conn.execute('INSERT INTO finance_txn_allocations (txn_id,category_id,person,amount,note) VALUES (?,?,?,?,?)',
                         (txn['id'], category_id, person, value, line_note))
        fields = dict(kind=kind, review='accepted')
        if merchant is not None:
            fields['merchant'] = mask_digits(merchant) or txn['merchant']
        if note is not None:
            fields['note'] = finance_budget._long_text(note)
        _touch(conn, txn['id'], actor, **fields)


def suggest(conn, txn_id):
    txn = _txn(conn, txn_id)
    if txn['review'] != 'unreviewed':
        return
    key = merchant_key(txn['merchant'])
    if not key:
        return
    sign = '<' if Decimal(txn['amount']) < 0 else '>'
    for row in conn.execute(
            f"SELECT t.id, t.kind, t.merchant FROM finance_txns t WHERE t.id != ? AND t.review='accepted' "
            f"AND CAST(t.amount AS REAL) {sign} 0 AND t.kind NOT IN ('transfer','card_payment') "
            "AND t.status NOT IN ('void','replaced') ORDER BY t.txn_date DESC, t.id DESC LIMIT 500", (txn['id'],)):
        if merchant_key(row['merchant']) != key:
            continue
        lines = conn.execute('SELECT category_id, person FROM finance_txn_allocations WHERE txn_id=?', (row['id'],)).fetchall()
        if len(lines) != 1:
            return
        conn.execute("UPDATE finance_txns SET review='suggested', suggested_kind=?, suggested_category_id=?, "
                     "suggested_person=?, suggestion_source='prior' WHERE id=?",
                     (row['kind'], lines[0]['category_id'], lines[0]['person'], txn['id']))
        return


def accept_suggestion(conn, txn_id, actor):
    with _transaction(conn):
        txn = _txn(conn, txn_id)
        if txn['review'] != 'suggested':
            raise FinanceStoreError('This transaction has no suggestion to accept.')
        classify(conn, txn['id'], kind=txn['suggested_kind'], actor=actor, allocations=[dict(
            category_id=txn['suggested_category_id'], person=txn['suggested_person'], amount=Decimal(txn['amount']))])


def accept_suggestions(conn, txn_ids, actor):
    count = 0
    with _transaction(conn):
        for txn_id in txn_ids:
            if _txn(conn, txn_id)['review'] == 'suggested':
                accept_suggestion(conn, txn_id, actor)
                count += 1
    return count


def _role(conn, account_id):
    row = conn.execute('SELECT role FROM finance_budget_accounts WHERE account_id=?', (account_id,)).fetchone()
    return row['role'] if row else None


def link(conn, *, kind, from_id, to_id, actor, payment_id=None):
    actor = _text(actor, True)
    if kind not in LINK_KINDS:
        raise FinanceStoreError('Select a valid link.')
    with _transaction(conn):
        a, b = _txn(conn, from_id), _txn(conn, to_id)
        if a['id'] == b['id'] or 'void' in (a['status'], b['status']):
            raise FinanceStoreError('These transactions cannot be linked.')
        amount_a, amount_b = Decimal(a['amount']), Decimal(b['amount'])
        if payment_id not in (None, ''):
            payment_id = _id(payment_id, 'Payment record was not found.')
            if not conn.execute('SELECT 1 FROM finance_payments WHERE id=?', (payment_id,)).fetchone():
                raise FinanceStoreError('Payment record was not found.')
        else:
            payment_id = None
        if kind in MOVEMENT_KINDS:
            if (a['account_id'] == b['account_id'] or amount_a != -amount_b or 'replaced' in (a['status'], b['status'])
                    or _movement_link(conn, a['id']) or _movement_link(conn, b['id'])):
                raise FinanceStoreError('A transfer links equal and opposite amounts on two different accounts.')
            if kind == 'card_payment' and sorted([_role(conn, a['account_id']) == 'card',
                                                  _role(conn, b['account_id']) == 'card']) != [False, True]:
                raise FinanceStoreError('A card payment links a card account with a non-card account.')
            for txn in (a, b):
                conn.execute('DELETE FROM finance_txn_allocations WHERE txn_id=?', (txn['id'],))
                _touch(conn, txn['id'], actor, kind=kind, review='accepted')
        elif kind == 'refund':
            already = sum((Decimal(r['amount']) for r in conn.execute(
                "SELECT t.amount FROM finance_txn_links l JOIN finance_txns t ON t.id=l.from_txn_id "
                "WHERE l.kind='refund' AND l.to_txn_id=?", (b['id'],))), Decimal('0'))
            if (amount_a <= 0 or b['kind'] != 'expense' or already + amount_a > -amount_b
                    or conn.execute("SELECT 1 FROM finance_txn_links WHERE kind='refund' AND from_txn_id=?", (a['id'],)).fetchone()):
                raise FinanceStoreError('A refund links money in to an earlier expense of at least that amount.')
            lines = conn.execute('SELECT category_id, person FROM finance_txn_allocations WHERE txn_id=?', (b['id'],)).fetchall()
            if a['review'] != 'accepted' and len(lines) == 1:
                classify(conn, a['id'], kind='refund', actor=actor, allocations=[dict(
                    category_id=lines[0]['category_id'], person=lines[0]['person'], amount=amount_a)])
        else:
            if a['status'] != 'pending' or b['status'] != 'posted' or a['account_id'] != b['account_id']:
                raise FinanceStoreError('Link a pending item to the posted transaction that replaced it on the same account.')
            conn.execute("UPDATE finance_txns SET status='replaced' WHERE id=?", (a['id'],))
            lines = conn.execute('SELECT category_id, person FROM finance_txn_allocations WHERE txn_id=?', (a['id'],)).fetchall()
            if (b['review'] != 'accepted' and a['review'] == 'accepted' and len(lines) == 1
                    and a['kind'] not in MOVEMENT_KINDS and (amount_a < 0) == (amount_b < 0)):
                classify(conn, b['id'], kind=a['kind'], actor=actor, allocations=[dict(
                    category_id=lines[0]['category_id'], person=lines[0]['person'], amount=amount_b)])
        return conn.execute('INSERT INTO finance_txn_links (kind,from_txn_id,to_txn_id,payment_id,created_by,created_at) '
                            'VALUES (?,?,?,?,?,?)', (kind, a['id'], b['id'], payment_id, actor, _now())).lastrowid


def unlink(conn, link_id, actor):
    actor = _text(actor, True)
    with _transaction(conn):
        row = conn.execute('SELECT * FROM finance_txn_links WHERE id=?', (_id(link_id, 'Link was not found.'),)).fetchone()
        if row is None:
            raise FinanceStoreError('Link was not found.')
        conn.execute('DELETE FROM finance_txn_links WHERE id=?', (row['id'],))
        if row['kind'] == 'pending_posted':
            conn.execute("UPDATE finance_txns SET status='pending' WHERE id=? AND status='replaced'", (row['from_txn_id'],))
        elif row['kind'] in MOVEMENT_KINDS:
            for txn_id in (row['from_txn_id'], row['to_txn_id']):
                _touch(conn, txn_id, actor, kind='unclassified', review='unreviewed')
        return row


def void(conn, txn_id, actor):
    actor = _text(actor, True)
    with _transaction(conn):
        txn = _txn(conn, txn_id)
        _touch(conn, txn['id'], actor, status='void')


def _display(conn):
    return {r['id']: r['name'] for r in conn.execute(
        "SELECT id, COALESCE(NULLIF(nickname,''),NULLIF(provider_name,''),label) AS name FROM finance_accounts")}


def link_candidates(conn, txn_id, kind):
    txn = _txn(conn, txn_id)
    amount, day = Decimal(txn['amount']), date.fromisoformat(txn['txn_date'])
    names = _display(conn)

    def window(days, before_only=False):
        low = (day - timedelta(days=days)).isoformat()
        high = day.isoformat() if before_only else (day + timedelta(days=days)).isoformat()
        return conn.execute("SELECT * FROM finance_txns WHERE id != ? AND txn_date BETWEEN ? AND ? "
                            "AND status NOT IN ('void') ORDER BY txn_date, id", (txn['id'], low, high)).fetchall()

    found = []
    if kind in MOVEMENT_KINDS:
        if not _movement_link(conn, txn['id']):
            for row in window(7):
                if (row['account_id'] != txn['account_id'] and Decimal(row['amount']) == -amount
                        and row['status'] != 'replaced' and not _movement_link(conn, row['id'])):
                    if kind == 'card_payment' and (_role(conn, row['account_id']) == 'card') == (_role(conn, txn['account_id']) == 'card'):
                        continue
                    found.append(row)
    elif kind == 'refund' and amount > 0:
        key = merchant_key(txn['merchant'])
        found = [r for r in window(180, before_only=True) if r['kind'] == 'expense' and r['status'] != 'replaced'
                 and -Decimal(r['amount']) >= amount and merchant_key(r['merchant']) == key]
    elif kind == 'pending_posted':
        want = 'posted' if txn['status'] == 'pending' else 'pending'
        key = merchant_key(txn['merchant'])
        found = [r for r in window(10) if r['account_id'] == txn['account_id'] and r['status'] == want
                 and merchant_key(r['merchant']) == key]
    found.sort(key=lambda r: abs((date.fromisoformat(r['txn_date']) - day).days))
    return [dict(id=r['id'], txn_date=r['txn_date'], amount=Decimal(r['amount']), merchant=r['merchant'],
                 status=r['status'], account_name=names.get(r['account_id'], '')) for r in found[:10]]


def get_txn(conn, txn_id):
    txn = dict(_txn(conn, txn_id))
    names = _display(conn)
    categories = {r['id']: r['name'] for r in conn.execute('SELECT id, name FROM finance_budget_categories')}
    txn['amount'] = Decimal(txn['amount'])
    txn['account_name'] = names.get(txn['account_id'], '')
    txn['suggested_category_name'] = categories.get(txn['suggested_category_id'])
    txn['allocations'] = [dict(r, amount=Decimal(r['amount']), category_name=categories.get(r['category_id'], ''))
                          for r in conn.execute('SELECT * FROM finance_txn_allocations WHERE txn_id=? ORDER BY id', (txn['id'],))]
    txn['links'] = []
    for row in conn.execute('SELECT * FROM finance_txn_links WHERE from_txn_id=? OR to_txn_id=? ORDER BY id',
                            (txn['id'], txn['id'])):
        other = conn.execute('SELECT * FROM finance_txns WHERE id=?',
                             (row['to_txn_id'] if row['from_txn_id'] == txn['id'] else row['from_txn_id'],)).fetchone()
        txn['links'].append(dict(id=row['id'], kind=row['kind'], other_id=other['id'], other_date=other['txn_date'],
                                 other_amount=Decimal(other['amount']), other_merchant=other['merchant'],
                                 other_account=names.get(other['account_id'], ''), payment_id=row['payment_id']))
    txn['source_records'] = [dict(source=r['source'], batch_id=r['batch_id'], columns=json.loads(r['columns_json']))
                             for r in conn.execute('SELECT * FROM finance_source_records WHERE txn_id=? ORDER BY id', (txn['id'],))]
    return txn
