"""Canonical transaction ledger behavior (product spec acceptance tests 4, 5, 6, 9, 11)."""
from decimal import Decimal

import pytest

import finance_ledger as L
from finance_store import FinanceStoreError
from test_finance_budget import ledger_db, cat, CHECKING, CARD_S, CARD_H


def alloc(conn, name, person, amount):
    return dict(category_id=cat(conn, name), person=person, amount=amount)


def test_card_owner_does_not_decide_person(tmp_path):  # Test 4
    conn = ledger_db(tmp_path)
    t = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-03', amount='-100', description='KROGER #123', actor='steve')
    L.classify(conn, t, kind='expense', allocations=[alloc(conn, 'Groceries and household essentials', 'shared', '-100')], actor='steve')
    rows = conn.execute('SELECT person, amount FROM finance_txn_allocations WHERE txn_id=?', (t,)).fetchall()
    assert [(r['person'], r['amount']) for r in rows] == [('shared', '-100.00')]


def test_split_must_sum_exactly(tmp_path):  # Test 5
    conn = ledger_db(tmp_path)
    t = L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-04', amount='-200', description='TARGET', actor='h')
    parts = [alloc(conn, 'Groceries and household essentials', 'shared', '-80'),
             alloc(conn, 'Gifts and Christmas', 'shared', '-70'),
             alloc(conn, 'Heather personal', 'Heather', '-50')]
    with pytest.raises(FinanceStoreError):
        L.classify(conn, t, kind='expense', allocations=parts[:2], actor='h')
    L.classify(conn, t, kind='expense', allocations=parts, actor='h')
    assert conn.execute('SELECT COUNT(*) FROM finance_txns').fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM finance_txn_allocations WHERE txn_id=?', (t,)).fetchone()[0] == 3


def test_card_payment_link_removes_allocations_and_needs_card(tmp_path):  # Test 6
    conn = ledger_db(tmp_path)
    out = L.create_txn(conn, account_id=CHECKING, txn_date='2026-11-20', amount='-500', description='CARD PAYMENT', actor='s')
    inn = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-21', amount='500', description='PAYMENT THANK YOU', actor='s')
    L.link(conn, kind='card_payment', from_id=out, to_id=inn, actor='s')
    assert {r['kind'] for r in conn.execute('SELECT kind FROM finance_txns')} == {'card_payment'}
    other = L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-21', amount='500', description='X', actor='s')
    card_out = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-21', amount='-500', description='Y', actor='s')
    with pytest.raises(FinanceStoreError):
        L.link(conn, kind='card_payment', from_id=card_out, to_id=other, actor='s')
    with pytest.raises(FinanceStoreError):
        L.link(conn, kind='transfer', from_id=out, to_id=other, actor='s')  # already linked


def test_refund_restores_category(tmp_path):  # Test 9
    conn = ledger_db(tmp_path)
    hotel = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-02', amount='-500', description='HOTEL', actor='s')
    L.classify(conn, hotel, kind='expense', allocations=[alloc(conn, 'Travel', 'shared', '-500')], actor='s')
    refund = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-09', amount='500', description='HOTEL', actor='s')
    L.link(conn, kind='refund', from_id=refund, to_id=hotel, actor='s')
    r = L.get_txn(conn, refund)
    assert r['kind'] == 'refund' and r['allocations'][0]['category_id'] == cat(conn, 'Travel')
    assert r['allocations'][0]['amount'] == Decimal('500.00')
    too_big = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-10', amount='600', description='HOTEL', actor='s')
    with pytest.raises(FinanceStoreError):
        L.link(conn, kind='refund', from_id=too_big, to_id=hotel, actor='s')


def test_pending_replacement_and_unlink(tmp_path):  # Test 11
    conn = ledger_db(tmp_path)
    p = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-05', amount='-50', description='BISTRO', actor='s', status='pending')
    L.classify(conn, p, kind='expense', allocations=[alloc(conn, 'Shared dining and entertainment', 'shared', '-50')], actor='s')
    q = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-06', amount='-60', description='BISTRO', actor='s')
    link_id = L.link(conn, kind='pending_posted', from_id=p, to_id=q, actor='s')
    assert L.get_txn(conn, p)['status'] == 'replaced'
    assert L.get_txn(conn, q)['allocations'][0]['amount'] == Decimal('-60.00')
    L.unlink(conn, link_id, actor='s')
    assert L.get_txn(conn, p)['status'] == 'pending'


