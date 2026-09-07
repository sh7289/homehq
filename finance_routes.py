"""Read-only finance views. Provider credentials never enter this module."""
import os
import sqlite3
import stat
from datetime import date
from pathlib import Path
from functools import wraps

from flask import abort, render_template, current_app

import finance_store
from mfa_security import require_recent_mfa

ROOT = Path(__file__).resolve().parent


def _enabled(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_app.config['FINANCE_ENABLED']:
            abort(404)
        return fn(*args, **kwargs)
    return wrapped


def init_app(app):
    enabled = os.environ.get('HOMEHQ_FINANCE_ENABLED', '').lower() == 'true'
    path = os.environ.get('HOMEHQ_FINANCE_DB_PATH', '')
    if enabled:
        path = finance_store._validate_path(path, str(ROOT))
    app.config.update(FINANCE_ENABLED=enabled, FINANCE_DB_PATH=path)

    @app.template_filter('finance_amount')
    def finance_amount(value):
        # Keep provider precision, including currencies with sub-cent units.
        result = format(value, ',f')
        if '.' not in result:
            return result + '.00'
        whole, fraction = result.split('.')
        return whole + '.' + fraction.ljust(2, '0')

    @app.after_request
    def protect_finance(response):
        from flask import request
        if request.path.rstrip('/') == '/finance':
            response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                     'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin'})
        return response

    @app.route('/finance')
    @_enabled
    @require_recent_mfa
    def finance():
        path = app.config['FINANCE_DB_PATH']
        data = None
        unavailable = False
        demo = False
        try:
            path = finance_store._validate_path(path, str(ROOT))
            if os.path.lexists(path):
                info = os.lstat(path)
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise finance_store.FinanceStoreError('Finance database is not private.')
                conn = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True, timeout=5)
                try:
                    conn.row_factory = sqlite3.Row
                    conn.execute('BEGIN')  # one consistent view across all queries
                    data = finance_store.dashboard(conn)
                    demo = finance_store.get_store_mode(conn) == 'demo'
                    if data['history']:
                        start = date.fromisoformat(data['history'][0]['date'])
                        duration = max(1, (date.fromisoformat(data['history'][-1]['date']) - start).days)
                        for day in data['history']:
                            day['chart_x'] = 15 + (date.fromisoformat(day['date']) - start).days * 570 / duration
                finally:
                    conn.close()
        except (OSError, sqlite3.Error, finance_store.FinanceStoreError, ValueError):
            unavailable = True
        return render_template('finance.html', active='finance', data=data,
                               unavailable=unavailable, demo=demo)
