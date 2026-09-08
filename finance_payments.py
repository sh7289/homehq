"""User-entered payment plans; these records never initiate money movement."""
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext

from finance_book import _transaction
from finance_store import FinanceStoreError, _utc
from simplefin import ISO_CURRENCIES

STATUSES = ('planned', 'scheduled', 'paid', 'cancelled')
ACTIVE_STATUSES = ('planned', 'scheduled')


def initialize(conn):
    with _transaction(conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS finance_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT NOT NULL REFERENCES finance_accounts(id),
            funding_id TEXT NOT NULL REFERENCES finance_accounts(id),
            currency TEXT NOT NULL,
            amount TEXT NOT NULL,
            payment_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('planned','scheduled','paid','cancelled'))
        )''')


def _values(amount, payment_date, status):
    message = 'Enter a positive payment amount, a valid payment date, and a supported status.'
    if not isinstance(amount, str) or len(amount) > 100 or not re.fullmatch(r'[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', amount):
        raise FinanceStoreError(message)
    try:
        value = Decimal(amount)
        if not value.is_finite() or value <= 0 or value > Decimal('1e24') or value.as_tuple().exponent < -18 or len(value.as_tuple().digits) > 43:
            raise ValueError
        if not isinstance(payment_date, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', payment_date):
            raise ValueError
        day = date.fromisoformat(payment_date)
        if status not in STATUSES:
            raise ValueError
    except (ValueError, InvalidOperation):
        raise FinanceStoreError(message) from None
    return format(value, 'f'), day.isoformat()


def _account(conn, account_id, bucket):
    if not isinstance(account_id, str) or len(account_id) > 200:
        raise FinanceStoreError('Select an existing debt account and a liquid funding account.')
    row = conn.execute('''SELECT a.currency, g.bucket FROM finance_accounts a
        JOIN finance_groups g ON g.id=a.group_id WHERE a.id=?''', (account_id,)).fetchone()
    if row is None or row['bucket'] != bucket:
        raise FinanceStoreError('Select an existing debt account and a liquid funding account.')
    return row


def save(conn, *, payment_id=None, card_id, funding_id, amount, payment_date, status):
    amount, payment_date = _values(amount, payment_date, status)
    if payment_id is not None:
        if isinstance(payment_id, bool) or not re.fullmatch(r'[0-9]{1,18}', str(payment_id)) or int(payment_id) < 1:
            raise FinanceStoreError('Payment record was not found.')
        payment_id = int(payment_id)
    with _transaction(conn):
        existing = None
        if payment_id is not None:
            existing = conn.execute('SELECT * FROM finance_payments WHERE id=?', (payment_id,)).fetchone()
            if existing is None:
                raise FinanceStoreError('Payment record was not found.')
        if existing and existing['card_id'] == card_id and existing['funding_id'] == funding_id:
            # Classification edits must not trap old schedules in active status.
            # Retain the currency recorded for this unchanged payment relationship.
            currency = existing['currency']
        else:
            card = _account(conn, card_id, 'debt')
            funding = _account(conn, funding_id, 'liquid')
            if card_id == funding_id or card['currency'] not in ISO_CURRENCIES or card['currency'] != funding['currency']:
                raise FinanceStoreError('Payment accounts must use the same supported monetary currency.')
            currency = card['currency']
        args = (card_id, funding_id, currency, amount, payment_date, status)
        if payment_id is None:
            return conn.execute('''INSERT INTO finance_payments
                (card_id,funding_id,currency,amount,payment_date,status) VALUES (?,?,?,?,?,?)''', args).lastrowid
        if not conn.execute('SELECT 1 FROM finance_payments WHERE id=?', (payment_id,)).fetchone():
            raise FinanceStoreError('Payment record was not found.')
        conn.execute('''UPDATE finance_payments SET card_id=?,funding_id=?,currency=?,amount=?,payment_date=?,status=?
            WHERE id=?''', args + (payment_id,))
    return payment_id


def view(conn, now=None):
    today = _utc(now).date()
    through = today + timedelta(days=30)
    result = dict(payments=[], upcoming_totals={}, scheduled_totals={})
    # One SELECT keeps account names and payment records consistent together.
    rows = conn.execute('''SELECT p.*,
        COALESCE(NULLIF(c.nickname,''),NULLIF(c.provider_name,''),c.label) AS card_name,
        COALESCE(NULLIF(f.nickname,''),NULLIF(f.provider_name,''),f.label) AS funding_name
        FROM finance_payments p JOIN finance_accounts c ON c.id=p.card_id
        JOIN finance_accounts f ON f.id=p.funding_id ORDER BY p.payment_date,p.id''')
    with localcontext() as ctx:
        ctx.prec = 80
        for row in rows:
            payment = dict(row)
            amount, _ = _values(payment['amount'], payment['payment_date'], payment['status'])
            payment['amount'] = Decimal(amount)
            day = date.fromisoformat(payment['payment_date'])
            active = payment['status'] in ACTIVE_STATUSES
            payment['overdue'] = active and day < today
            result['payments'].append(payment)
            if active and day <= through:
                currency = payment['currency']
                result['upcoming_totals'][currency] = result['upcoming_totals'].get(currency, Decimal(0)) + payment['amount']
                if payment['status'] == 'scheduled':
                    result['scheduled_totals'][currency] = result['scheduled_totals'].get(currency, Decimal(0)) + payment['amount']
    return result
