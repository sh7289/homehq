"""Budget schema and household budgeting configuration in the private finance DB.

Budgeting adds financial *activity* (transactions) and *intentions* (categories,
targets) beside the existing balance worksheet. Tables are created additively by
``initialize``, which ``finance_book.initialize`` calls, so only the operator's
migration or sync ever changes the schema. Seeded targets are planning values or
estimates carried from the household brief -- never historical actuals.
"""
import json
from datetime import date

from finance_book import _transaction
from finance_store import FinanceStoreError, _iso, _utc

CATEGORY_TYPES = ('operating', 'capped', 'sinking', 'income', 'savings', 'transfer', 'other')
ROLLOVERS = ('reset', 'capped', 'carry')
BASES = ('planning', 'estimate', 'historical')
ROLES = ('checking', 'savings', 'reserve', 'card', 'hsa', 'loan', 'other')
KINDS = ('unclassified', 'expense', 'income', 'refund', 'reimbursement', 'transfer', 'card_payment')

TABLES = frozenset({
    'finance_budget_settings', 'finance_budget_accounts', 'finance_budget_categories',
    'finance_budget_targets', 'finance_import_batches', 'finance_staged_rows', 'finance_txns',
    'finance_source_records', 'finance_txn_allocations', 'finance_txn_links', 'finance_coverage',
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS finance_budget_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_budget_accounts (
  account_id TEXT PRIMARY KEY REFERENCES finance_accounts(id) ON DELETE CASCADE,
  included INTEGER NOT NULL CHECK (included IN (0,1)),
  role TEXT NOT NULL CHECK (role IN ('checking','savings','reserve','card','hsa','loan','other')),
  csv_profile TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS finance_budget_categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
  parent_id INTEGER REFERENCES finance_budget_categories(id),
  type TEXT NOT NULL CHECK (type IN ('operating','capped','sinking','income','savings','transfer','other')),
  default_person TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
  rollover TEXT NOT NULL DEFAULT 'reset' CHECK (rollover IN ('reset','capped','carry')),
  rollover_cap TEXT, position INTEGER NOT NULL DEFAULT 0,
  policy_includes TEXT NOT NULL DEFAULT '', policy_excludes TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS finance_budget_targets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER NOT NULL REFERENCES finance_budget_categories(id),
  effective_month TEXT NOT NULL, amount TEXT NOT NULL,
  basis TEXT NOT NULL CHECK (basis IN ('planning','estimate','historical')),
  note TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (category_id, effective_month));
CREATE TABLE IF NOT EXISTS finance_import_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL REFERENCES finance_accounts(id),
  state TEXT NOT NULL CHECK (state IN ('staged','committed','rolled_back')),
  label TEXT NOT NULL, imported_by TEXT NOT NULL, created_at TEXT NOT NULL,
  header_json TEXT NOT NULL, mapping_json TEXT NOT NULL DEFAULT '{}',
  row_count INTEGER NOT NULL DEFAULT 0, new_count INTEGER NOT NULL DEFAULT 0,
  duplicate_count INTEGER NOT NULL DEFAULT 0, committed_at TEXT, rolled_back_at TEXT);
CREATE TABLE IF NOT EXISTS finance_staged_rows (
  batch_id INTEGER NOT NULL REFERENCES finance_import_batches(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL, cells_json TEXT NOT NULL, PRIMARY KEY (batch_id, idx));
CREATE TABLE IF NOT EXISTS finance_txns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL REFERENCES finance_accounts(id),
  txn_date TEXT NOT NULL, posted_date TEXT, amount TEXT NOT NULL, currency TEXT NOT NULL,
  original_description TEXT NOT NULL, merchant TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','posted','void','replaced')),
  kind TEXT NOT NULL CHECK (kind IN ('unclassified','expense','income','refund','reimbursement','transfer','card_payment')),
  review TEXT NOT NULL CHECK (review IN ('unreviewed','suggested','accepted')),
  suggested_kind TEXT, suggested_category_id INTEGER REFERENCES finance_budget_categories(id),
  suggested_person TEXT, suggestion_source TEXT,
  possible_duplicate_of INTEGER REFERENCES finance_txns(id) ON DELETE SET NULL,
  note TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created_at TEXT NOT NULL,
  updated_by TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS finance_txns_date ON finance_txns(txn_date);
CREATE TABLE IF NOT EXISTS finance_source_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER REFERENCES finance_import_batches(id),
  account_id TEXT NOT NULL, source TEXT NOT NULL CHECK (source IN ('csv','manual')),
  source_txn_hash TEXT, fingerprint TEXT NOT NULL, occurrence INTEGER NOT NULL,
  columns_json TEXT NOT NULL,
  txn_id INTEGER NOT NULL REFERENCES finance_txns(id) ON DELETE CASCADE);
CREATE UNIQUE INDEX IF NOT EXISTS finance_source_fp
  ON finance_source_records(account_id, source, fingerprint, occurrence);
CREATE UNIQUE INDEX IF NOT EXISTS finance_source_id
  ON finance_source_records(account_id, source, source_txn_hash) WHERE source_txn_hash IS NOT NULL;
CREATE TABLE IF NOT EXISTS finance_txn_allocations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  txn_id INTEGER NOT NULL REFERENCES finance_txns(id) ON DELETE CASCADE,
  category_id INTEGER NOT NULL REFERENCES finance_budget_categories(id),
  person TEXT NOT NULL, amount TEXT NOT NULL, note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS finance_txn_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('transfer','card_payment','refund','pending_posted')),
  from_txn_id INTEGER NOT NULL REFERENCES finance_txns(id) ON DELETE CASCADE,
  to_txn_id INTEGER NOT NULL REFERENCES finance_txns(id) ON DELETE CASCADE,
  payment_id INTEGER REFERENCES finance_payments(id) ON DELETE SET NULL,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_coverage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL REFERENCES finance_accounts(id),
  start_date TEXT NOT NULL, end_date TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('import','declared')),
  batch_id INTEGER REFERENCES finance_import_batches(id) ON DELETE CASCADE,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL);
