"""Budgeting pages inside Finance: month view, transaction review, imports, settings.

Every route reuses the Finance gates from ``finance_routes`` (feature flag, login
plus recent MFA, session CSRF on POST) and its private database opener. Routes are
registered from ``finance_routes.init_app`` so those gates are read at registration.
"""
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from functools import wraps

from flask import abort, redirect, render_template, request, url_for
from flask_login import current_user

import finance_budget
import finance_import
import finance_ledger
import finance_ledger_math
from finance_store import FinanceStoreError, _utc

ROLES = {'checking': 'Checking', 'savings': 'Savings', 'reserve': 'Reserve', 'card': 'Credit card', 'hsa': 'HSA',
         'loan': 'Loan', 'other': 'Other'}
KINDS = {'expense': 'Expense', 'income': 'Income', 'refund': 'Refund', 'reimbursement': 'Reimbursement',
         'transfer': 'Transfer between accounts', 'card_payment': 'Card payment'}
LINKS = {'transfer': 'Transfer', 'card_payment': 'Card payment', 'refund': 'Refund of', 'pending_posted': 'Pending → posted'}
TYPES = {'operating': 'Operating', 'capped': 'Monthly capped', 'sinking': 'Sinking fund', 'income': 'Income',
         'savings': 'Savings', 'transfer': 'Transfer', 'other': 'Other'}
SIGNS = {'outflow_negative': 'Money out is negative', 'outflow_positive': 'Money out is positive',
         'debit_credit': 'Separate debit and credit columns'}
MAP_FIELDS = ('date', 'description', 'amount', 'debit', 'credit', 'posted_date', 'status', 'id')
STATES = {
    'missing': ('First sync needed', 'Complete the server setup and run your first sync before budgeting.'),
    'upgrade': ('Finance update needed', 'Ask your household administrator to run the finance database upgrade.'),
    'budget_upgrade': ('Budgeting update needed', 'Your balances are unaffected. Ask your household administrator '
                                                  'to run the finance database upgrade to turn on budgeting.'),
    'unavailable': ('Budgeting is unavailable', 'The private finance store could not be read. Check the finance setup.'),
}


class BudgetUpgradeNeeded(Exception):
    pass


def _today():
    return _utc().date()


def _month_or_400(value):
    try:
        return finance_budget._month(value)
    except FinanceStoreError:
        abort(400, description='Choose a month as YYYY-MM.')


def _shift(month, delta):
    first = date.fromisoformat(month + '-01')
    moved = (first + timedelta(days=32 * delta)).replace(day=1) if delta > 0 else (first - timedelta(days=1)).replace(day=1)
    return moved.strftime('%Y-%m')


def _safe_next(value, fallback):
    if isinstance(value, str) and value.startswith('/finance/') and '//' not in value and '\\' not in value:
        return value
    return fallback


