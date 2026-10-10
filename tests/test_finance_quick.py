"""Quick categorize: one category (and person) per inbox row, saved together."""
import pytest

import finance_ledger as L
import finance_store
from finance_store import FinanceStoreError
from test_finance_budget import ledger_db, cat, CHECKING, CARD_S, CARD_H
from test_finance_routes import finance_app  # noqa: F401 (fixture)
from test_finance_budget_routes import budget_app, csrf  # noqa: F401 (fixture)


def txn(conn, amount, description='SHOP', account=CARD_S, day='2026-10-02'):
    return L.create_txn(conn, account_id=account, txn_date=day, amount=amount, description=description, actor='s')


def lines(conn, txn_id):
    t = L.get_txn(conn, txn_id)
    return t['kind'], t['review'], [(a['category_id'], a['person'], str(a['amount'])) for a in t['allocations']]


def test_money_out_is_an_expense_with_the_category_default_person(tmp_path):
    conn = ledger_db(tmp_path)
    t = txn(conn, '-20.00')
    L.quick_classify(conn, t, category_id=cat(conn, 'Heather personal'), person='', actor='s')
    assert lines(conn, t) == ('expense', 'accepted', [(cat(conn, 'Heather personal'), 'Heather', '-20.00')])
    u = txn(conn, '-9.00', 'OTHER')
    L.quick_classify(conn, u, category_id=cat(conn, 'Groceries and household essentials'), person='Steve', actor='s')
    assert lines(conn, u)[2][0][1] == 'Steve'


def test_money_in_is_income_or_reimbursement_by_category(tmp_path):
    conn = ledger_db(tmp_path)
    pay = txn(conn, '1000.00', 'PAYROLL', CHECKING)
    back = txn(conn, '35.00', 'VENMO', CHECKING)
    L.quick_classify(conn, pay, category_id=cat(conn, 'Income'), person='', actor='s')
    L.quick_classify(conn, back, category_id=cat(conn, 'Steve personal'), person='', actor='s')
    assert lines(conn, pay)[0] == 'income'
    assert lines(conn, back)[:2] == ('reimbursement', 'accepted')


def test_rows_needing_the_full_page_are_refused(tmp_path):
    conn = ledger_db(tmp_path)
    first = txn(conn, '-42.10', 'GROCER')
    dup = txn(conn, '-42.10', 'GROCER', day='2026-10-03')
    conn.execute('UPDATE finance_txns SET possible_duplicate_of=? WHERE id=?', (first, dup))
    with pytest.raises(FinanceStoreError):
        L.quick_classify(conn, dup, category_id=cat(conn, 'Groceries and household essentials'), person='', actor='s')
    out, inn = txn(conn, '-500', 'PAYMENT', CHECKING), txn(conn, '500', 'PAYMENT')
    L.link(conn, kind='card_payment', from_id=out, to_id=inn, actor='s')
    with pytest.raises(FinanceStoreError):
        L.quick_classify(conn, out, category_id=cat(conn, 'Groceries and household essentials'), person='', actor='s')


def test_unchanged_suggestion_keeps_its_suggested_kind(tmp_path):
    conn = ledger_db(tmp_path)
    first = txn(conn, '15.00', 'AMAZON REFUND', CARD_H)
    L.classify(conn, first, kind='refund', actor='s',
               allocations=[dict(category_id=cat(conn, 'Household wants'), person='shared', amount='15.00')])
    second = txn(conn, '8.00', 'AMAZON REFUND', CARD_H, day='2026-10-05')
    assert L.get_txn(conn, second)['review'] == 'suggested'
    L.quick_classify(conn, second, category_id=cat(conn, 'Household wants'), person='shared', actor='s')
    assert lines(conn, second)[0] == 'refund'


def test_inbox_saves_chosen_rows_skips_blank_ones_and_reports_bad_ones(budget_app):  # noqa: F811
    app, path = budget_app
    client = app.test_client()
    token = csrf(client)
    conn = finance_store.connect(str(path))
    good, blank, bad = txn(conn, '-20', 'A'), txn(conn, '-30', 'B'), txn(conn, '-40', 'C')
    groceries, personal = cat(conn, 'Groceries and household essentials'), cat(conn, 'Heather personal')
    conn.close()
    page = client.get('/finance/transactions').data
    assert f'name="category_{good}"'.encode() in page and b'Save categories' in page
    form = {'csrf_token': token, 'txn_id': [good, blank, bad], f'category_{good}': groceries, f'person_{good}': '',
            f'category_{blank}': '', f'person_{blank}': '', f'category_{bad}': personal, f'person_{bad}': 'Nobody'}
    r = client.post('/finance/transactions/categorize', data=form)
    assert r.status_code == 400 and b'household member' in r.data and b'Saved 1 category' in r.data
    conn = finance_store.connect(str(path))
    assert lines(conn, good)[1] == 'accepted'
    assert lines(conn, blank)[1] != 'accepted' and lines(conn, bad)[1] != 'accepted'
    conn.close()
    form = {'csrf_token': token, 'txn_id': [bad], f'category_{bad}': personal, f'person_{bad}': ''}
    r = client.post('/finance/transactions/categorize', data=form)
    assert r.status_code == 302 and 'saved=1' in r.location
    assert client.post('/finance/transactions/categorize', data={}).status_code == 400


def test_money_in_without_refund_matches_explains_reimbursement(budget_app):  # noqa: F811
    app, path = budget_app
    conn = finance_store.connect(str(path))
    back = txn(conn, '35.00', 'VENMO', CHECKING)
    conn.close()
    assert b'paying you back' in app.test_client().get(f'/finance/transactions/{back}').data


def classify_form(token, category_id, amount):
    return {'csrf_token': token, 'kind': 'expense', 'alloc_category': [category_id], 'alloc_person': ['shared'],
            'alloc_amount': [amount], 'alloc_note': ['']}


def test_saving_a_reviewed_transaction_moves_to_the_next_one_needing_review(budget_app):  # noqa: F811
    app, path = budget_app
    client = app.test_client()
    token = csrf(client)
    conn = finance_store.connect(str(path))
    newer, older = txn(conn, '-20', 'NEWER', day='2026-10-05'), txn(conn, '-30', 'OLDER', day='2026-10-02')
    groceries = cat(conn, 'Groceries and household essentials')
    conn.close()

    r = client.post(f'/finance/transactions/{newer}/classify', data=classify_form(token, groceries, '-20'))
    assert r.status_code == 302 and r.location.endswith(f'/finance/transactions/{older}?reviewed=1')
    assert b'1 left to review' in client.get(r.location).data

    r = client.post(f'/finance/transactions/{older}/classify', data=classify_form(token, groceries, '-30'))
    assert r.status_code == 302 and r.location.endswith('/finance/transactions?caught_up=1')
    assert b'All caught up' in client.get(r.location).data

    # Editing something already reviewed stays on that transaction.
    r = client.post(f'/finance/transactions/{newer}/classify', data=classify_form(token, groceries, '-20'))
    assert r.location.endswith(f'/finance/transactions/{newer}')
