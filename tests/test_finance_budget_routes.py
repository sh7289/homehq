"""Budgeting pages: security gates (acceptance test 18) and the main flows."""
import io
import types

import pytest

import finance_store
from test_finance_routes import finance_app  # noqa: F401 (fixture)
from test_finance_budget import CHECKING, CARD_H, ledger_db

POSTS = ['/finance/transactions/accept', '/finance/transactions/new', '/finance/transactions/1/classify',
         '/finance/transactions/1/link', '/finance/links/1/unlink', '/finance/transactions/1/void',
         '/finance/imports', '/finance/imports/1/commit', '/finance/imports/1/discard', '/finance/imports/1/rollback',
         '/finance/budget/settings/people']
GETS = ['/finance/budget', '/finance/transactions', '/finance/transactions/new', '/finance/imports', '/finance/budget/settings']


@pytest.fixture
def budget_app(finance_app, monkeypatch):  # noqa: F811
    import finance_budget_routes
    monkeypatch.setattr(finance_budget_routes, 'current_user', types.SimpleNamespace(id='alice'))
    app, path = finance_app
    ledger_db(path.parent.parent).close()
    return app, path


def csrf(client):
    client.get('/finance/budget/settings')
    with client.session_transaction() as s:
        return s['csrf_token']


def test_every_budget_post_requires_csrf_and_pages_are_private(budget_app):  # Test 18
    app, _ = budget_app
    client = app.test_client()
    for url in POSTS:
        assert client.post(url, data={}).status_code == 400, url
    for url in GETS:
        r = client.get(url)
        assert r.status_code == 200, url
        assert r.headers['Cache-Control'] == 'no-store'
        assert b'fonts.googleapis.com' not in r.data


def test_budget_routes_require_real_recent_mfa(app, tmp_path):  # Test 18 with the real gate
    app.config.update(FINANCE_ENABLED=True, FINANCE_DB_PATH=str(tmp_path / 'private' / 'finance.db'))
    client = app.test_client()
    for url in GETS:
        assert '/login' in client.get(url).location, url
    client.get('/login')
    with client.session_transaction() as s:
        token = s['csrf_token']
    client.post('/login', data={'username': 'alice', 'password': 'password1', 'csrf_token': token})
    for url in GETS:
        assert '/mfa' in client.get(url).location, url
    for url in POSTS:
        assert '/mfa' in client.post(url, data={}).location, url
    app.config['FINANCE_ENABLED'] = False
    assert client.get('/finance/budget').status_code == 404


def test_import_flow_end_to_end(budget_app):
    app, path = budget_app
    client = app.test_client()
    token = csrf(client)
    data = {'csrf_token': token, 'account_id': CHECKING, 'label': 'sep',
            'file': (io.BytesIO(b'Date,Description,Amount\n09/01/2026,COFFEE,-4.50\n'), 'sep.csv')}
    r = client.post('/finance/imports', data=data, content_type='multipart/form-data')
    assert r.status_code == 302
    page = client.get(r.location)
    assert b'1 new' in page.data
    batch = r.location.rsplit('/', 1)[1]
    form = {'csrf_token': token, 'action': 'commit', 'map_date': '0', 'map_description': '1', 'map_amount': '2',
            'sign': 'outflow_negative', 'period_start': '2026-09-01', 'period_end': '2026-09-02'}
    r = client.post(f'/finance/imports/{batch}/commit', data=form)
    assert r.status_code == 302
    assert b'COFFEE' in client.get('/finance/transactions').data
    r = client.post(f'/finance/imports/{batch}/rollback', data={'csrf_token': token})
    assert r.status_code == 302
    assert b'COFFEE' not in client.get('/finance/transactions?view=all').data


def test_bad_upload_shows_safe_error(budget_app):
    app, _ = budget_app
    client = app.test_client()
    token = csrf(client)
    data = {'csrf_token': token, 'account_id': CHECKING, 'label': 'x',
            'file': (io.BytesIO(b'just one line'), 'x.csv')}
    r = client.post('/finance/imports', data=data, content_type='multipart/form-data')
    assert r.status_code == 400 and b'could not be read' in r.data


