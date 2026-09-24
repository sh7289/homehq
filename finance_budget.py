"""Budget schema and household budgeting configuration in the private finance DB.

Budgeting adds financial *activity* (transactions) and *intentions* (categories,
targets) beside the existing balance worksheet. Tables are created additively by
``initialize``, which ``finance_book.initialize`` calls, so only the operator's
migration or sync ever changes the schema. Seeded targets are planning values or
estimates carried from the household brief -- never historical actuals.
"""
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from finance_book import _text, _transaction
from finance_store import FinanceStoreError, _iso, _utc

CATEGORY_TYPES = ('operating', 'capped', 'sinking', 'income', 'savings', 'transfer', 'other')
ROLLOVERS = ('reset', 'capped', 'carry')
BASES = ('planning', 'estimate', 'historical')
ROLES = ('checking', 'savings', 'reserve', 'card', 'hsa', 'loan', 'other')
CENT = Decimal('0.01')
SEED_VERSION = '2'
SEED_START_MONTH = '2026-10'
KINDS = ('unclassified', 'expense', 'income', 'refund', 'reimbursement', 'transfer', 'card_payment')

TABLES = frozenset({
    'finance_budget_settings', 'finance_budget_accounts', 'finance_budget_categories',
    'finance_budget_targets', 'finance_import_batches', 'finance_staged_rows', 'finance_txns',
    'finance_source_records', 'finance_txn_allocations', 'finance_txn_links', 'finance_coverage',
    'finance_fund_movements',
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
CREATE TABLE IF NOT EXISTS finance_fund_movements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER NOT NULL REFERENCES finance_budget_categories(id),
  kind TEXT NOT NULL CHECK (kind IN ('opening','contribution','release','adjustment')),
  amount TEXT NOT NULL, movement_date TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
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

# (category, amount, basis, note). First seeded effective 2026-11; _seed_v2 moves them to October.
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
        if get_setting(conn, 'seed_version') != SEED_VERSION:
            _seed_v2(conn)
            conn.execute("INSERT INTO finance_budget_settings VALUES ('seed_version', ?) "
                         'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (SEED_VERSION,))


def _seed_v2(conn):
    """Start the seeded plan in October 2026 and add tirzepatide under medical.

    Only rows still exactly as seeded move; anything a person saved is left alone.
    Routine medical has no target on purpose: the brief records no amount for it,
    so the plan keeps naming it as missing rather than treating medical as covered.
    """
    conn.execute("UPDATE finance_budget_targets SET effective_month=? WHERE created_by='seed' AND effective_month='2026-11' "
                 'AND NOT EXISTS (SELECT 1 FROM finance_budget_targets t WHERE t.category_id=finance_budget_targets.category_id '
                 'AND t.effective_month=?)', (SEED_START_MONTH, SEED_START_MONTH))
    conn.execute("UPDATE finance_budget_settings SET value=? WHERE key='budget_start' AND value='2026-11-01'",
                 (SEED_START_MONTH + '-01',))
    health = conn.execute("SELECT id FROM finance_budget_categories WHERE name='Health and medical' AND parent_id IS NULL").fetchone()
    if health is None:
        return
    position = conn.execute("SELECT position FROM finance_budget_categories WHERE id=?", (health['id'],)).fetchone()['position']
    for name in ('Tirzepatide', 'Routine medical'):
        conn.execute('INSERT OR IGNORE INTO finance_budget_categories (name,parent_id,type,position) VALUES (?,?,?,?)',
                     (name, health['id'], 'operating', position))
    tirzepatide = conn.execute("SELECT id FROM finance_budget_categories WHERE name='Tirzepatide' AND parent_id=?",
                               (health['id'],)).fetchone()
    if tirzepatide:
        conn.execute("INSERT OR IGNORE INTO finance_budget_targets (category_id,effective_month,amount,basis,note,created_by,created_at) "
                     "VALUES (?,?,'199.67','estimate',?,'seed',?)",
                     (tirzepatide['id'], SEED_START_MONTH, '$599 quarterly; HSA funding and eligibility not confirmed', _iso(_utc())))


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


MAX_MONEY = Decimal('1e12')
_MONEY = re.compile(r'[+-]?(?:\d+(?:\.\d{0,2})?|\.\d{1,2})')


def parse_money(text, *, signed=True):
    """Parse a bank or form amount to cents; accepts $, commas and (negative)."""
    message = 'Enter an amount with at most two decimal places.'
    if not isinstance(text, str) or len(text) > 40:
        raise FinanceStoreError(message)
    value = text.strip().replace('$', '').replace(',', '').replace(' ', '')
    negative = value.startswith('(') and value.endswith(')')
    if negative:
        value = value[1:-1]
    if not _MONEY.fullmatch(value):
        raise FinanceStoreError(message)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise FinanceStoreError(message) from None
    if negative:
        number = -number
    if number.copy_abs() >= MAX_MONEY or (not signed and number < 0):
        raise FinanceStoreError(message)
    return number.quantize(CENT)


def money_text(value):
    return format(Decimal(value).quantize(CENT), 'f')


def mask_digits(text):
    """Printable, single-spaced text with account-number-like digit runs hidden."""
    if not isinstance(text, str):
        return ''
    text = ' '.join(''.join(c if ord(c) >= 32 and ord(c) != 127 else ' ' for c in text).split())
    return re.sub(r'\d(?:[ -]?\d){3,}', '••••', text)[:200]


def _long_text(value, limit=500):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in '\n\r\t' or ord(c) == 127 for c in value):
        raise FinanceStoreError('Enter shorter plain text.')
    return value.strip()


def _day(value, message='Enter a valid date.'):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise FinanceStoreError(message)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise FinanceStoreError(message) from None


def _month(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value):
        raise FinanceStoreError('Enter a month as YYYY-MM.')
    return value


def get_setting(conn, key):
    row = conn.execute('SELECT value FROM finance_budget_settings WHERE key=?', (key,)).fetchone()
    return row['value'] if row else None


def primary_currency(conn):
    return get_setting(conn, 'primary_currency') or 'USD'


def people(conn):
    try:
        names = json.loads(get_setting(conn, 'people') or '[]')
    except ValueError:
        return []
    return [n for n in names if isinstance(n, str)]


def set_people(conn, names):
    if not isinstance(names, list) or not 1 <= len(names) <= 6:
        raise FinanceStoreError('Enter between one and six household members.')
    cleaned = [_text(n, True) for n in names]
    if len({n.lower() for n in cleaned}) != len(cleaned) or any(n.lower() == 'shared' for n in cleaned):
        raise FinanceStoreError('Household member names must be unique and not "shared".')
    with _transaction(conn):
        conn.execute("INSERT INTO finance_budget_settings VALUES ('people', ?) "
                     'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (json.dumps(cleaned),))


def account_settings(conn):
    result = {}
    for row in conn.execute('SELECT * FROM finance_budget_accounts'):
        try:
            profile = json.loads(row['csv_profile'])
        except ValueError:
            profile = {}
        result[row['account_id']] = dict(included=bool(row['included']), role=row['role'],
                                         csv_profile=profile if isinstance(profile, dict) else {})
    return result


def save_csv_profile(conn, account_id, profile):
    if not isinstance(profile, dict):
        raise FinanceStoreError('Import settings are invalid.')
    with _transaction(conn):
        conn.execute('UPDATE finance_budget_accounts SET csv_profile=? WHERE account_id=?',
                     (json.dumps(profile, sort_keys=True), account_id))


def _category(conn, category_id):
    if isinstance(category_id, bool) or not re.fullmatch(r'\d{1,9}', str(category_id)):
        raise FinanceStoreError('Select an existing category.')
    row = conn.execute('SELECT * FROM finance_budget_categories WHERE id=?', (int(category_id),)).fetchone()
    if row is None:
        raise FinanceStoreError('Select an existing category.')
    return row


def save_category(conn, *, category_id=None, name, parent_id=None, type, default_person='', active=True,
                  rollover='reset', rollover_cap=None, position=0, policy_includes='', policy_excludes='', notes=''):
    name = _text(name, True)
    if type not in CATEGORY_TYPES or rollover not in ROLLOVERS or not isinstance(active, bool):
        raise FinanceStoreError('Category settings are invalid.')
    if isinstance(position, bool) or not re.fullmatch(r'-?\d{1,6}', str(position)):
        raise FinanceStoreError('Category settings are invalid.')
    cap = None
    if rollover == 'capped':
        if rollover_cap in (None, ''):
            raise FinanceStoreError('A capped rollover needs a maximum available amount.')
        cap = money_text(parse_money(str(rollover_cap), signed=False))
    includes, excludes, notes = _long_text(policy_includes), _long_text(policy_excludes), _long_text(notes)
    with _transaction(conn):
        if default_person and default_person not in people(conn):
            raise FinanceStoreError('Select a household member or leave the default person blank.')
        parent = None
        if parent_id not in (None, ''):
            parent_row = _category(conn, parent_id)
            if parent_row['parent_id'] is not None or (category_id is not None and parent_row['id'] == int(category_id)):
                raise FinanceStoreError('Subcategories can only sit under a top-level category.')
            parent = parent_row['id']
        clash = conn.execute('SELECT id FROM finance_budget_categories WHERE lower(name)=lower(?)', (name,)).fetchone()
        if clash and (category_id is None or clash['id'] != int(category_id)):
            raise FinanceStoreError('A category with that name already exists.')
        values = (name, parent, type, default_person, int(active), rollover, cap, int(position), includes, excludes, notes)
        if category_id is None:
            return conn.execute(
                'INSERT INTO finance_budget_categories (name,parent_id,type,default_person,active,rollover,rollover_cap,'
                'position,policy_includes,policy_excludes,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)', values).lastrowid
        existing = _category(conn, category_id)
        if parent is not None and conn.execute('SELECT 1 FROM finance_budget_categories WHERE parent_id=?',
                                               (existing['id'],)).fetchone():
            raise FinanceStoreError('A category with subcategories cannot become a subcategory.')
        conn.execute('UPDATE finance_budget_categories SET name=?,parent_id=?,type=?,default_person=?,active=?,rollover=?,'
                     'rollover_cap=?,position=?,policy_includes=?,policy_excludes=?,notes=? WHERE id=?',
                     values + (existing['id'],))
        return existing['id']


def categories(conn, active_only=False):
    rows = [dict(r) for r in conn.execute('SELECT * FROM finance_budget_categories ORDER BY position, name')]
    for row in rows:
        row['active'] = bool(row['active'])
    rows = [r for r in rows if r['active']] if active_only else rows
    ordered = []
    for parent in (r for r in rows if r['parent_id'] is None):
        ordered.append(parent)
        ordered.extend(r for r in rows if r['parent_id'] == parent['id'])
    return ordered


def set_target(conn, *, category_id, effective_month, amount, basis, note, actor, today=None):
    effective_month = _month(effective_month)
    today = today or _utc().date()
    if effective_month < today.strftime('%Y-%m'):
        raise FinanceStoreError('Targets for past months are kept as they were. Set a target from this month onward.')
    if basis not in BASES:
        raise FinanceStoreError('Select whether the target is a planning value, an estimate, or historical.')
    value = money_text(parse_money(str(amount), signed=False))
    note, actor = _long_text(note, 200), _text(actor, True)
    with _transaction(conn):
        category = _category(conn, category_id)
        conn.execute('INSERT INTO finance_budget_targets (category_id,effective_month,amount,basis,note,created_by,created_at) '
                     'VALUES (?,?,?,?,?,?,?) ON CONFLICT(category_id,effective_month) DO UPDATE SET amount=excluded.amount,'
                     'basis=excluded.basis,note=excluded.note,created_by=excluded.created_by,created_at=excluded.created_at',
                     (category['id'], effective_month, value, basis, note, actor, _iso(_utc())))


def target_for(conn, category_id, month):
    row = conn.execute('SELECT * FROM finance_budget_targets WHERE category_id=? AND effective_month<=? '
                       'ORDER BY effective_month DESC LIMIT 1', (category_id, month)).fetchone()
    if row is None:
        return None
    return dict(amount=Decimal(row['amount']), basis=row['basis'], note=row['note'], effective_month=row['effective_month'])


def declare_coverage(conn, *, account_id, start, end, actor, today=None, source='declared', batch_id=None):
    start_day, end_day = _day(start), _day(end)
    today = today or _utc().date()
    if start_day > end_day or end_day > today:
        raise FinanceStoreError('Coverage must start before it ends and cannot extend past today.')
    actor = _text(actor, True)
    with _transaction(conn):
        if not conn.execute('SELECT 1 FROM finance_budget_accounts WHERE account_id=?', (account_id,)).fetchone():
            raise FinanceStoreError('Set up this account for budgeting first.')
        conn.execute('INSERT INTO finance_coverage (account_id,start_date,end_date,source,batch_id,created_by,created_at) '
                     'VALUES (?,?,?,?,?,?,?)', (account_id, start_day.isoformat(), end_day.isoformat(), source, batch_id,
                                                actor, _iso(_utc())))


MONEY_SETTINGS = ('checking_minimum', 'reserve_holds')


def money_setting(conn, key):
    value = get_setting(conn, key)
    return Decimal(value) if value else None


def set_money_setting(conn, key, value):
    """Store a non-negative amount, or clear the setting when the value is blank."""
    if key not in MONEY_SETTINGS or not isinstance(value, str):
        raise FinanceStoreError('That setting is not available.')
    with _transaction(conn):
        if not value.strip():
            conn.execute('DELETE FROM finance_budget_settings WHERE key=?', (key,))
            return
        amount = money_text(parse_money(value, signed=False))
        conn.execute('INSERT INTO finance_budget_settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                     (key, amount))
