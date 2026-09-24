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