def test_split_classify_via_form_and_mismatch_error(budget_app):
    app, path = budget_app
    client = app.test_client()
    token = csrf(client)
    r = client.post('/finance/transactions/new', data={'csrf_token': token, 'account_id': CARD_H, 'txn_date': '2026-09-04',
                                                       'amount': '-200', 'description': 'TARGET', 'status': 'posted'})
    assert r.status_code == 302
    conn = finance_store.connect(str(path))
    ids = {r['name']: r['id'] for r in conn.execute('SELECT id, name FROM finance_budget_categories')}
    tid = conn.execute('SELECT id FROM finance_txns').fetchone()['id']
    conn.close()
    form = {'csrf_token': token, 'kind': 'expense',
            'alloc_category': [ids['Groceries and household essentials'], ids['Gifts and Christmas'], ids['Heather personal'], ''],
            'alloc_person': ['shared', 'shared', 'Heather', 'shared'], 'alloc_amount': ['-80', '-70', '-40', ''],
            'alloc_note': ['', '', '', '']}
    bad = client.post(f'/finance/transactions/{tid}/classify', data=form)
    assert bad.status_code == 400 and b'add up' in bad.data
    form['alloc_amount'] = ['-80', '-70', '-50', '']
    assert client.post(f'/finance/transactions/{tid}/classify', data=form).status_code == 302
    detail = client.get(f'/finance/transactions/{tid}').data
    assert b'Gifts and Christmas' in detail and b'Heather personal' in detail


def test_accept_suggested_and_link(budget_app):
    app, path = budget_app
    client = app.test_client()
    token = csrf(client)
    conn = finance_store.connect(str(path))
    import finance_ledger as L
    from test_finance_budget import cat, CARD_S
    a = L.create_txn(conn, account_id=CARD_H, txn_date='2026-09-01', amount='-30', description='CHIPOTLE', actor='s')
    L.classify(conn, a, kind='expense', allocations=[dict(category_id=cat(conn, 'Shared dining and entertainment'), person='shared', amount='-30')], actor='s')
    b = L.create_txn(conn, account_id=CARD_S, txn_date='2026-09-02', amount='-12', description='CHIPOTLE', actor='s')
    out = L.create_txn(conn, account_id=CHECKING, txn_date='2026-09-10', amount='-500', description='PAYMENT', actor='s')
    inn = L.create_txn(conn, account_id=CARD_S, txn_date='2026-09-11', amount='500', description='PAYMENT', actor='s')
    conn.close()
    inbox = client.get('/finance/transactions').data
    assert b'Suggested' in inbox
    assert client.post('/finance/transactions/accept', data={'csrf_token': token, 'txn_id': [b]}).status_code == 302
    detail = client.get(f'/finance/transactions/{out}').data
    assert f'value="{inn}"'.encode() in detail
    r = client.post(f'/finance/transactions/{out}/link', data={'csrf_token': token, 'kind': 'card_payment', 'other_id': inn})
    assert r.status_code == 302
    conn = finance_store.connect(str(path))
    kinds = {r['id']: (r['kind'], r['review']) for r in conn.execute('SELECT id, kind, review FROM finance_txns')}
    conn.close()
    assert kinds[b] == ('expense', 'accepted') and kinds[out][0] == 'card_payment'


def test_settings_forms(budget_app):
    app, path = budget_app
    client = app.test_client()
    token = csrf(client)
    page = client.get('/finance/budget/settings').data
    assert b'Household wants' in page and b'Checking' in page
    assert client.post('/finance/budget/settings/people', data={'csrf_token': token, 'people': 'Heather, Steve, Jackie'}).status_code == 302
    r = client.post('/finance/budget/settings/coverage', data={'csrf_token': token, 'account_id': CHECKING,
                                                              'start': '2026-09-01', 'end': '2026-09-05'})
    assert r.status_code == 302
    r = client.post('/finance/budget/settings/target', data={'csrf_token': token, 'category_id': '1', 'effective_month': '2020-01',
                                                            'amount': '5', 'basis': 'estimate', 'note': ''})
    assert r.status_code == 400 and b'past months' in r.data


def test_budget_page_shows_quality_and_plan_labels(budget_app):
    app, _ = budget_app
    page = app.test_client().get('/finance/budget?month=2026-11').data
    assert b'Insufficient data' in page and b'Plan incomplete' in page and b'Groceries and household essentials' in page
    assert b'planning' in page and b'estimate' in page


def test_budget_upgrade_needed_does_not_break_worksheet(finance_app):  # noqa: F811
    app, path = finance_app
    conn = finance_store.connect(str(path))
    conn.execute('DROP TABLE finance_coverage')
    conn.commit()
    conn.close()
    client = app.test_client()
    assert b'Budgeting update needed' in client.get('/finance/budget').data
    assert client.get('/finance').status_code == 200
    assert b'Budgeting update needed' not in client.get('/finance').data


def test_bad_month_query_is_rejected_safely(budget_app):
    app, _ = budget_app
    assert app.test_client().get('/finance/budget?month=2026-13').status_code == 400


def test_missing_store_is_friendly(finance_app):  # noqa: F811
    app, path = finance_app
    r = app.test_client().get('/finance/transactions')
    assert r.status_code == 200 and b'First sync needed' in r.data
    assert not path.exists()


