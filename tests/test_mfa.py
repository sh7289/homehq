import os
import sqlite3

import pyotp
import pytest

SECRET = 'JBSWY3DPEHPK3PXP'


def login(client):
    client.get('/login')
    with client.session_transaction() as state:
        token = state['csrf_token']
    return client.post('/login', data={'username': 'alice', 'password': 'password1', 'csrf_token': token})


def verify(client, code, **extra):
    client.get('/mfa')
    with client.session_transaction() as state:
        token = state['csrf_token']
    return client.post('/mfa', data={'code': code, 'csrf_token': token, **extra})


@pytest.fixture
def enrolled(app, monkeypatch):
    import mfa_security as mfa
    monkeypatch.setenv('HOMEHQ_USER1_TOTP_SECRET', SECRET)
    monkeypatch.setattr(mfa.time, 'time', lambda: 1800000000)
    conn = sqlite3.connect(os.environ['HOMEHQ_DB_PATH'])
    mfa.enroll(conn, 'alice', SECRET, ['recovery-code-with-high-entropy'])
    conn.close()
    @app.route('/private-test')
    @mfa.require_recent_mfa
    def private():
        return 'private'
    return app


def test_csrf_required(app):
    client = app.test_client()
    assert client.post('/login', data={'username':'alice','password':'password1'}).status_code == 400
    assert login(client).status_code == 302
    assert client.post('/mfa', data={'code':'123456'}).status_code == 400


def test_challenge_success_expiry_and_replay(enrolled, monkeypatch):
    import mfa_security as mfa
    client = enrolled.test_client()
    login(client)
    assert client.get('/').status_code == 200
    assert '/mfa' in client.get('/private-test').location
    code = pyotp.TOTP(SECRET).at(1800000000)
    assert verify(client, code).status_code == 302
    assert client.get('/private-test').status_code == 200
    other = enrolled.test_client()
    login(other)
    assert verify(other, code).status_code == 401
    monkeypatch.setattr(mfa.time, 'time', lambda: 1800000901)
    assert '/mfa' in client.get('/private-test').location


def test_recovery_single_use_and_throttle(enrolled):
    client = enrolled.test_client()
    login(client)
    assert verify(client, '', recovery_code='recovery-code-with-high-entropy', next='https://evil.example').location == '/finance'
    client.post('/logout')
    login(client)
    assert verify(client, '', recovery_code='recovery-code-with-high-entropy').status_code == 401
    for _ in range(3):
        assert verify(client, '000000').status_code == 401
    assert verify(client, '000000').status_code == 429
    other = enrolled.test_client()
    login(other)
    assert verify(other, pyotp.TOTP(SECRET).at(1800000000)).status_code == 429


def test_previous_window_logout_rotation_future(enrolled, monkeypatch):
    client = enrolled.test_client()
    login(client)
    assert verify(client, pyotp.TOTP(SECRET).at(1799999970)).status_code == 302
    with client.session_transaction() as state:
        state['mfa_grant']['at'] += 100
        state.modified = True
    assert '/mfa' in client.get('/private-test').location
    assert verify(client, pyotp.TOTP(SECRET).at(1800000000)).status_code == 302
    monkeypatch.setenv('HOMEHQ_USER1_TOTP_SECRET', pyotp.random_base32())
    assert '/mfa' in client.get('/private-test').location
    assert verify(client, '000000').status_code == 503
    client.post('/logout')
    with client.session_transaction() as state:
        assert 'mfa_grant' not in state


def test_missing_enrollment(app):
    client = app.test_client()
    login(client)
    assert client.get('/mfa').status_code == 503


def test_invalid_csrf_and_destinations(app):
    import mfa_security as mfa
    client=app.test_client()
    client.get('/login')
    assert client.post('/login', data={'csrf_token':'é'}).status_code == 400
    for target in ('//evil.example','https://[broken','/\\evil','/%2fexample','/x%0d%0aheader'):
        assert mfa.safe_next(target) == '/finance'


def test_unfresh_session_and_user_switch(enrolled):
    client=enrolled.test_client()
    login(client)
    assert verify(client, pyotp.TOTP(SECRET).at(1800000000)).status_code == 302
    with client.session_transaction() as state:
        state['_fresh'] = False
    assert '/login' in client.get('/private-test').location
    login(client)
    assert '/mfa' in client.get('/private-test').location


def test_bad_secret_fails_closed(app, monkeypatch):
    monkeypatch.setenv('HOMEHQ_USER1_TOTP_SECRET', 'this-is-not-a-base32-secret')
    client=app.test_client()
    login(client)
    assert client.get('/mfa').status_code == 503


def test_concurrent_totp_consumption_is_atomic(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import mfa_security as mfa
    path=tmp_path/'atomic.db'
    conn=sqlite3.connect(path)
    mfa.enroll(conn,'alice',SECRET,[])
    conn.close()
    code=pyotp.TOTP(SECRET).now()
    def attempt():
        connection=sqlite3.connect(path, timeout=5)
        try:
            return mfa._verify(connection,'alice',SECRET,code,'')
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:attempt(),range(2)))
    assert sorted(results) == [200,401]


def test_switch_user_clears_grant_and_private_page_headers(enrolled):
    client=enrolled.test_client()
    login(client)
    response=client.get('/mfa')
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert b'fonts.googleapis.com' not in response.data
    assert verify(client, pyotp.TOTP(SECRET).at(1800000000)).status_code == 302
    client.get('/login')
    with client.session_transaction() as state:
        token=state['csrf_token']
    client.post('/login',data={'username':'bob','password':'password1','csrf_token':token})
    with client.session_transaction() as state:
        assert state['_user_id'] == 'bob'
        assert 'mfa_grant' not in state
    assert '/mfa' in client.get('/private-test').location


def test_throttle_expires(enrolled, monkeypatch):
    import mfa_security as mfa
    client=enrolled.test_client()
    login(client)
    for _ in range(5):
        verify(client,'000000')
    monkeypatch.setattr(mfa.time,'time',lambda:1800000301)
    assert verify(client,pyotp.TOTP(SECRET).at(1800000301)).status_code == 302