"""

# (name, parent, type, default_person, rollover, rollover_cap)
SEED_CATEGORIES = [
    ('Housing and fixed obligations', None, 'operating', '', 'reset', None),
    ('Mortgage', 'Housing and fixed obligations', 'operating', '', 'reset', None),
    ('Student loan', 'Housing and fixed obligations', 'operating', '', 'reset', None),
    ('Fence financing', 'Housing and fixed obligations', 'operating', '', 'reset', None),
    ('Home and auto insurance', 'Housing and fixed obligations', 'operating', '', 'reset', None),
    ('Mattress financing', 'Housing and fixed obligations', 'operating', '', 'reset', None),
    ('RAV4 financing', 'Housing and fixed obligations', 'operating', '', 'reset', None),
    ('House cleaning', 'Housing and fixed obligations', 'operating', '', 'reset', None),
    ('Yard service', 'Housing and fixed obligations', 'operating', '', 'reset', None),
    ('Childcare', None, 'operating', '', 'reset', None),
    ('Liz', 'Childcare', 'operating', '', 'reset', None),
    ('Haley', 'Childcare', 'operating', '', 'reset', None),
    ('Utilities and communications', None, 'operating', '', 'reset', None),
    ('Georgia Power', 'Utilities and communications', 'operating', '', 'reset', None),
    ('Water', 'Utilities and communications', 'operating', '', 'reset', None),
    ('Natural gas', 'Utilities and communications', 'operating', '', 'reset', None),
    ('AT&T', 'Utilities and communications', 'operating', '', 'reset', None),
    ('T-Mobile', 'Utilities and communications', 'operating', '', 'reset', None),
    ('Groceries and household essentials', None, 'operating', '', 'reset', None),
    ('Routine pets', None, 'operating', '', 'reset', None),
    ('Health and medical', None, 'operating', '', 'reset', None),
    ('Transportation', None, 'operating', '', 'reset', None),
    ('Shared dining and entertainment', None, 'capped', '', 'reset', None),
    ('Household wants', None, 'capped', '', 'capped', '900.00'),
    ('Heather personal', None, 'capped', 'Heather', 'reset', None),
    ('Steve personal', None, 'capped', 'Steve', 'reset', None),
    ('Work/professional', None, 'capped', '', 'reset', None),
    ('Gifts and Christmas', None, 'sinking', '', 'carry', None),
    ('Celebrations', None, 'sinking', '', 'carry', None),
    ('Travel', None, 'sinking', '', 'carry', None),
    ('Home/car/pet reserve', None, 'sinking', '', 'carry', None),
    ('Income', None, 'income', '', 'reset', None),
    ('Savings', None, 'savings', '', 'reset', None),
    ('Transfers', None, 'transfer', '', 'reset', None),
]

# (category, amount, basis, note) effective 2026-11.
SEED_TARGETS = [
    ('Shared dining and entertainment', '650.00', 'planning', 'Proposed allowance'),
    ('Household wants', '300.00', 'planning', 'Proposed allowance'),
    ('Heather personal', '350.00', 'planning', 'Proposed allowance'),
    ('Steve personal', '350.00', 'planning', 'Proposed allowance'),
    ('Work/professional', '75.00', 'planning', 'Proposed allowance'),
    ('Income', '11952.00', 'estimate', 'Previously recorded; reverify'),
    ('Mortgage', '2910.21', 'estimate', 'Previously recorded'),
    ('Liz', '1000.00', 'estimate', 'Previously recorded'),
    ('Haley', '250.00', 'estimate', 'Previously recorded'),
    ('House cleaning', '300.00', 'estimate', 'Estimate'),
    ('Yard service', '75.00', 'estimate', 'Estimate'),
    ('Student loan', '490.35', 'estimate', 'Reverify'),
    ('Fence financing', '560.00', 'estimate', 'Expected to end spring 2027'),
    ('Home and auto insurance', '459.00', 'estimate', 'Monthly equivalent; confirm cadence'),
    ('T-Mobile', '177.00', 'estimate', 'Estimate'),
    ('Georgia Power', '215.00', 'estimate', 'Range $200–230'),
    ('AT&T', '90.00', 'estimate', 'Estimate'),
    ('Water', '130.00', 'estimate', 'Estimate'),
    ('Natural gas', '35.00', 'estimate', 'Estimate'),
    ('Mattress financing', '250.00', 'estimate', 'Placeholder'),
    ('RAV4 financing', '784.93', 'estimate', 'Expected to end after October 2026; confirm payoff'),
]


def is_initialized(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return TABLES <= tables


def initialize(conn):
    with _transaction(conn):
        for statement in SCHEMA.split(';'):
            if statement.strip():
                conn.execute(statement)
        for key, value in (('primary_currency', 'USD'), ('budget_start', '2026-11-01'),
                           ('people', json.dumps(['Heather', 'Steve']))):
            conn.execute('INSERT OR IGNORE INTO finance_budget_settings VALUES (?,?)', (key, value))
        _seed(conn)


def _seed(conn):
    if not conn.execute('SELECT 1 FROM finance_budget_categories LIMIT 1').fetchone():
        ids = {}
        for position, (name, parent, kind, person, rollover, cap) in enumerate(SEED_CATEGORIES):
            ids[name] = conn.execute(
                'INSERT INTO finance_budget_categories (name,parent_id,type,default_person,rollover,rollover_cap,position) '
                'VALUES (?,?,?,?,?,?,?)', (name, ids.get(parent), kind, person, rollover, cap, position)).lastrowid
    if not conn.execute('SELECT 1 FROM finance_budget_targets LIMIT 1').fetchone():
        now = _iso(_utc())
        for name, amount, basis, note in SEED_TARGETS:
            row = conn.execute('SELECT id FROM finance_budget_categories WHERE name=?', (name,)).fetchone()
            if row:
                conn.execute('INSERT INTO finance_budget_targets (category_id,effective_month,amount,basis,note,created_by,created_at) '
                             "VALUES (?,'2026-11',?,?,?,'seed',?)", (row['id'], amount, basis, note, now))


def _account_row(conn, account_id):
    if not isinstance(account_id, str) or len(account_id) > 200:
        raise FinanceStoreError('Finance account was not found.')
    row = conn.execute('SELECT id, currency FROM finance_accounts WHERE id=?', (account_id,)).fetchone()
    if row is None:
        raise FinanceStoreError('Finance account was not found.')
    return row


def set_account(conn, account_id, *, included, role):
    if type(included) is not bool or role not in ROLES:
        raise FinanceStoreError('Budget account settings are invalid.')
    with _transaction(conn):
        _account_row(conn, account_id)
        conn.execute('INSERT INTO finance_budget_accounts (account_id, included, role) VALUES (?,?,?) '
                     'ON CONFLICT(account_id) DO UPDATE SET included=excluded.included, role=excluded.role',
                     (account_id, int(included), role))
