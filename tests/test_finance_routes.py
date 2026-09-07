"""Finance request tests use synthetic balances and a stubbed MFA boundary."""
from decimal import Decimal
from datetime import datetime, timezone
import sys
import types

import pytest
from flask import Flask
import finance_store


@pytest.fixture
def finance_app(tmp_path, monkeypatch):
    # MFA itself is exercised in test_mfa; isolate this route's data boundary.
    import finance_routes
    monkeypatch.setattr(finance_routes, 'require_recent_mfa', lambda fn: fn)
    app = Flask(__name__, template_folder=str(finance_routes.ROOT / 'templates'), static_folder=str(finance_routes.ROOT / 'static'))
    app.secret_key = 'fixture'
    app.jinja_env.filters['titlecase'] = str.title
    app.add_url_rule('/', 'home', lambda: '')
    for endpoint in ['pantry', 'freezer', 'recipes', 'shopping_list', 'capture', 'import_review', 'report']:
        app.add_url_rule('/' + endpoint, endpoint, lambda: '')
    app.context_processor(lambda: {'current_user': types.SimpleNamespace(is_authenticated=True), 'nav_categories': []})
    monkeypatch.setenv('HOMEHQ_FINANCE_ENABLED', 'true')
    path = tmp_path / 'private' / 'finance.db'
    monkeypatch.setenv('HOMEHQ_FINANCE_DB_PATH', str(path))
    finance_routes.init_app(app)
    app.testing = True
    return app, path


def test_missing_store_does_not_create_database(finance_app):
    app, path = finance_app
    response = app.test_client().get('/finance')
    assert response.status_code == 200
    assert b'First sync needed' in response.data
    assert not path.exists()
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert b'fonts.googleapis.com' not in response.data


def test_currency_totals_signs_and_staleness(finance_app):
    app, path = finance_app
    conn = finance_store.connect(str(path))
    accounts = [{'id': str(i) * 64, 'label': label, 'currency': currency,
                 'balance': Decimal(amount), 'balance_at': '2020-01-01T00:00:00Z'}
                for i, label, currency, amount in [(1, 'Checking', 'USD', '100.10'), (2, 'Credit', 'USD', '-20.05'), (3, 'Savings', 'EUR', '7.00')]]
    finance_store.record_sync(conn, {'accounts': accounts, 'warnings': [], 'complete': True})
    conn.close()
    response = app.test_client().get('/finance')
    assert response.status_code == 200
    assert b'80.05' in response.data and b'-20.05' in response.data
    assert b'EUR' in response.data and b'7.00' in response.data
    assert b'Stale' in response.data
    assert b'net worth' in response.data
    assert b'fonts.googleapis.com' not in response.data


def test_disabled_is_404_before_mfa(finance_app):
    app, _ = finance_app
    app.config['FINANCE_ENABLED'] = False
    assert app.test_client().get('/finance').status_code == 404


def test_invalid_repository_path_rejected(monkeypatch):
    import finance_routes
    monkeypatch.setenv('HOMEHQ_FINANCE_ENABLED', 'true')
    monkeypatch.setenv('HOMEHQ_FINANCE_DB_PATH', str(finance_routes.ROOT / 'finance.db'))
    with pytest.raises(finance_store.FinanceStoreError):
        finance_routes.init_app(Flask(__name__))


def test_history_renders_decimal_points_without_joining_gaps(finance_app):
    app, path = finance_app
    conn = finance_store.connect(str(path))
    account = {'id': 'a' * 64, 'label': '<script>alert(1)</script>', 'currency': 'USD',
               'balance': Decimal('100.10'), 'balance_at': '2026-01-01T00:00:00Z'}
    for day, complete in [(1, True), (2, False), (4, True)]:
        account['balance'] += Decimal('2.05')
        finance_store.record_sync(conn, {'accounts': [account], 'complete': complete,
            'warnings': [] if complete else ['provider_warning']}, now=datetime(2026, 1, day, tzinfo=timezone.utc))
    conn.close()
    response = app.test_client().get('/finance')
    assert response.status_code == 200
    assert response.data.count(b'<circle ') == 2
    assert b'<polyline' not in response.data
    assert b'Incomplete' in response.data
    assert b'<script>alert(1)</script>' not in response.data
    assert b'&lt;script&gt;' in response.data


def test_failed_store_has_safe_error_state(finance_app):
    app, path = finance_app
    path.parent.mkdir(mode=0o700)
    path.write_text('not sqlite')
    path.chmod(0o600)
    response = app.test_client().get('/finance')
    assert response.status_code == 200
    assert b'Balances temporarily unavailable' in response.data
    assert b'not sqlite' not in response.data


def test_real_finance_requires_login_then_mfa_and_keeps_cookie_clean(app, tmp_path, monkeypatch):
    import sqlite3
    import pyotp
    import mfa_security
    import os
    client = app.test_client()
    assert client.get('/finance').status_code == 404
    path = tmp_path / 'secure' / 'finance.db'
    app.config.update(FINANCE_ENABLED=True, FINANCE_DB_PATH=str(path))
    assert '/login' in client.get('/finance').location
    client.get('/login')
    with client.session_transaction() as state:
        csrf = state['csrf_token']
    client.post('/login', data={'username':'alice', 'password':'password1', 'csrf_token':csrf})
    assert '/mfa' in client.get('/finance').location
    assert not path.exists()
    secret = pyotp.random_base32()
    monkeypatch.setenv('HOMEHQ_USER1_TOTP_SECRET', secret)
    conn = sqlite3.connect(os.environ['HOMEHQ_DB_PATH'])
    mfa_security.enroll(conn, 'alice', secret, [])
    conn.close()
    client.get('/mfa')
    with client.session_transaction() as state:
        csrf = state['csrf_token']
    response = client.post('/mfa', data={'code':pyotp.TOTP(secret).now(), 'csrf_token':csrf, 'next':'/finance'})
    assert response.location == '/finance'
    assert b'First sync needed' in client.get('/finance').data
    conn = finance_store.connect(str(path))
    finance_store.record_sync(conn, {'accounts':[{'id':'a'*64, 'label':'Private checking', 'currency':'USD',
        'balance':Decimal('987.65'), 'balance_at':'2026-09-07T12:00:00Z'}], 'warnings':[], 'complete':True})
    conn.close()
    response = client.get('/finance')
    assert b'987.65' in response.data
    assert b'Private checking' not in client.get('/').data
    assert response.headers['Cache-Control'] == 'no-store'
    with client.session_transaction() as state:
        assert '987.65' not in str(dict(state))
        assert 'Private checking' not in str(dict(state))
        assert secret not in str(dict(state))


def test_three_decimal_currency_is_never_rounded_to_cents(finance_app):
    app, path = finance_app
    conn = finance_store.connect(str(path))
    finance_store.record_sync(conn, {'accounts':[{'id':'a'*64, 'label':'Savings', 'currency':'KWD',
        'balance':Decimal('0.001'), 'balance_at':'2026-09-07T12:00:00Z'}], 'warnings':[], 'complete':True})
    conn.close()
    response = app.test_client().get('/finance')
    assert response.data.count(b'0.001') == 3  # total, account, daily table
