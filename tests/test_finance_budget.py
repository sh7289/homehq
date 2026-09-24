"""Budget schema, settings, categories and targets using synthetic data only."""
from decimal import Decimal

import finance_store

CHECKING = 'c' * 64
CARD_S = '5' * 64   # Steve's card
CARD_H = 'e' * 64   # Heather's card


def ledger_db(tmp_path):
    """A migrated private DB with three included USD accounts."""
    conn = finance_store.connect(str(tmp_path / 'private' / 'finance.db'))
    finance_store.record_sync(conn, {'accounts': [
        {'id': i, 'label': l, 'currency': 'USD', 'balance': Decimal('0'), 'balance_at': '2026-11-01T00:00:00Z'}
        for i, l in [(CHECKING, 'Checking'), (CARD_S, 'Steve card'), (CARD_H, 'Heather card')]],
        'warnings': [], 'complete': True})
    import finance_budget
    finance_budget.set_account(conn, CHECKING, included=True, role='checking')
    finance_budget.set_account(conn, CARD_S, included=True, role='card')
    finance_budget.set_account(conn, CARD_H, included=True, role='card')
    return conn


def cat(conn, name):
    return conn.execute('SELECT id FROM finance_budget_categories WHERE name=?', (name,)).fetchone()['id']


def test_initialize_creates_tables_and_seeds_once(tmp_path):
    import finance_budget
    conn = ledger_db(tmp_path)
    assert finance_budget.is_initialized(conn)
    names = {r['name'] for r in conn.execute('SELECT name FROM finance_budget_categories')}
    assert {'Shared dining and entertainment', 'Household wants', 'Heather personal', 'Mortgage', 'Savings'} <= names
    count = conn.execute('SELECT COUNT(*) FROM finance_budget_categories').fetchone()[0]
    finance_budget.initialize(conn)
    assert conn.execute('SELECT COUNT(*) FROM finance_budget_categories').fetchone()[0] == count


def test_seed_targets_are_never_historical(tmp_path):
    conn = ledger_db(tmp_path)
    bases = {r['basis'] for r in conn.execute('SELECT basis FROM finance_budget_targets')}
    assert bases == {'planning', 'estimate'}
    row = conn.execute('SELECT amount, basis FROM finance_budget_targets WHERE category_id=?',
                       (cat(conn, 'Shared dining and entertainment'),)).fetchone()
    assert (row['amount'], row['basis']) == ('650.00', 'planning')
    groceries = cat(conn, 'Groceries and household essentials')
    assert conn.execute('SELECT 1 FROM finance_budget_targets WHERE category_id=?', (groceries,)).fetchone() is None


def test_existing_snapshot_db_upgrades_additively(tmp_path):
    import finance_budget
    conn = ledger_db(tmp_path)
    conn.execute("INSERT INTO finance_saved_snapshots(captured_at,actor,complete,payload) VALUES ('2026-01-01T00:00:00Z','a',1,'{}')")
    conn.commit()
    for t in ['finance_txn_allocations', 'finance_txn_links', 'finance_source_records', 'finance_txns', 'finance_budget_targets']:
        conn.execute('DROP TABLE IF EXISTS ' + t)
    conn.commit()
    conn.close()
    conn = finance_store.connect(str(tmp_path / 'private' / 'finance.db'))
    assert finance_budget.is_initialized(conn)
    assert conn.execute('SELECT COUNT(*) FROM finance_saved_snapshots').fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM finance_budget_targets').fetchone()[0] > 0


import pytest
from datetime import date
from finance_store import FinanceStoreError


def test_parse_money_formats():
    import finance_budget as b
    assert b.parse_money('$1,234.5') == Decimal('1234.50')
    assert b.parse_money('(12.34)') == Decimal('-12.34')
    assert b.parse_money('-0.10') == Decimal('-0.10')
    for bad in ['1.234', 'abc', '', '1e5', 'NaN', '99999999999999', None]:
        with pytest.raises(FinanceStoreError):
            b.parse_money(bad)
    with pytest.raises(FinanceStoreError):
        b.parse_money('-1', signed=False)


def test_mask_digits_hides_account_numbers():
    import finance_budget as b
    assert b.mask_digits('ZELLE TO 1234-5678-9012 ref') == 'ZELLE TO •••• ref'
    assert b.mask_digits('COFFEE #12') == 'COFFEE #12'
    assert b.mask_digits('a\x00b') == 'a b'


def test_targets_are_effective_dated_and_past_is_immutable(tmp_path):
    import finance_budget as b
    conn = ledger_db(tmp_path)
    dining = cat(conn, 'Shared dining and entertainment')
    b.set_target(conn, category_id=dining, effective_month='2027-02', amount='700', basis='planning', note='', actor='alice', today=date(2026, 12, 5))
    assert b.target_for(conn, dining, '2027-01')['amount'] == Decimal('650.00')
    assert b.target_for(conn, dining, '2027-03')['amount'] == Decimal('700.00')
    assert b.target_for(conn, dining, '2026-09') is None
    with pytest.raises(FinanceStoreError):
        b.set_target(conn, category_id=dining, effective_month='2026-11', amount='1', basis='planning', note='', actor='alice', today=date(2026, 12, 5))
    with pytest.raises(FinanceStoreError):
        b.set_target(conn, category_id=dining, effective_month='2027-13', amount='1', basis='planning', note='', actor='alice', today=date(2026, 12, 5))
    b.set_target(conn, category_id=dining, effective_month='2027-02', amount='720', basis='historical', note='', actor='alice', today=date(2026, 12, 5))
    assert b.target_for(conn, dining, '2027-02')['basis'] == 'historical'


