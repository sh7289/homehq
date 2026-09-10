from datetime import datetime, timezone
from decimal import Decimal
import pytest
import finance_book as book
import finance_store as store
import finance_payments as payments

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)

@pytest.fixture
def db(tmp_path):
    conn = store.connect(str(tmp_path / 'private' / 'finance.db'))
    payments.initialize(conn)
    return conn

def account(db, name, group=1, currency='USD'):
    return book.add_manual(db, nickname=name, currency=currency, balance='100', balance_at='2026-09-07', owner='', group_id=group)

def pair(db, currency='USD'):
    return account(db, 'Card', 5, currency), account(db, 'Checking', 1, currency)

def save(db, card, funding, **changes):
    args = dict(card_id=card, funding_id=funding, amount='12.34', payment_date='2026-09-10', status='scheduled')
    args.update(changes)
    return payments.save(db, **args)

def test_exact_totals_window_overdue_and_names(db):
    card, funding = pair(db)
    first = save(db, card, funding, amount='999999999999999999999999.1234567890123456', payment_date='2026-09-01')
    save(db, card, funding, amount='0.000000000000000001', payment_date='2026-10-07', status='planned')
    save(db, card, funding, amount='40', payment_date='2026-10-08')
    save(db, card, funding, amount='80', status='paid')
    save(db, card, funding, amount='90', status='cancelled')
    euro_card, euro_funding = pair(db, 'EUR')
    save(db, euro_card, euro_funding, amount='3')
    result = payments.view(db, now=NOW)
    assert result['upcoming_totals'] == {'USD': Decimal('999999999999999999999999.123456789012345601'), 'EUR': Decimal('3')}
    assert result['scheduled_totals'] == {'USD': Decimal('999999999999999999999999.1234567890123456'), 'EUR': Decimal('3')}
    assert result['payments'][0] == dict(id=first, card_id=card, card_name='Card', funding_id=funding, funding_name='Checking', currency='USD', amount=Decimal('999999999999999999999999.1234567890123456'), payment_date='2026-09-01', status='scheduled', overdue=True)
    assert [(r['payment_date'], r['id']) for r in result['payments']] == sorted((r['payment_date'], r['id']) for r in result['payments'])

def test_status_updates_preserve_id_and_clear_overdue(db):
    card, funding = pair(db)
    pid = save(db, card, funding, payment_date='2026-09-01', status='planned')
    assert save(db, card, funding, payment_id=pid, payment_date='2026-09-01', status='paid') == pid
    result = payments.view(db, now=NOW)
    assert len(result['payments']) == 1 and not result['payments'][0]['overdue']
    assert result['upcoming_totals'] == result['scheduled_totals'] == {}

@pytest.mark.parametrize('changes', [dict(amount='0'), dict(amount='-1'), dict(amount='NaN'), dict(amount='Infinity'), dict(amount='1e3'), dict(amount='1000000000000000000000001'), dict(amount='0.0000000000000000001'), dict(amount=1), dict(payment_date='2026-02-30'), dict(payment_date='2026-9-7'), dict(status='executed'), dict(payment_id=999), dict(card_id='missing'), dict(funding_id='missing')])
def test_invalid_input_is_atomic(db, changes):
    card, funding = pair(db)
    with pytest.raises(store.FinanceStoreError):
        save(db, card, funding, **changes)
    assert payments.view(db, now=NOW)['payments'] == []

def test_account_bucket_currency_and_same_account_validation(db):
    card, funding = pair(db)
    euro = account(db, 'Euro', currency='EUR')
    illiquid = account(db, 'House', 4)
    for c, f in [(funding, card), (card, card), (card, euro), (card, illiquid)]:
        with pytest.raises(store.FinanceStoreError):
            save(db, c, f)
    db.execute("UPDATE finance_accounts SET currency='NONFINANCIAL' WHERE id IN (?,?)", (card, funding))
    db.commit()
    with pytest.raises(store.FinanceStoreError):
        save(db, card, funding)

def test_provider_accounts_and_idempotent_schema(db):
    card, funding = pair(db)
    db.execute("UPDATE finance_accounts SET source='provider',nickname='',provider_name='Issuer' WHERE id=?", (card,))
    db.commit()
    save(db, card, funding)
    payments.initialize(db)
    payments.initialize(db)
    assert payments.view(db, now=NOW)['payments'][0]['card_name'] == 'Issuer'

def test_nested_rollback_and_failed_edit_preserve_existing(db):
    card, funding = pair(db)
    pid = save(db, card, funding)
    db.execute('BEGIN IMMEDIATE')
    save(db, card, funding, payment_id=pid, amount='99')
    save(db, card, funding, amount='2')
    with pytest.raises(store.FinanceStoreError):
        save(db, card, funding, payment_id=pid, funding_id='missing')
    db.rollback()
    rows = payments.view(db, now=NOW)['payments']
    assert len(rows) == 1 and rows[0]['amount'] == Decimal('12.34')


def test_existing_payment_can_be_closed_after_account_regrouping(db):
    card, funding = pair(db)
    pid = save(db, card, funding)
    book.update_account(db, card, nickname='Card', owner='', group_id=6, position=0,
                        included=True, debt_sign='unconfirmed')
    assert save(db, card, funding, payment_id=pid, status='paid') == pid
    assert payments.view(db, now=NOW)['upcoming_totals'] == {}