def test_incomplete_plan_does_not_claim_surplus_or_shortfall(budget_app):
    app, _ = budget_app
    page = app.test_client().get('/finance/budget?month=2026-11').data
    assert b'Planned shortfall' not in page and b'Planned surplus' not in page
    assert b'Unknown until the missing lines have amounts' in page
    assert not __import__('re').search(rb'\b1 plan lines', page)


def test_card_payment_match_not_repeated_as_transfer(budget_app):
    app, path = budget_app
    conn = finance_store.connect(str(path))
    import finance_ledger as L
    from test_finance_budget import CARD_S
    out = L.create_txn(conn, account_id=CHECKING, txn_date='2026-09-10', amount='-500', description='AUTOPAY', actor='s')
    inn = L.create_txn(conn, account_id=CARD_S, txn_date='2026-09-10', amount='500', description='THANK YOU', actor='s')
    conn.close()
    page = app.test_client().get(f'/finance/transactions/{out}').data
    assert page.count(f'name="other_id" value="{inn}"'.encode()) == 1
    assert b'Possible card payment matches' in page and b'Possible transfer matches' not in page


P2_GETS = ['/finance/funds', '/finance/forecast', '/finance/commitments',
           '/finance/exports/transactions.csv?month=2026-10', '/finance/exports/categories.csv?month=2026-10']
P2_POSTS = ['/finance/funds/movements', '/finance/commitments', '/finance/budget/settings/limits']


def test_phase2_routes_are_gated_and_private(budget_app):
    app, _ = budget_app
    client = app.test_client()
    for url in P2_POSTS:
        assert client.post(url, data={}).status_code == 400, url
    for url in P2_GETS:
        r = client.get(url)
        assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store', url
    r = client.get('/finance/exports/transactions.csv?month=2026-10')
    assert r.mimetype == 'text/csv' and 'attachment' in r.headers['Content-Disposition']
    assert 'transactions-2026-10.csv' in r.headers['Content-Disposition']


def test_phase2_routes_require_real_mfa(app, tmp_path):
    app.config.update(FINANCE_ENABLED=True, FINANCE_DB_PATH=str(tmp_path / 'private' / 'finance.db'))
    client = app.test_client()
    for url in P2_GETS:
        assert '/login' in client.get(url).location, url
    for url in P2_POSTS:
        assert '/login' in client.post(url, data={}).location, url


def test_fund_movement_and_dashboard(budget_app):
    app, path = budget_app
    client = app.test_client()
    token = csrf(client)
    conn = finance_store.connect(str(path))
    travel = conn.execute("SELECT id FROM finance_budget_categories WHERE name='Travel'").fetchone()['id']
    conn.close()
    r = client.post('/finance/funds/movements', data={'csrf_token': token, 'category_id': travel, 'kind': 'opening',
                                                      'amount': '1200', 'movement_date': '2026-09-01', 'note': ''})
    assert r.status_code == 302
    page = client.get('/finance/funds').data
    assert b'1,200.00' in page and b'Setup needed' in page
    bad = client.post('/finance/funds/movements', data={'csrf_token': token, 'category_id': travel, 'kind': 'contribution',
                                                        'amount': '-5', 'movement_date': '2026-09-01', 'note': ''})
    assert bad.status_code == 400 and b'positive amount' in bad.data
    dash = client.get('/finance/budget').data
    for text in [b'Remaining category budget', b'Projected cash after commitments', b'Available cash', b'Savings progress']:
        assert text in dash


def test_commitment_form_and_bad_month_export(budget_app):
    app, _ = budget_app
    client = app.test_client()
    token = csrf(client)
    r = client.post('/finance/commitments', data={'csrf_token': token, 'name': 'Paycheck', 'direction': 'in', 'amount': '3000',
                                                  'amount_kind': 'exact', 'cadence': 'biweekly', 'next_date': '2026-10-02',
                                                  'end_date': '', 'account_id': CHECKING, 'category_id': '', 'status': 'active',
                                                  'notes': ''})
    assert r.status_code == 302
    assert b'Paycheck' in client.get('/finance/commitments').data
    assert client.get('/finance/exports/transactions.csv?month=bad').status_code == 400
    assert client.get('/finance/exports/transactions.csv').status_code == 400


def test_limits_settings(budget_app):
    app, _ = budget_app
    client = app.test_client()
    token = csrf(client)
    assert client.post('/finance/budget/settings/limits', data={'csrf_token': token, 'checking_minimum': '1000',
                                                                'reserve_holds': ''}).status_code == 302
    assert b'1000.00' in client.get('/finance/budget/settings').data
    assert client.post('/finance/budget/settings/limits', data={'csrf_token': token, 'checking_minimum': 'abc',
                                                                'reserve_holds': ''}).status_code == 400


def test_forecast_page_lists_undated_commitments(budget_app):
    app, _ = budget_app
    page = app.test_client().get('/finance/forecast').data
    assert b'Mortgage' in page and b'need a date' in page