def test_suggestion_from_prior_acceptance_is_marked(tmp_path):
    conn = ledger_db(tmp_path)
    a = L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-01', amount='-30', description='CHIPOTLE 0412', actor='h')
    L.classify(conn, a, kind='expense', allocations=[alloc(conn, 'Shared dining and entertainment', 'shared', '-30')], actor='h')
    b = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-08', amount='-41.20', description='CHIPOTLE 0977', actor='s')
    t = L.get_txn(conn, b)
    assert t['review'] == 'suggested' and t['allocations'] == []
    assert L.accept_suggestions(conn, [b], actor='s') == 1
    assert L.get_txn(conn, b)['allocations'][0]['amount'] == Decimal('-41.20')


def test_expense_sign_and_person_validation(tmp_path):
    conn = ledger_db(tmp_path)
    t = L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-01', amount='25', description='?', actor='h')
    with pytest.raises(FinanceStoreError):
        L.classify(conn, t, kind='expense', allocations=[alloc(conn, 'Household wants', 'shared', '25')], actor='h')
    with pytest.raises(FinanceStoreError):
        L.classify(conn, t, kind='refund', allocations=[alloc(conn, 'Household wants', 'Bob', '25')], actor='h')
    with pytest.raises(FinanceStoreError):
        L.classify(conn, t, kind='refund', allocations=[alloc(conn, 'Transfers', 'shared', '25')], actor='h')
    with pytest.raises(FinanceStoreError):
        L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-01', amount='0', description='?', actor='h')


def test_void_and_candidates(tmp_path):
    conn = ledger_db(tmp_path)
    out = L.create_txn(conn, account_id=CHECKING, txn_date='2026-11-20', amount='-500', description='TRANSFER', actor='s')
    far = L.create_txn(conn, account_id=CARD_S, txn_date='2026-12-20', amount='500', description='PAYMENT', actor='s')
    near = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-22', amount='500', description='PAYMENT', actor='s')
    assert [c['id'] for c in L.link_candidates(conn, out, 'card_payment')] == [near]
    L.void(conn, near, actor='s')
    assert L.link_candidates(conn, out, 'card_payment') == []
    assert far


def test_review_inbox_shows_undecided_and_unmatched(tmp_path):
    conn = ledger_db(tmp_path)
    done = L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-01', amount='-30', description='SHOP', actor='h')
    L.classify(conn, done, kind='expense', allocations=[alloc(conn, 'Household wants', 'shared', '-30')], actor='h')
    open_item = L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-02', amount='-12', description='OTHER', actor='h')
    lone = L.create_txn(conn, account_id=CHECKING, txn_date='2026-11-03', amount='-100', description='TO SAVINGS', actor='h')
    L.classify(conn, lone, kind='transfer', allocations=[], actor='h')
    manual = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-04', amount='-9', description='A', actor='h')
    assert {r['id'] for r in L.transactions(conn, view='review')} == {open_item, lone, manual}
    assert len(L.transactions(conn, view='all', month='2026-11')) == 4
    assert [r['id'] for r in L.transactions(conn, view='all', category_id=cat(conn, 'Household wants'))] == [done]
    assert manual


def test_keep_both_clears_duplicate_flag(tmp_path):
    import finance_import as I
    from datetime import datetime, timezone
    conn = ledger_db(tmp_path)
    L.create_txn(conn, account_id=CHECKING, txn_date='2026-11-02', amount='-20', description='CASH', actor='s')
    b = I.stage(conn, account_id=CHECKING, data=b'Date,Description,Amount\n2026-11-02,ATM,-20\n', label='x', actor='s',
                now=datetime(2026, 12, 1, tzinfo=timezone.utc))
    I.commit(conn, b, {'date': 0, 'description': 1, 'amount': 2, 'sign': 'outflow_negative'}, period_start='2026-11-01',
             period_end='2026-11-30', actor='s', now=datetime(2026, 12, 1, tzinfo=timezone.utc))
    flagged = conn.execute('SELECT id FROM finance_txns WHERE possible_duplicate_of IS NOT NULL').fetchone()['id']
    assert flagged in {r['id'] for r in L.transactions(conn, view='review')}
    L.keep_both(conn, flagged, 's')
    assert L.get_txn(conn, flagged)['possible_duplicate_of'] is None


def test_standalone_suggest_commits_and_leaves_no_open_transaction(tmp_path):
    conn = ledger_db(tmp_path)
    a = L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-01', amount='-30', description='CAFE', actor='h')
    b = L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-02', amount='-31', description='CAFE', actor='h')
    L.classify(conn, a, kind='expense', allocations=[alloc(conn, 'Shared dining and entertainment', 'shared', '-30')], actor='h')
    L.suggest(conn, b)
    assert not conn.in_transaction
