"""Scoped, recent MFA. Secrets remain in per-user environment files."""
import functools
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import unquote, urlsplit

import pyotp
from flask import abort, current_app, redirect, render_template, request, session, url_for
from flask_login import current_user, login_fresh, login_required


SCHEMA = """
CREATE TABLE IF NOT EXISTS mfa_enrollments (
 username TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, last_step INTEGER NOT NULL DEFAULT -1,
 failures INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mfa_recovery (
 username TEXT NOT NULL, code_hash TEXT NOT NULL, PRIMARY KEY(username, code_hash)
);
"""


def fingerprint(secret):
    return hashlib.sha256(secret.encode('ascii')).hexdigest()


def enroll(conn, username, secret, recovery_codes, replace=False):
    """Register only a credential fingerprint and high-entropy recovery hashes."""
    pyotp.TOTP(secret).now()  # Validate the secret before modifying state.
    conn.executescript(SCHEMA)
    conn.execute('BEGIN IMMEDIATE')
    try:
        exists = conn.execute('SELECT 1 FROM mfa_enrollments WHERE username=?', (username,)).fetchone()
        if exists and not replace:
            raise ValueError('Enrollment already exists; explicit replacement required.')
        conn.execute('DELETE FROM mfa_recovery WHERE username=?', (username,))
        conn.execute('INSERT OR REPLACE INTO mfa_enrollments(username,fingerprint) VALUES (?,?)', (username, fingerprint(secret)))
        conn.executemany('INSERT INTO mfa_recovery VALUES (?,?)', [(username, hashlib.sha256(code.encode()).hexdigest()) for code in recovery_codes])
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


def safe_next(value):
    value = value or '/finance'
    decoded = unquote(value)
    try:
        parsed = urlsplit(decoded)
    except ValueError:
        return '/finance'
    if parsed.scheme or parsed.netloc or not decoded.startswith('/') or decoded.startswith('//') or '\\' in decoded or any(ord(c) < 32 for c in decoded):
        return '/finance'
    return value


def _secret(username):
    if username not in current_app.extensions['homehq_mfa']['users']:
        return None
    for slot in (1, 2):
        if os.environ.get(f'HOMEHQ_USER{slot}_NAME') == username:
            value = os.environ.get(f'HOMEHQ_USER{slot}_TOTP_SECRET', '')
            try:
                pyotp.TOTP(value).now()
                if len(value) >= 16:
                    return value
            except (ValueError, TypeError):
                pass
    return None


def _connection():
    conn = current_app.extensions['homehq_mfa']['get_db']()
    # execute() preserves the caller's transaction; executescript() commits it.
    for statement in SCHEMA.split(';'):
        if statement.strip():
            conn.execute(statement)
    return conn


def _enrolled(conn, username, secret):
    row = conn.execute('SELECT fingerprint,last_step,failures,locked_until FROM mfa_enrollments WHERE username=?', (username,)).fetchone()
    return row if row and secret and hmac.compare_digest(row[0], fingerprint(secret)) else None


def has_recent_mfa():
    if not current_user.is_authenticated or not login_fresh():
        return False
    grant = session.get('mfa_grant')
    secret = _secret(current_user.id)
    if not isinstance(grant, dict) or not secret:
        return False
    stored_fingerprint = grant.get('fingerprint')
    if not isinstance(stored_fingerprint, str) or not stored_fingerprint.isascii():
        return False
    stamp = grant.get('at')
    return (grant.get('username') == current_user.id
            and hmac.compare_digest(stored_fingerprint, fingerprint(secret))
            and isinstance(stamp, (int, float)) and 0 <= time.time() - stamp <= 900
            and bool(_enrolled(_connection(), current_user.id, secret)))


def require_recent_mfa(view):
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not login_fresh():
            session.pop('mfa_grant', None)
            return redirect(url_for('login'))
        if not has_recent_mfa():
            return redirect(url_for('mfa', next=safe_next(request.full_path.rstrip('?'))))
        return view(*args, **kwargs)
    return wrapped


def _verify(conn, username, secret, code, recovery):
    now = time.time()
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = _enrolled(conn, username, secret)
        if not row:
            conn.rollback()
            return 503
        if row[3] > now:
            conn.rollback()
            return 429
        accepted_step = None
        accepted = False
        if recovery and not code and len(recovery) <= 256:
            digest = hashlib.sha256(recovery.encode()).hexdigest()
            accepted = conn.execute('DELETE FROM mfa_recovery WHERE username=? AND code_hash=?', (username, digest)).rowcount == 1
        elif not recovery and len(code) == 6 and code.isascii() and code.isdigit():
            step = int(now // 30)
            totp = pyotp.TOTP(secret)
            for candidate in (step, step - 1, step + 1):
                if candidate > row[1] and hmac.compare_digest(totp.at(candidate * 30), code):
                    accepted_step = candidate
                    accepted = True
                    break
        if accepted:
            conn.execute('UPDATE mfa_enrollments SET failures=0,locked_until=0,last_step=? WHERE username=?', (accepted_step if accepted_step is not None else row[1], username))
            conn.commit()
            return 200
        failures = (0 if row[3] and row[3] <= now else row[2]) + 1
        conn.execute('UPDATE mfa_enrollments SET failures=?,locked_until=? WHERE username=?', (failures, now + 300 if failures >= 5 else 0, username))
        conn.commit()
        return 429 if failures >= 5 else 401
    except Exception:
        conn.rollback()
        raise


def init_app(app, get_db, users):
    app.extensions['homehq_mfa'] = {'get_db': get_db, 'users': users}
    app.jinja_env.globals['csrf_token'] = csrf_token

    @app.before_request
    def scoped_csrf():
        if request.method == 'POST' and request.endpoint in ('login', 'mfa'):
            expected = session.get('csrf_token')
            supplied = request.form.get('csrf_token', '')
            if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
                abort(400, description='This form expired. Reload the page and try again.')

    @app.after_request
    def private_headers(response):
        if request.endpoint in ('mfa', 'login'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['Referrer-Policy'] = 'same-origin'
        return response

    @app.route('/mfa', methods=['GET', 'POST'])
    @login_required
    def mfa():
        if not login_fresh():
            session.pop('mfa_grant', None)
            return redirect(url_for('login'))
        username = current_user.id
        secret = _secret(username)
        conn = _connection()
        destination = safe_next(request.values.get('next'))
        status = 200 if _enrolled(conn, username, secret) else 503
        error = None
        if status == 503:
            error = 'Authenticator setup is needed. Ask your household administrator to enroll your account.'
        elif request.method == 'POST':
            status = _verify(conn, username, secret, request.form.get('code', '').strip(), request.form.get('recovery_code', '').strip())
            if status == 200:
                session['mfa_grant'] = {'username': username, 'at': time.time(), 'fingerprint': fingerprint(secret)}
                session['csrf_token'] = secrets.token_urlsafe(32)
                return redirect(destination)
            error = 'Too many attempts. Try again in five minutes.' if status == 429 else 'That code could not be verified. Try a fresh code or a recovery code.'
        return render_template('mfa.html', error=error, enrolled=status != 503, next_path=destination, active='finance'), status
