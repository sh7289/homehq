"""Private household worksheet; bank access remains exclusive to the sync CLI."""
import hmac
import os
import sqlite3
import stat
from contextlib import contextmanager
from datetime import date, datetime, timezone
from functools import wraps
from pathlib import Path

from flask import abort, current_app, redirect, render_template, request, session, url_for
from flask_login import current_user

import finance_book
import finance_payments
import finance_store
from mfa_security import csrf_token, require_recent_mfa

ROOT = Path(__file__).resolve().parent
BUCKETS = {'liquid': 'Liquid', 'illiquid': 'Illiquid', 'debt': 'Debts', 'unassigned': 'Needs grouping'}


class UpgradeNeeded(Exception):
    pass


def _enabled(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_app.config['FINANCE_ENABLED']:
            abort(404)
        return fn(*args, **kwargs)
    return wrapped


def _csrf(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if request.method == 'POST':
            expected = session.get('csrf_token', '')
            supplied = request.form.get('csrf_token', '')
            if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
                abort(400, description='This form expired. Reload the page and try again.')
        return fn(*args, **kwargs)
    return wrapped


@contextmanager
def _open(write=False):
    path = finance_store._validate_path(current_app.config['FINANCE_DB_PATH'], str(ROOT))
    if not os.path.lexists(path):
        raise FileNotFoundError
    info = os.lstat(path)
    parent = os.lstat(os.path.dirname(path))
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or parent.st_mode & 0o077:
        raise finance_store.FinanceStoreError('Finance database is not private.')
    conn = sqlite3.connect(Path(path).as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        if not finance_book.is_initialized(conn):
            raise UpgradeNeeded
        if not write:
            conn.execute('BEGIN')
        yield conn
    finally:
        conn.close()


def init_app(app):
    enabled = os.environ.get('HOMEHQ_FINANCE_ENABLED', '').lower() == 'true'
    path = os.environ.get('HOMEHQ_FINANCE_DB_PATH', '')
    if enabled:
        path = finance_store._validate_path(path, str(ROOT))
    app.config.update(FINANCE_ENABLED=enabled, FINANCE_DB_PATH=path)
    app.jinja_env.globals.setdefault('csrf_token', csrf_token)

    @app.template_filter('finance_amount')
    def finance_amount(value):
        result = format(value, ',f')
        if '.' not in result:
            return result + '.00'
        whole, fraction = result.split('.')
        return whole + '.' + fraction.ljust(2, '0')

    @app.template_filter('finance_age')
    def finance_age(value):
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        age = max(0, int((datetime.now(timezone.utc) - moment).total_seconds()))
        if age < 3600:
            return 'Under an hour ago'
        if age < 86400:
            hours = age // 3600
            return f'{hours} hour' + ('s' if hours != 1 else '') + ' ago'
        days = age // 86400
        return f'{days} day' + ('s' if days != 1 else '') + ' ago'

    @app.after_request
    def protect_finance(response):
        if request.path == '/finance' or request.path.startswith('/finance/'):
            response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                     'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin'})
        return response

    def render_worksheet(error=None, status=200):
        data, daily, snapshots = None, None, []
        unavailable = upgrade = demo = False
        try:
            with _open() as conn:
                data = finance_book.view(conn)
                daily = finance_store.dashboard(conn)
                demo = finance_store.get_store_mode(conn) == 'demo'
                snapshots = finance_book.list_snapshots(conn)
                if daily['history']:
                    start = date.fromisoformat(daily['history'][0]['date'])
                    duration = max(1, (date.fromisoformat(daily['history'][-1]['date']) - start).days)
                    for day in daily['history']:
                        day['chart_x'] = 15 + (date.fromisoformat(day['date']) - start).days * 570 / duration
        except FileNotFoundError:
            pass
        except UpgradeNeeded:
            upgrade = True
        except (OSError, sqlite3.Error, finance_store.FinanceStoreError, ValueError):
            unavailable = True
        return render_template('finance.html', active='finance', data=data, daily=daily,
                               snapshots=snapshots, unavailable=unavailable, upgrade=upgrade,
                               demo=demo, error=error, buckets=BUCKETS, today=date.today().isoformat()), status

    @app.route('/finance')
    @_enabled
    @require_recent_mfa
    def finance():
        return render_worksheet()

    def mutation_failed():
        return render_worksheet('Check the entered values and try again. Nothing was saved.', 400)

    @app.route('/finance/accounts/<account_id>/edit', methods=['GET', 'POST'])
    @_enabled
    @require_recent_mfa
    @_csrf
    def finance_account(account_id):
        error = None
        status = 200
        try:
            if request.method == 'POST':
                try:
                    with _open(write=True) as conn:
                        # Profile and manual-balance edits must succeed together.
                        conn.execute('BEGIN IMMEDIATE')
                        finance_book.update_account(conn, account_id,
                            nickname=request.form.get('nickname', ''), owner=request.form.get('owner', ''),
                            group_id=request.form.get('group_id', ''), position=request.form.get('position', '0'),
                            included='included' in request.form, debt_sign=request.form.get('debt_sign', 'unconfirmed'))
                        if 'balance' in request.form:
                            finance_book.update_manual(conn, account_id, balance=request.form['balance'],
                                                       balance_at=request.form.get('balance_at', ''))
                        conn.commit()
                    return redirect(url_for('finance'))
                except (ValueError, finance_store.FinanceStoreError):
                    error = 'Check the entered values and try again. Nothing was saved.'
                    status = 400
            with _open() as conn:
                data = finance_book.view(conn)
                account = next((a for a in data['accounts'] if a['id'] == account_id), None)
                if account is None:
                    abort(404)
                return render_template('finance_account.html', active='finance', account=account,
                                       groups=data['groups'], error=error), status
        except (OSError, sqlite3.Error, UpgradeNeeded):
            return render_worksheet('Account editing is unavailable. Check the finance setup.', 503)

    @app.route('/finance/manual', methods=['POST'])
    @_enabled
    @require_recent_mfa
    @_csrf
    def finance_manual():
        try:
            with _open(write=True) as conn:
                finance_book.add_manual(conn, nickname=request.form.get('nickname', ''),
                    currency=request.form.get('currency', ''), balance=request.form.get('balance', ''),
                    balance_at=request.form.get('balance_at', ''), owner=request.form.get('owner', ''),
                    group_id=request.form.get('group_id', ''), position=0)
            return redirect(url_for('finance'))
        except (OSError, sqlite3.Error, UpgradeNeeded):
            return render_worksheet('Saving is unavailable. Check the finance setup.', 503)
        except (ValueError, finance_store.FinanceStoreError):
            return mutation_failed()

    @app.route('/finance/groups', methods=['POST'])
    @_enabled
    @require_recent_mfa
    @_csrf
    def finance_group():
        try:
            with _open(write=True) as conn:
                finance_book.save_group(conn, group_id=request.form.get('group_id') or None,
                    name=request.form.get('name', ''), bucket=request.form.get('bucket', ''),
                    position=request.form.get('position', '0'))
            return redirect(url_for('finance'))
        except (OSError, sqlite3.Error, UpgradeNeeded):
            return render_worksheet('Saving is unavailable. Check the finance setup.', 503)
        except (ValueError, finance_store.FinanceStoreError):
            return mutation_failed()

    @app.route('/finance/payments', methods=['POST'])
    @_enabled
    @require_recent_mfa
    @_csrf
    def finance_payment():
        try:
            with _open(write=True) as conn:
                finance_payments.save(conn, payment_id=request.form.get('payment_id') or None,
                    card_id=request.form.get('card_id', ''), funding_id=request.form.get('funding_id', ''),
                    amount=request.form.get('amount', ''), payment_date=request.form.get('payment_date', ''),
                    status=request.form.get('status', 'planned'))
            return redirect(url_for('finance') + '#payments')
        except (OSError, sqlite3.Error, UpgradeNeeded):
            return render_worksheet('Saving is unavailable. Check the finance setup.', 503)
        except (ValueError, finance_store.FinanceStoreError):
            return mutation_failed()

    @app.route('/finance/snapshots', methods=['POST'])
    @_enabled
    @require_recent_mfa
    @_csrf
    def finance_save_snapshot():
        try:
            with _open(write=True) as conn:
                snapshot_id = finance_book.save_snapshot(conn, actor=current_user.id)
            return redirect(url_for('finance_snapshot', snapshot_id=snapshot_id))
        except (OSError, sqlite3.Error, UpgradeNeeded):
            return render_worksheet('Saving is unavailable. Check the finance setup.', 503)
        except (ValueError, finance_store.FinanceStoreError):
            return mutation_failed()

    @app.route('/finance/snapshots/<int:snapshot_id>')
    @_enabled
    @require_recent_mfa
    def finance_snapshot(snapshot_id):
        try:
            with _open() as conn:
                saved = finance_book.snapshot(conn, snapshot_id)
            return render_template('finance_snapshot.html', active='finance', data=saved, buckets=BUCKETS)
        except finance_store.FinanceStoreError:
            abort(404)
        except (OSError, sqlite3.Error, UpgradeNeeded):
            return render_worksheet('Saved snapshots are unavailable. Check the finance setup.', 503)