def test_category_rules(tmp_path):
    import finance_budget as b
    conn = ledger_db(tmp_path)
    child = b.save_category(conn, name='Takeout', parent_id=cat(conn, 'Shared dining and entertainment'), type='capped')
    with pytest.raises(FinanceStoreError):
        b.save_category(conn, name='Too deep', parent_id=child, type='capped')
    with pytest.raises(FinanceStoreError):
        b.save_category(conn, name='Mystery', type='capped', default_person='Nobody')
    with pytest.raises(FinanceStoreError):
        b.save_category(conn, name='Capped no cap', type='capped', rollover='capped')
    with pytest.raises(FinanceStoreError):
        b.save_category(conn, name='Takeout', type='capped')
    b.save_category(conn, category_id=child, name='Takeout & delivery', parent_id=cat(conn, 'Shared dining and entertainment'),
                    type='capped', policy_includes='Family takeout', policy_excludes='Solo lunches')
    names = [c['name'] for c in b.categories(conn)]
    assert names.index('Takeout & delivery') == names.index('Shared dining and entertainment') + 1
    b.set_account(conn, CHECKING, included=False, role='checking')
    assert b.account_settings(conn)[CHECKING]['included'] is False


def test_people_and_coverage(tmp_path):
    import finance_budget as b
    conn = ledger_db(tmp_path)
    assert b.people(conn) == ['Heather', 'Steve']
    with pytest.raises(FinanceStoreError):
        b.set_people(conn, ['shared'])
    with pytest.raises(FinanceStoreError):
        b.set_people(conn, ['A', 'A'])
    b.declare_coverage(conn, account_id=CHECKING, start='2026-11-01', end='2026-11-30', actor='s', today=date(2026, 12, 1))
    with pytest.raises(FinanceStoreError):
        b.declare_coverage(conn, account_id=CHECKING, start='2026-11-02', end='2026-11-01', actor='s', today=date(2026, 12, 1))
    with pytest.raises(FinanceStoreError):
        b.declare_coverage(conn, account_id=CHECKING, start='2026-11-01', end='2026-12-05', actor='s', today=date(2026, 12, 1))


def test_seeds_start_in_october_with_tirzepatide(tmp_path):
    import finance_budget as b
    conn = ledger_db(tmp_path)
    months = {r[0] for r in conn.execute("SELECT effective_month FROM finance_budget_targets WHERE created_by='seed'")}
    assert months == {'2026-10'}
    assert b.get_setting(conn, 'budget_start') == '2026-10-01'
    tirz = b.target_for(conn, cat(conn, 'Tirzepatide'), '2026-10')
    assert (tirz['amount'], tirz['basis']) == (Decimal('199.67'), 'estimate')
    assert 'HSA' in tirz['note']
    health = cat(conn, 'Health and medical')
    parents = {r['name']: r['parent_id'] for r in conn.execute('SELECT name, parent_id FROM finance_budget_categories')}
    assert parents['Tirzepatide'] == health and parents['Routine medical'] == health
    assert b.target_for(conn, cat(conn, 'Routine medical'), '2026-10') is None


def test_november_seed_database_moves_to_october_without_touching_edits(tmp_path):
    import finance_budget as b
    conn = ledger_db(tmp_path)
    # Recreate a database seeded by the first release: November seeds, no medical children.
    conn.execute("UPDATE finance_budget_targets SET effective_month='2026-11' WHERE created_by='seed'")
    conn.execute("DELETE FROM finance_budget_targets WHERE category_id IN (SELECT id FROM finance_budget_categories "
                 "WHERE name IN ('Tirzepatide','Routine medical'))")
    conn.execute("DELETE FROM finance_budget_categories WHERE name IN ('Tirzepatide','Routine medical')")
    conn.execute("UPDATE finance_budget_settings SET value='2026-11-01' WHERE key='budget_start'")
    conn.execute("DELETE FROM finance_budget_settings WHERE key='seed_version'")
    conn.commit()
    dining = cat(conn, 'Shared dining and entertainment')
    b.set_target(conn, category_id=dining, effective_month='2026-11', amount='700', basis='planning', note='', actor='alice',
                 today=date(2026, 9, 24))
    b.initialize(conn)
    b.initialize(conn)
    assert b.target_for(conn, cat(conn, 'Mortgage'), '2026-10')['amount'] == Decimal('2910.21')
    assert b.target_for(conn, dining, '2026-10') is None
    assert b.target_for(conn, dining, '2026-11')['amount'] == Decimal('700.00')
    assert b.get_setting(conn, 'budget_start') == '2026-10-01'
    assert b.target_for(conn, cat(conn, 'Tirzepatide'), '2026-10')['amount'] == Decimal('199.67')
    assert conn.execute("SELECT COUNT(*) FROM finance_budget_categories WHERE name='Tirzepatide'").fetchone()[0] == 1


def test_user_changed_start_date_is_kept(tmp_path):
    import finance_budget as b
    conn = ledger_db(tmp_path)
    conn.execute("UPDATE finance_budget_settings SET value='2027-01-01' WHERE key='budget_start'")
    conn.execute("DELETE FROM finance_budget_settings WHERE key='seed_version'")
    conn.commit()
    b.initialize(conn)
    assert b.get_setting(conn, 'budget_start') == '2027-01-01'
