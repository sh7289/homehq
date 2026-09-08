"""Household worksheet behavior using synthetic balances only."""
from decimal import Decimal
import sqlite3
import types
import pytest
import finance_store
from test_finance_routes import finance_app


def seed(path):
    conn = finance_store.connect(str(path))
    finance_store.record_sync(conn, {'accounts':[{'id':'a'*64, 'label':'Checking', 'currency':'USD',
        'balance':Decimal('100.10'), 'balance_at':'2026-09-07T12:00:00Z'}], 'warnings':[], 'complete':True})
    conn.close()


def token(client):
    client.get('/finance')
    with client.session_transaction() as state:
        return state['csrf_token']


def test_finance_writes_reject_missing_csrf(finance_app):
    app, path = finance_app
    seed(path)
    client = app.test_client()
    for url in ['/finance/manual', '/finance/groups', '/finance/snapshots', '/finance/payments', '/finance/accounts/'+'a'*64+'/edit']:
        response = client.post(url, data={})
        assert response.status_code == 400
        assert response.headers['Cache-Control'] == 'no-store'


def test_old_schema_shows_upgrade_without_migrating(finance_app):
    app, path = finance_app
    path.parent.mkdir(mode=0o700)
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE old_schema (id TEXT)')
    conn.close()
    path.chmod(0o600)
    response = app.test_client().get('/finance')
    assert b'Finance update needed' in response.data
    conn = sqlite3.connect(path)
    assert [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ['old_schema']
    conn.close()


def test_account_edit_and_manual_snapshot(finance_app, monkeypatch):
    import finance_routes
    monkeypatch.setattr(finance_routes, 'current_user', types.SimpleNamespace(id='alice'))
    app, path = finance_app
    seed(path)
    client = app.test_client()
    csrf = token(client)
    response = client.post('/finance/accounts/'+'a'*64+'/edit', data={'csrf_token':csrf,
        'nickname':'Household checking', 'owner':'Joint', 'group_id':'1', 'position':'2',
        'included':'on', 'debt_sign':'unconfirmed'})
    assert response.status_code == 302
    response = client.post('/finance/manual', data={'csrf_token':csrf, 'nickname':'Cash at home',
        'currency':'USD', 'balance':'20.05', 'balance_at':'2026-01-01', 'owner':'Joint', 'group_id':'1'})
    assert response.status_code == 302
    page = client.get('/finance')
    assert b'Household checking' in page.data and b'Cash at home' in page.data
    assert b'120.15' in page.data and b'Manual' in page.data
    saved = client.post('/finance/snapshots', data={'csrf_token':csrf})
    assert saved.status_code == 302 and '/finance/snapshots/' in saved.location
    snapshot = client.get(saved.location)
    assert b'Household checking' in snapshot.data
    assert b'2026-01-01' in snapshot.data
    assert b'fonts.googleapis.com' not in snapshot.data


def test_invalid_manual_balance_keeps_form_values(finance_app):
    app, path = finance_app
    seed(path)
    client = app.test_client()
    response = client.post('/finance/manual', data={'csrf_token':token(client), 'nickname':'Coins',
        'currency':'USD', 'balance':'NaN', 'balance_at':'2026-01-01', 'owner':'Joint', 'group_id':'1'})
    assert response.status_code == 400
    assert b'Coins' in response.data
    assert b'Check the entered values' in response.data


def test_manual_balance_does_not_enter_bank_daily_history(finance_app):
    import finance_book
    app, path = finance_app
    seed(path)
    conn = finance_store.connect(str(path))
    finance_book.add_manual(conn, nickname='Cash', currency='USD', balance='50.00',
        balance_at='2026-01-01', owner='Joint', group_id=1)
    finance_store.record_sync(conn, {'accounts':[{'id':'a'*64, 'label':'Checking', 'currency':'USD',
        'balance':Decimal('100.10'), 'balance_at':'2026-09-07T12:00:00Z'}], 'warnings':[], 'complete':True})
    daily = finance_store.dashboard(conn)
    assert daily['totals']['by_currency']['USD'] == Decimal('100.10')
    assert daily['history'][-1]['by_currency']['USD'] == Decimal('100.10')
    assert len(daily['accounts']) == 1
    conn.close()


def test_invalid_manual_edit_rolls_back_nickname_too(finance_app):
    import finance_book
    app, path = finance_app
    seed(path)
    conn = finance_store.connect(str(path))
    account_id = finance_book.add_manual(conn, nickname='Original cash', currency='USD',
        balance='50.00', balance_at='2026-01-01', owner='Joint', group_id=1)
    conn.close()
    client = app.test_client()
    response = client.post('/finance/accounts/'+account_id+'/edit', data={'csrf_token':token(client),
        'nickname':'Changed name', 'owner':'Joint', 'group_id':'1', 'position':'0', 'included':'on',
        'debt_sign':'unconfirmed', 'balance':'broken', 'balance_at':'2026-01-01'})
    assert response.status_code == 400
    conn = finance_store.connect(str(path))
    row = next(a for a in finance_book.view(conn)['accounts'] if a['id']==account_id)
    assert row['nickname'] == 'Original cash' and row['balance'] == Decimal('50.00')
    conn.close()


def test_every_finance_mutation_requires_real_recent_mfa(app, tmp_path, monkeypatch):
    import os
    import pyotp
    import mfa_security
    app.config.update(FINANCE_ENABLED=True, FINANCE_DB_PATH=str(tmp_path / 'private' / 'finance.db'))
    seed(tmp_path / 'private' / 'finance.db')
    client = app.test_client()
    paths = ['/finance/manual', '/finance/groups', '/finance/snapshots', '/finance/payments', '/finance/accounts/'+'a'*64+'/edit']
    for path in paths:
        assert '/login' in client.post(path, data={}).location
    client.get('/login')
    with client.session_transaction() as state:
        csrf = state['csrf_token']
    client.post('/login', data={'username':'alice','password':'password1','csrf_token':csrf})
    for path in paths:
        assert '/mfa' in client.post(path, data={}).location
    secret = pyotp.random_base32()
    monkeypatch.setenv('HOMEHQ_USER1_TOTP_SECRET', secret)
    conn = sqlite3.connect(os.environ['HOMEHQ_DB_PATH'])
    mfa_security.enroll(conn, 'alice', secret, [])
    conn.close()
    client.get('/mfa')
    with client.session_transaction() as state:
        csrf = state['csrf_token']
    client.post('/mfa', data={'code':pyotp.TOTP(secret).now(), 'csrf_token':csrf})
    for path in paths:
        assert client.post(path, data={}).status_code == 400
    csrf = token(client)
    saved = client.post('/finance/snapshots', data={'csrf_token':csrf})
    assert saved.status_code == 302
    with client.session_transaction() as state:
        state['mfa_grant']['at'] -= 901
        state.modified = True
    for path in paths + [saved.location]:
        response = client.get(path) if path == saved.location else client.post(path, data={'csrf_token':csrf})
        assert '/mfa' in response.location
    app.config['FINANCE_ENABLED'] = False
    assert client.post('/finance/snapshots', data={'csrf_token':csrf}).status_code == 404


def test_snapshot_comparison_never_turns_unknown_debt_into_zero(finance_app):
    import finance_book
    app, path = finance_app
    seed(path)
    conn = finance_store.connect(str(path))
    account_id = finance_book.add_manual(conn, nickname='Card', currency='USD', balance='100',
        balance_at='2026-01-01', owner='Joint', group_id=5)
    def confirm(sign):
        finance_book.update_account(conn, account_id, nickname='Card', owner='Joint', group_id=5,
            position=0, included=True, debt_sign=sign)
    confirm('positive')
    finance_book.save_snapshot(conn, actor='alice')
    confirm('unconfirmed')
    second = finance_book.save_snapshot(conn, actor='alice')
    change = next(c for c in finance_book.snapshot(conn, second)['changes'] if c['group_id']==5)
    assert change['current'] is None and change['delta'] is None
    confirm('positive')
    third = finance_book.save_snapshot(conn, actor='alice')
    saved = finance_book.snapshot(conn, third)
    change = next(c for c in saved['changes'] if c['group_id']==5)
    assert change['previous'] is None and change['delta'] is None
    assert saved['previous_complete'] is False
    conn.close()


def test_payment_schedule_is_displayed_and_frozen(finance_app, monkeypatch):
    import finance_book
    import finance_payments
    import finance_routes
    monkeypatch.setattr(finance_routes, 'current_user', types.SimpleNamespace(id='alice'))
    app, path = finance_app
    seed(path)
    conn = finance_store.connect(str(path))
    finance_book.update_account(conn, 'a'*64, nickname='Joint checking', owner='Joint',
        group_id=1, position=0, included=True, debt_sign='unconfirmed')
    card = finance_book.add_manual(conn, nickname='Household card', currency='USD', balance='100',
        balance_at='2026-01-01', owner='Joint', group_id=5)
    conn.close()
    client = app.test_client()
    csrf = token(client)
    response = client.post('/finance/payments', data={'csrf_token':csrf, 'card_id':card,
        'funding_id':'a'*64, 'amount':'25.05', 'payment_date':'2026-09-20', 'status':'scheduled'})
    assert response.status_code == 302
    page = client.get('/finance')
    assert b'2026-09-20' in page.data and b'25.05' in page.data
    assert b'Scheduled with issuer' in page.data and b'Joint checking' in page.data
    saved = client.post('/finance/snapshots', data={'csrf_token':csrf})
    conn = finance_store.connect(str(path))
    payment_id = finance_payments.view(conn)['payments'][0]['id']
    finance_payments.save(conn, payment_id=payment_id, card_id=card, funding_id='a'*64,
        amount='50.10', payment_date='2026-10-20', status='planned')
    conn.close()
    frozen = client.get(saved.location)
    assert frozen.status_code == 200
    assert b'25.05' in frozen.data and b'50.10' not in frozen.data
    assert b'2026-09-20' in frozen.data


def test_payment_editor_retains_regrouped_accounts(finance_app):
    import finance_book
    import finance_payments
    app, path = finance_app
    seed(path)
    conn = finance_store.connect(str(path))
    finance_book.update_account(conn, 'a'*64, nickname='Checking', owner='', group_id=1,
        position=0, included=True, debt_sign='unconfirmed')
    card = finance_book.add_manual(conn, nickname='Card', currency='USD', balance='50',
        balance_at='2026-01-01', owner='', group_id=5)
    pid = finance_payments.save(conn, card_id=card, funding_id='a'*64, amount='20',
        payment_date='2026-01-01', status='scheduled')
    finance_book.update_account(conn, card, nickname='Card', owner='', group_id=6,
        position=0, included=True, debt_sign='unconfirmed')
    conn.close()
    client = app.test_client()
    page = client.get('/finance').get_data(as_text=True)
    edit = page.split('id="payment-edit-'+str(pid)+'"',1)[1]
    assert 'value="'+card+'" selected' in edit
    response = client.post('/finance/payments', data={'csrf_token':token(client), 'payment_id':pid,
        'card_id':card, 'funding_id':'a'*64, 'amount':'20', 'payment_date':'2026-01-01', 'status':'cancelled'})
    assert response.status_code == 302
    conn = finance_store.connect(str(path))
    assert finance_payments.view(conn)['upcoming_totals'] == {}
    conn.close()