def register(app):
    import finance_routes
    gates = (finance_routes._enabled, finance_routes.require_recent_mfa, finance_routes._csrf)
    opener, upgrade_error = finance_routes._open, finance_routes.UpgradeNeeded

    def actor():
        return current_user.id

    @contextmanager
    def budget_db(write=False):
        with opener(write=write) as conn:
            if not finance_budget.is_initialized(conn):
                raise BudgetUpgradeNeeded
            yield conn

    def state(kind, status):
        title, message = STATES[kind]
        return render_template('finance_budget_state.html', active='finance', title=title, message=message), status

    def route(path, endpoint, methods=('GET',)):
        def decorate(fn):
            @wraps(fn)
            def guarded(*args, **kwargs):
                failure = 200 if request.method == 'GET' else 503
                try:
                    return fn(*args, **kwargs)
                except FileNotFoundError:
                    return state('missing', failure)
                except upgrade_error:
                    return state('upgrade', failure)
                except BudgetUpgradeNeeded:
                    return state('budget_upgrade', failure)
                except (OSError, sqlite3.Error):
                    return state('unavailable', 503)
            view = guarded
            for gate in reversed(gates):
                view = gate(view)
            app.add_url_rule(path, endpoint, view, methods=list(methods))
            return fn
        return decorate

    def accounts(conn, included_only=False):
        settings = finance_budget.account_settings(conn)
        names = finance_ledger._display(conn)
        rows = []
        for row in conn.execute("SELECT id, currency, source FROM finance_accounts WHERE currency != 'NONFINANCIAL' "
                                'ORDER BY position, nickname, label, id'):
            setting = settings.get(row['id'])
            if included_only and not (setting and setting['included']):
                continue
            rows.append(dict(id=row['id'], name=names.get(row['id'], ''), currency=row['currency'], source=row['source'],
                             configured=setting is not None, included=bool(setting and setting['included']),
                             role=setting['role'] if setting else ''))
        return rows

    # ---- Month view -------------------------------------------------------

    @route('/finance/budget', 'finance_budget')
    def budget_month():
        month = _month_or_400(request.args.get('month') or _today().strftime('%Y-%m'))
        with budget_db() as conn:
            summary = finance_ledger_math.month_summary(conn, month, _today())
        return render_template('finance_budget.html', active='finance', tab='budget', s=summary,
                               prev_month=_shift(month, -1), next_month=_shift(month, 1))

    # ---- Transactions -----------------------------------------------------

    def inbox(error=None, status=200):
        view = 'all' if request.args.get('view') == 'all' else 'review'
        month = request.args.get('month') or None
        if month:
            month = _month_or_400(month)
        category = request.args.get('category') or None
        if category and not category.isdigit():
            abort(400)
        with budget_db() as conn:
            rows = finance_ledger.transactions(conn, view=view, month=month, account_id=request.args.get('account') or None,
                                               category_id=int(category) if category else None)
            return render_template('finance_transactions.html', active='finance', tab='transactions', rows=rows, view=view,
                                   month=month or '', account=request.args.get('account', ''), category=category or '',
                                   accounts=accounts(conn), categories=finance_budget.categories(conn), error=error,
                                   accepted=request.args.get('accepted')), status

    @route('/finance/transactions', 'finance_transactions')
    def transactions_page():
        return inbox()

    @route('/finance/transactions/accept', 'finance_accept_suggested', methods=('POST',))
    def accept_suggested():
        ids = request.form.getlist('txn_id')
        try:
            with budget_db(write=True) as conn:
                count = finance_ledger.accept_suggestions(conn, ids[:200], actor())
        except FinanceStoreError as error:
            return inbox(str(error), 400)
        return redirect(url_for('finance_transactions', accepted=count))

    def new_form(error=None, status=200):
        with budget_db() as conn:
            return render_template('finance_txn_new.html', active='finance', tab='transactions', error=error,
                                   accounts=accounts(conn, included_only=True), today=_today().isoformat()), status

    @route('/finance/transactions/new', 'finance_txn_new', methods=('GET', 'POST'))
    def txn_new():
        if request.method == 'GET':
            return new_form()
        form = request.form
        try:
            with budget_db(write=True) as conn:
                txn_id = finance_ledger.create_txn(conn, account_id=form.get('account_id', ''), txn_date=form.get('txn_date', ''),
                                                   amount=form.get('amount', ''), description=form.get('description', ''),
                                                   status=form.get('status', 'posted'), actor=actor())
        except FinanceStoreError as error:
            return new_form(str(error), 400)
        return redirect(url_for('finance_txn', txn_id=txn_id))

    def detail(txn_id, error=None, status=200):
        with budget_db() as conn:
            try:
                txn = finance_ledger.get_txn(conn, txn_id)
            except FinanceStoreError:
                abort(404)
            kinds = ['transfer', 'card_payment', 'pending_posted'] + (['refund'] if txn['amount'] > 0 else [])
            candidates = {kind: finance_ledger.link_candidates(conn, txn['id'], kind) for kind in kinds}
            card_ids = {c['id'] for c in candidates['card_payment']}
            candidates['transfer'] = [c for c in candidates['transfer'] if c['id'] not in card_ids]
            lines = [dict(category_id=a['category_id'], person=a['person'], amount=a['amount'], note=a['note'])
                     for a in txn['allocations']]
            if not lines and txn['suggested_category_id']:
                lines = [dict(category_id=txn['suggested_category_id'], person=txn['suggested_person'], amount=txn['amount'], note='')]
            if not lines:
                lines = [dict(category_id=None, person='shared', amount=txn['amount'], note='')]
            if request.method == 'POST' and request.form.getlist('alloc_amount'):
                form = request.form
                lines = [dict(category_id=int(c) if c.isdigit() else None, person=p, amount=a, note=n) for c, p, a, n in
                         zip(form.getlist('alloc_category'), form.getlist('alloc_person'), form.getlist('alloc_amount'),
                             form.getlist('alloc_note'))]
            lines += [dict(category_id=None, person='shared', amount='', note='')] * max(0, 5 - len(lines))
            kind = txn['kind'] if txn['kind'] != 'unclassified' else (
                txn['suggested_kind'] or ('expense' if txn['amount'] < 0 else 'income'))
            payments = [dict(r) for r in conn.execute(
                "SELECT id, payment_date, amount FROM finance_payments WHERE status IN ('planned','scheduled','paid') "
                'ORDER BY payment_date DESC LIMIT 20')]
            return render_template('finance_txn.html', active='finance', tab='transactions', txn=txn, lines=lines,
                                   kind=request.form.get('kind', kind) if request.method == 'POST' else kind,
                                   categories=[c for c in finance_budget.categories(conn, active_only=True) if c['type'] != 'transfer'],
                                   people=['shared'] + finance_budget.people(conn), candidates=candidates, payments=payments,
                                   error=error, next=_safe_next(request.values.get('next'), ''),
                                   kinds=KINDS, links=LINKS), status

    @route('/finance/transactions/<int:txn_id>', 'finance_txn')
    def txn_detail(txn_id):
        return detail(txn_id)

    def after_edit(txn_id):
        return redirect(_safe_next(request.form.get('next'), url_for('finance_txn', txn_id=txn_id)))

    @route('/finance/transactions/<int:txn_id>/classify', 'finance_txn_classify', methods=('POST',))
    def txn_classify(txn_id):
        form = request.form
        try:
            with budget_db(write=True) as conn:
                txn = finance_ledger.get_txn(conn, txn_id)
                kind = form.get('kind', '')
                allocations = []
                if kind not in finance_ledger.MOVEMENT_KINDS:
                    for category, person, amount, note in zip(form.getlist('alloc_category'), form.getlist('alloc_person'),
                                                              form.getlist('alloc_amount'), form.getlist('alloc_note')):
                        if not category and not amount.strip():
                            continue
                        allocations.append(dict(category_id=category, person=person, amount=amount, note=note))
                    if len(allocations) == 1 and not allocations[0]['amount'].strip():
                        allocations[0]['amount'] = txn['amount']
                finance_ledger.classify(conn, txn['id'], kind=kind, allocations=allocations, actor=actor(),
                                        merchant=form.get('merchant'), note=form.get('note'))
                if form.get('keep_both'):
                    finance_ledger.keep_both(conn, txn['id'], actor())
        except FinanceStoreError as error:
            if str(error) == 'Transaction was not found.':
                abort(404)
            return detail(txn_id, str(error), 400)
        return after_edit(txn_id)

    @route('/finance/transactions/<int:txn_id>/link', 'finance_txn_link', methods=('POST',))
    def txn_link(txn_id):
        kind, other = request.form.get('kind', ''), request.form.get('other_id', '')
        try:
            with budget_db(write=True) as conn:
                txn = finance_ledger.get_txn(conn, txn_id)
                first, second = txn['id'], other
                if kind == 'pending_posted' and txn['status'] != 'pending':
                    first, second = other, txn['id']
                finance_ledger.link(conn, kind=kind, from_id=first, to_id=second, actor=actor(),
                                    payment_id=request.form.get('payment_id') or None)
        except FinanceStoreError as error:
            return detail(txn_id, str(error), 400)
        return after_edit(txn_id)

    @route('/finance/links/<int:link_id>/unlink', 'finance_txn_unlink', methods=('POST',))
    def txn_unlink(link_id):
        try:
            with budget_db(write=True) as conn:
                row = finance_ledger.unlink(conn, link_id, actor())
        except FinanceStoreError:
            abort(404)
        return redirect(url_for('finance_txn', txn_id=row['from_txn_id']))

    @route('/finance/transactions/<int:txn_id>/void', 'finance_txn_void', methods=('POST',))
    def txn_void(txn_id):
        try:
            with budget_db(write=True) as conn:
                finance_ledger.void(conn, txn_id, actor())
        except FinanceStoreError:
            abort(404)
        return redirect(url_for('finance_transactions'))

    # ---- Imports ----------------------------------------------------------

    def imports_page(error=None, status=200):
        with budget_db() as conn:
            return render_template('finance_imports.html', active='finance', tab='imports', error=error,
                                   accounts=accounts(conn, included_only=True), batches=finance_import.batches(conn)), status

    @route('/finance/imports', 'finance_imports', methods=('GET', 'POST'))
    def imports():
        if request.method == 'GET':
            return imports_page()
        upload = request.files.get('file')
        data = upload.read(finance_import.MAX_BYTES + 1) if upload else b''
        try:
            with budget_db(write=True) as conn:
                batch_id = finance_import.stage(conn, account_id=request.form.get('account_id', ''), data=data,
                                                label=request.form.get('label') or 'Import', actor=actor())
        except FinanceStoreError as error:
            return imports_page(str(error), 400)
        return redirect(url_for('finance_import', batch_id=batch_id))

    def mapping_from(values):
        mapping = {'sign': values.get('sign', 'outflow_negative')}
        for field in MAP_FIELDS:
            value = values.get('map_' + field, '')
            if value.isdigit():
                mapping[field] = int(value)
        return mapping

    def import_page(batch_id, error=None, status=200, mapping=None):
        with budget_db() as conn:
            batch = conn.execute('SELECT * FROM finance_import_batches WHERE id=?', (batch_id,)).fetchone()
            if batch is None:
                abort(404)
            batch = dict(batch, account_name=finance_ledger._display(conn).get(batch['account_id'], ''))
            preview = columns = None
            mapping_error = None
            if batch['state'] == 'staged':
                columns = finance_import.header(conn, batch_id)
                if mapping is None:
                    mapping = mapping_from(request.args) if 'map_date' in request.args else finance_import.default_mapping(conn, batch_id)
                try:
                    preview = finance_import.preview(conn, batch_id, mapping)
                except FinanceStoreError as problem:
                    mapping_error = str(problem)
            return render_template('finance_import.html', active='finance', tab='imports', batch=batch, columns=columns,
                                   mapping=mapping or {}, preview=preview, mapping_error=mapping_error, error=error,
                                   signs=SIGNS, fields=MAP_FIELDS), status

    @route('/finance/imports/<int:batch_id>', 'finance_import')
    def import_detail(batch_id):
        return import_page(batch_id)

    @route('/finance/imports/<int:batch_id>/commit', 'finance_import_commit', methods=('POST',))
    def import_commit(batch_id):
        mapping = mapping_from(request.form)
        if request.form.get('action') != 'commit':
            return redirect(url_for('finance_import', batch_id=batch_id, **{'map_' + k: v for k, v in mapping.items() if k != 'sign'},
                                    sign=mapping['sign']))
        try:
            with budget_db(write=True) as conn:
                account = conn.execute('SELECT account_id FROM finance_import_batches WHERE id=?', (batch_id,)).fetchone()
                finance_import.commit(conn, batch_id, mapping, period_start=request.form.get('period_start', ''),
                                      period_end=request.form.get('period_end', ''), actor=actor())
        except FinanceStoreError as error:
            if str(error) == 'Import was not found.':
                abort(404)
            return import_page(batch_id, str(error), 400, mapping)
        return redirect(url_for('finance_transactions', account=account['account_id']))

    @route('/finance/imports/<int:batch_id>/discard', 'finance_import_discard', methods=('POST',))
    def import_discard(batch_id):
        try:
            with budget_db(write=True) as conn:
                finance_import.discard(conn, batch_id)
        except FinanceStoreError:
            abort(404)
        return redirect(url_for('finance_imports'))

    @route('/finance/imports/<int:batch_id>/rollback', 'finance_import_rollback', methods=('POST',))
    def import_rollback(batch_id):
        try:
            with budget_db(write=True) as conn:
                finance_import.rollback(conn, batch_id, actor=actor(), confirm_edited=bool(request.form.get('confirm_edited')))
        except FinanceStoreError as error:
            if str(error) == 'Import was not found.':
                abort(404)
            return import_page(batch_id, str(error), 400)
        return redirect(url_for('finance_imports'))

    # ---- Settings ---------------------------------------------------------

    def settings_page(error=None, status=200):
        month = _today().strftime('%Y-%m')
        with budget_db() as conn:
            categories = finance_budget.categories(conn)
            for category in categories:
                category['target'] = finance_budget.target_for(conn, category['id'], month)
            coverage = {}
            for row in conn.execute('SELECT account_id, MIN(start_date) AS first, MAX(end_date) AS last, COUNT(*) AS spans '
                                    'FROM finance_coverage GROUP BY account_id'):
                coverage[row['account_id']] = dict(row)
            return render_template('finance_budget_settings.html', active='finance', tab='settings', error=error,
                                   people=finance_budget.people(conn), accounts=accounts(conn), coverage=coverage,
                                   categories=categories, month=month, today=_today().isoformat(),
                                   roles=ROLES, types=TYPES), status

    @route('/finance/budget/settings', 'finance_budget_settings')
    def settings():
        return settings_page()

    @route('/finance/budget/settings/<section>', 'finance_budget_settings_save', methods=('POST',))
    def settings_save(section):
        form = request.form
        try:
            with budget_db(write=True) as conn:
                if section == 'people':
                    finance_budget.set_people(conn, [n.strip() for n in form.get('people', '').split(',') if n.strip()])
                elif section == 'account':
                    finance_budget.set_account(conn, form.get('account_id', ''), included='included' in form,
                                               role=form.get('role', ''))
                elif section == 'category':
                    finance_budget.save_category(
                        conn, category_id=form.get('category_id') or None, name=form.get('name', ''),
                        parent_id=form.get('parent_id') or None, type=form.get('type', ''),
                        default_person=form.get('default_person', ''), active='active' in form,
                        rollover=form.get('rollover', 'reset'), rollover_cap=form.get('rollover_cap') or None,
                        position=form.get('position', '0') or '0', policy_includes=form.get('policy_includes', ''),
                        policy_excludes=form.get('policy_excludes', ''), notes=form.get('notes', ''))
                elif section == 'target':
                    finance_budget.set_target(conn, category_id=form.get('category_id', ''),
                                              effective_month=form.get('effective_month', ''), amount=form.get('amount', ''),
                                              basis=form.get('basis', ''), note=form.get('note', ''), actor=actor())
                elif section == 'coverage':
                    finance_budget.declare_coverage(conn, account_id=form.get('account_id', ''), start=form.get('start', ''),
                                                    end=form.get('end', ''), actor=actor())
                else:
                    abort(404)
        except FinanceStoreError as error:
            return settings_page(str(error), 400)
        return redirect(url_for('finance_budget_settings') + '#' + section)
