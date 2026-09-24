# Budgeting Phase 1: Ledger Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Home HQ Finance a trustworthy transaction ledger: CSV import and manual
entry, dedupe, classification with splits, transfer, card-payment, refund and
pending links, categories and effective-dated targets, and a month view that is
honest about coverage, uncategorized spending and plan completeness.

**Architecture:** New tables in the existing private finance SQLite DB, created by
`finance_budget.initialize` (called from `finance_book.initialize`, so the existing
migration CLI covers it). There are four focused modules: `finance_budget` (schema,
money, settings, categories, targets, coverage), `finance_ledger` (transactions,
allocations, links, suggestions), `finance_import` (CSV staging → mapping → commit
→ rollback) and `finance_ledger_math` (read-only budget-accounting aggregation). Routes
live in `finance_budget_routes.register(app)`, called from `finance_routes.init_app`
so every route reuses the existing `_enabled`/`require_recent_mfa`/`_csrf`/`_open` gates.

**Tech Stack:** Python 3.9+, Flask 3, Jinja, sqlite3, stdlib `csv`/`decimal`/`hashlib`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-budgeting-design.md` (implements its Phase 1),
product source `home_hq_budgeting_product_spec.md`, and the adopted adversarial review.

## Global Constraints

- Money is `Decimal`, stored as `TEXT`, quantized to `0.01`. Never use float. Reject inputs with more than 2 decimal places or `abs > 1e12`.
- Sign: `amount < 0` means money leaving through that account; `amount > 0` means money coming in.
- Budget totals use the primary currency only (`finance_budget_settings.primary_currency`, default `USD`). Other currencies are listed separately and never converted.
- A classified transaction's allocations sum **exactly** to its amount, and every allocation has the same sign as the amount. Transfers and card payments have no allocations.
- Web GETs never create or migrate the DB. Budget pages show "Budgeting update needed" when `finance_budget.is_initialized(conn)` is false.
- Every new route: `_enabled` → `require_recent_mfa` → `_csrf` (on POST), POST-redirect-GET, and generic error text that never echoes file contents.
- Never store raw CSV files. Stored descriptions and columns pass `mask_digits` (the same rule as `simplefin._display_name`: runs of 4+ digits, optionally separated by space or `-`, become `••••`). Provider transaction ids are stored only as a SHA-256 hex digest.
- Seeded target basis values are `planning` or `estimate`, never `historical`.
- Uncategorized outflows always count in known spending.
- Budget accounting (this phase) is by transaction date and never includes transfers or card payments.

## Review Focus

1. **Bank CSV quirks.** Parenthesized negatives `(12.34)`, `$` and thousands separators, trailing blank lines, BOM, and `MM/DD/YYYY` dates should import, not error. Pinned in Task 4 (`test_parse_amount_formats`, `test_bom_and_blank_rows`).
2. **Re-importing an overlapping export** from a later date must add only the new rows and keep user edits. Pinned in Task 4 (`test_overlapping_export_adds_only_new_rows`).
3. **Rolling back a batch whose transaction was used as the posted side of a pending link.** The pending item must come back as pending, not vanish or stay "replaced". Pinned in Task 4 (`test_rollback_restores_replaced_pending`).
4. **An active operating category with no target** must make the plan *incomplete* and name that category, never treat it as $0. Pinned in Task 5 (`test_plan_incomplete_names_missing_categories`).
5. **The current month** must judge coverage only up to today, so a mid-month import isn't called "insufficient" for days that haven't happened yet. Pinned in Task 5 (`test_current_month_coverage_only_to_today`).

---

## File structure

| File | Responsibility |
|---|---|
| `finance_budget.py` (new) | Schema + seed, `is_initialized`, money parsing, `mask_digits`, settings/people, account budget settings, categories, targets, declared coverage |
| `finance_ledger.py` (new) | Create/classify/void transactions, links, merchant key, suggestions, review queue |
| `finance_import.py` (new) | CSV parse, stage, mapping/normalize, preview, commit, discard, rollback |
| `finance_ledger_math.py` (new) | `month_summary` — coverage, data quality, spending, plan affordability (read-only) |
| `finance_budget_routes.py` (new) | Flask routes for budget/transactions/imports/settings |
| `finance_book.py` (modify `initialize`) | Call `finance_budget.initialize(conn)` |
| `finance_routes.py` (modify `init_app`) | Call `finance_budget_routes.register(app)` at the end |
| `templates/finance_subnav.html` (new) | Overview · Transactions · Budget · Imports · Settings |
| `templates/finance_budget.html`, `finance_transactions.html`, `finance_txn.html`, `finance_txn_new.html`, `finance_imports.html`, `finance_import.html`, `finance_budget_settings.html` (new) | Pages |
| `templates/finance.html` (modify) | Include sub-nav |
| `static/css/finance.css` (append) | Sub-nav, status pills, allocation rows |
| `docs/runbooks/finance.md` (modify) | Budgeting migration + backup note |
| `tests/test_finance_budget.py`, `test_finance_ledger.py`, `test_finance_import.py`, `test_finance_ledger_math.py`, `test_finance_budget_routes.py` (new) | Tests |

Shared test helper (put at the top of `tests/test_finance_budget.py` and import it from the other test modules):

```python
from decimal import Decimal
import finance_store

CHECKING = 'c' * 64
CARD_S = 's' * 64   # Steve's card
CARD_H = 'h' * 64   # Heather's card

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
```

---

### Task 1: Schema, seed data and migration hook

**Files:**
- Create: `finance_budget.py`
- Modify: `finance_book.py` (end of `initialize`)
- Test: `tests/test_finance_budget.py`

**Interfaces:**
- Produces: `finance_budget.initialize(conn)`, `finance_budget.is_initialized(conn) -> bool`, constants `CATEGORY_TYPES`, `ROLLOVERS`, `BASES`, `ROLES`, `KINDS`.

Schema (all inside `_transaction(conn)` from `finance_book`):

```sql
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
  policy_includes TEXT NOT NULL DEFAULT '', policy_excludes TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '');
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
CREATE UNIQUE INDEX IF NOT EXISTS finance_source_fp ON finance_source_records(account_id, source, fingerprint, occurrence);
CREATE UNIQUE INDEX IF NOT EXISTS finance_source_id ON finance_source_records(account_id, source, source_txn_hash) WHERE source_txn_hash IS NOT NULL;
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
```

Seed (only when `finance_budget_categories` is empty; settings via `INSERT OR IGNORE`):
- settings: `primary_currency=USD`, `budget_start=2026-11-01`, `people=["Heather","Steve"]`.
- Categories as `(name, parent, type, default_person, rollover, rollover_cap, position)`:
  Housing and fixed obligations (operating); children Mortgage, Student loan, Fence financing, Home and auto insurance, Mattress financing, RAV4 financing, House cleaning, Yard service (operating).
  Childcare (operating); children Liz, Haley.
  Utilities and communications (operating); children Georgia Power, Water, Natural gas, AT&T, T-Mobile.
  Groceries and household essentials; Routine pets; Health and medical; Transportation (all operating).
  Shared dining and entertainment (capped, reset); Household wants (capped, capped, cap `900.00`); Heather personal (capped, reset, person Heather); Steve personal (capped, reset, person Steve); Work/professional (capped, reset).
  Gifts and Christmas, Celebrations, Travel, Home/car/pet reserve (sinking, carry).
  Income (income); Savings (savings); Transfers (transfer).
- Targets effective `2026-11`, `created_by='seed'`:
  planning — Shared dining 650, Household wants 300, Heather personal 350, Steve personal 350, Work/professional 75.
  estimate — Income 11952 ("Previously recorded; reverify"), Mortgage 2910.21, Liz 1000, Haley 250, House cleaning 300, Yard service 75, Student loan 490.35 ("Reverify"), Fence financing 560 ("Expected to end spring 2027"), Home and auto insurance 459 ("Monthly equivalent; confirm cadence"), T-Mobile 177, Georgia Power 215 ("Range $200–230"), AT&T 90, Water 130, Natural gas 35, Mattress financing 250 ("Placeholder"), RAV4 financing 784.93 ("Expected to end after October 2026; confirm payoff").

- [ ] **Step 1: Write failing tests**

```python
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
    row = conn.execute('SELECT amount, basis FROM finance_budget_targets WHERE category_id=?', (cat(conn, 'Shared dining and entertainment'),)).fetchone()
    assert (row['amount'], row['basis']) == ('650.00', 'planning')
    groceries = cat(conn, 'Groceries and household essentials')
    assert conn.execute('SELECT 1 FROM finance_budget_targets WHERE category_id=?', (groceries,)).fetchone() is None

def test_existing_snapshot_db_upgrades_additively(tmp_path):
    import sqlite3, finance_budget
    conn = ledger_db(tmp_path)
    conn.execute("INSERT INTO finance_saved_snapshots(captured_at,actor,complete,payload) VALUES ('2026-01-01T00:00:00Z','a',1,'{}')")
    conn.commit()
    for t in ['finance_txns', 'finance_txn_allocations', 'finance_budget_targets']:
        conn.execute('DROP TABLE IF EXISTS ' + t)
    conn.commit(); conn.close()
    conn = finance_store.connect(str(tmp_path / 'private' / 'finance.db'))
    assert finance_budget.is_initialized(conn)
    assert conn.execute('SELECT COUNT(*) FROM finance_saved_snapshots').fetchone()[0] == 1
```

- [ ] **Step 2:** `.venv/bin/python -m pytest tests/test_finance_budget.py -q` → FAIL (`No module named 'finance_budget'`).
- [ ] **Step 3:** Implement `finance_budget.py` with `initialize`, `is_initialized` (checks the set of the 11 table names above) and `_seed`. Add `import finance_budget; finance_budget.initialize(conn)` as the last line inside `finance_book.initialize` (after `finance_payments.initialize(conn)`). Because `finance_budget_targets` may be dropped while the categories remain (the upgrade test), `_seed` inserts categories only when that table is empty, and inserts targets only when the targets table is empty.
- [ ] **Step 4:** Tests pass; also run `tests/test_finance_*.py`, which must stay green.
- [ ] **Step 5:** Commit `Add budgeting schema and seed data to the finance store`.

### Task 2: Money, settings, categories, targets, coverage

**Files:** Modify `finance_budget.py`; Test `tests/test_finance_budget.py`

**Interfaces (produces):**
- `parse_money(text, *, signed=True) -> Decimal` — accepts `-12.3`, `12`, `$1,234.56`, `(12.34)` → `-12.34`; raises `FinanceStoreError('Enter an amount with at most two decimal places.')`.
- `money_text(value: Decimal) -> str` — `format(value.quantize(Decimal('0.01')), 'f')`.
- `mask_digits(text: str) -> str` — control chars → space, collapse whitespace, `re.sub(r"\d(?:[ -]?\d){3,}", "••••", ...)`, max 200 chars.
- `get_setting(conn, key) -> str | None`, `people(conn) -> list[str]`, `set_people(conn, names: list[str])` (1–6 names, each `_text` valid, unique, not `shared`).
- `set_account(conn, account_id, *, included: bool, role: str)`; `account_settings(conn) -> dict[account_id, dict(included, role, csv_profile: dict)]`; `save_csv_profile(conn, account_id, profile: dict)`.
- `save_category(conn, *, category_id=None, name, parent_id=None, type, default_person='', active=True, rollover='reset', rollover_cap=None, position=0, policy_includes='', policy_excludes='', notes='') -> int` — parent must be top-level (depth ≤ 2) and not self; `default_person` must be '' or in `people(conn)`; `rollover='capped'` requires cap.
- `categories(conn, active_only=False) -> list[dict]` ordered parents first by position, children following their parent.
- `set_target(conn, *, category_id, effective_month, amount, basis, note, actor, today: date)` — `effective_month` `YYYY-MM` must be ≥ `today`'s month (past months are immutable). Same category+month replaces the row. `amount ≥ 0`.
- `target_for(conn, category_id, month) -> dict | None` — latest row with `effective_month <= month` (`amount: Decimal, basis, note, effective_month`).
- `declare_coverage(conn, *, account_id, start, end, actor)` — `start ≤ end ≤ today`, and the account must have budget settings.

- [ ] **Step 1: Failing tests**

```python
import pytest
from datetime import date
from decimal import Decimal
from finance_store import FinanceStoreError

def test_parse_money_formats():
    import finance_budget as b
    assert b.parse_money('$1,234.5') == Decimal('1234.50')
    assert b.parse_money('(12.34)') == Decimal('-12.34')
    assert b.parse_money('-0.10') == Decimal('-0.10')
    for bad in ['1.234', 'abc', '', '1e5', 'NaN', '99999999999999']:
        with pytest.raises(FinanceStoreError):
            b.parse_money(bad)
    with pytest.raises(FinanceStoreError):
        b.parse_money('-1', signed=False)

def test_mask_digits_hides_account_numbers():
    import finance_budget as b
    assert b.mask_digits('ZELLE TO 1234-5678-9012 ref') == 'ZELLE TO •••• ref'
    assert b.mask_digits('COFFEE #12') == 'COFFEE #12'

def test_targets_are_effective_dated_and_past_is_immutable(tmp_path):
    import finance_budget as b
    conn = ledger_db(tmp_path)
    dining = cat(conn, 'Shared dining and entertainment')
    b.set_target(conn, category_id=dining, effective_month='2027-02', amount='700', basis='planning', note='', actor='alice', today=date(2026, 12, 5))
    assert b.target_for(conn, dining, '2027-01')['amount'] == Decimal('650.00')
    assert b.target_for(conn, dining, '2027-03')['amount'] == Decimal('700.00')
    assert b.target_for(conn, dining, '2026-10') is None
    with pytest.raises(FinanceStoreError):
        b.set_target(conn, category_id=dining, effective_month='2026-11', amount='1', basis='planning', note='', actor='alice', today=date(2026, 12, 5))

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
    b.set_account(conn, CHECKING, included=False, role='checking')
    assert b.account_settings(conn)[CHECKING]['included'] is False
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Reuse `finance_book._text` for names (80 chars). Policy/notes fields allow up to 500 chars via a local `_long_text`.
- [ ] **Step 4:** Pass. **Step 5:** Commit `Add budget settings, categories, effective-dated targets`.

### Task 3: Ledger — transactions, classification, links, suggestions

**Files:** Create `finance_ledger.py`; Test `tests/test_finance_ledger.py`

**Interfaces (consumes Task 2; produces):**
- `merchant_key(text) -> str` — lowercase, drop digits and `#*`, collapse whitespace.
- `create_txn(conn, *, account_id, txn_date, amount, description, actor, status='posted', posted_date=None, source='manual', batch_id=None, fingerprint=None, occurrence=1, source_txn_hash=None, columns=None) -> int` — validates date `YYYY-MM-DD`, amount via `parse_money` (non-zero), account has budget settings; currency from `finance_accounts`; stores `mask_digits(description)` as `original_description` and `merchant`. For manual entries the fingerprint is computed like imports (`import_fingerprint` below) with occurrence = 1 + the count of existing manual records with that fingerprint. Applies `suggest(conn, txn_id)`. Sets `possible_duplicate_of` when another source already has a txn on the same account with an equal amount and a date within ±3 days.
- `import_fingerprint(account_id, txn_date, amount: Decimal, description) -> str` — sha256 hex of `f"{account_id}|{txn_date}|{money_text(amount)}|{merchant_key(mask_digits(description))}"`.
- `classify(conn, txn_id, *, kind, allocations: list[dict(category_id, person, amount, note='')], actor, merchant=None, note=None)` — rules from Global Constraints. `expense` requires amount < 0; `income`/`refund`/`reimbursement` require amount > 0. Allocations: non-empty, category active and not type `transfer`, person in `['shared'] + people`, the same sign, and the sum equals the amount exactly. Otherwise raise `FinanceStoreError('Split amounts must add up to the transaction total.')`. `transfer`/`card_payment` via `classify` require empty allocations. Replaces allocations and sets `review='accepted'`, `updated_by/at`.
- `accept_suggestion(conn, txn_id, actor)` — requires `review='suggested'`; classifies as the suggested kind with one allocation `(suggested_category_id, suggested_person, amount)`.
- `accept_suggestions(conn, txn_ids, actor) -> int`.
- `suggest(conn, txn_id)` — if `review='unreviewed'`: find the most recent other txn with the same `merchant_key(merchant)`, the same amount sign, `review='accepted'`, exactly one allocation and kind not in (transfer, card_payment). If found, set `suggested_kind/category/person`, `suggestion_source='prior'`, `review='suggested'`.
- `link(conn, *, kind, from_id, to_id, actor, payment_id=None) -> int`:
  - `transfer` / `card_payment`: different accounts, `amount_from == -amount_to`, both not void/replaced. `card_payment` requires one side's role `card` and the other not `card`. Sets both kinds, deletes their allocations, `review='accepted'`.
  - `refund`: `from` = the refund/reimbursement (amount > 0), `to` = the original expense; `from.amount ≤ -to.amount`. If `from` is not yet classified and `to` has exactly one allocation, classify `from` as `refund` into the same category/person.
  - `pending_posted`: `from.status == 'pending'`, `to.status == 'posted'`, same account. Sets `from.status='replaced'`. If `to` is not accepted and `from` is accepted with one allocation, classify `to` the same way at `to`'s amount.
- `unlink(conn, link_id, actor)` — deletes the link. For `pending_posted`, restores `from.status='pending'`. For transfer/card_payment, sets both txns to `kind='unclassified'`, `review='unreviewed'`.
- `void(conn, txn_id, actor)` — `status='void'`, allocations kept for audit.
- `link_candidates(conn, txn_id, kind) -> list[dict]` — windows from the design (transfer/card_payment: opposite equal amount, other account, ±7 days; refund: same `merchant_key`, expense, earlier ≤180 days; pending_posted: same account, `merchant_key` match, ±10 days, status pending when this txn is posted). Limit 10.
- `get_txn(conn, txn_id) -> dict` with `allocations`, `links` (each with the other txn's date/amount/account name), and `source_records`.

- [ ] **Step 1: Failing tests (acceptance tests 4, 5, 6, 9, 11)**

```python
import pytest
from decimal import Decimal
from finance_store import FinanceStoreError
import finance_ledger as L
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
    kinds = {r['kind'] for r in conn.execute('SELECT kind FROM finance_txns')}
    assert kinds == {'card_payment'}
    other = L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-21', amount='500', description='X', actor='s')
    with pytest.raises(FinanceStoreError):
        L.link(conn, kind='card_payment', from_id=L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-21', amount='-500', description='Y', actor='s'), to_id=other, actor='s')

def test_refund_restores_category(tmp_path):  # Test 9
    conn = ledger_db(tmp_path)
    hotel = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-02', amount='-500', description='HOTEL', actor='s')
    L.classify(conn, hotel, kind='expense', allocations=[alloc(conn, 'Travel', 'shared', '-500')], actor='s')
    refund = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-09', amount='500', description='HOTEL', actor='s')
    L.link(conn, kind='refund', from_id=refund, to_id=hotel, actor='s')
    r = L.get_txn(conn, refund)
    assert r['kind'] == 'refund' and r['allocations'][0]['category_id'] == cat(conn, 'Travel')
    assert r['allocations'][0]['amount'] == Decimal('500.00')

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
    L.accept_suggestion(conn, b, actor='s')
    assert L.get_txn(conn, b)['allocations'][0]['amount'] == Decimal('-41.20')

def test_expense_sign_and_person_validation(tmp_path):
    conn = ledger_db(tmp_path)
    t = L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-01', amount='25', description='?', actor='h')
    with pytest.raises(FinanceStoreError):
        L.classify(conn, t, kind='expense', allocations=[alloc(conn, 'Household wants', 'shared', '25')], actor='h')
    with pytest.raises(FinanceStoreError):
        L.classify(conn, t, kind='refund', allocations=[alloc(conn, 'Household wants', 'Bob', '25')], actor='h')
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement with every mutation inside `finance_book._transaction(conn)`. Timestamps come from `finance_store._iso(_utc())`. **Step 4:** Pass. **Step 5:** Commit `Add canonical transaction ledger with splits and links`.

### Task 4: CSV import — stage, map, preview, commit, rollback

**Files:** Create `finance_import.py`; Test `tests/test_finance_import.py`

**Interfaces (produces):**
- Limits: `MAX_BYTES = 2 * 1024 * 1024`, `MAX_ROWS = 5000`, `STAGE_TTL = timedelta(days=1)`.
- `stage(conn, *, account_id, data: bytes, label, actor, now=None) -> int` — decodes `utf-8-sig`, falling back to `cp1252`. Parses with `csv.reader`, drops fully blank rows, and uses the first row as the header (≤ 30 columns, each cell ≤ 500 chars). Requires ≥ 1 data row. Purges staged batches older than `STAGE_TTL`. Stores the header and rows (`finance_staged_rows`). Error messages: `'The file could not be read as a CSV.'`, `'The file is too large (limit 2 MB / 5,000 rows).'`.
- `default_mapping(conn, batch_id) -> dict` — the account's saved `csv_profile`, when its columns still exist in this header. Otherwise a guess by header names (`date`/`transaction date`, `description`/`payee`/`merchant`, `amount`, `debit`, `credit`, `status`, `id`/`transaction id`, `post date`/`posted date`), with `sign='outflow_negative'`.
- Mapping dict keys: `date`, `description`, `amount` | (`debit`, `credit`), optional `posted_date`, `status`, `id` (values are header indexes as ints), and `sign ∈ {'outflow_negative','outflow_positive','debit_credit'}`.
- `normalize(conn, batch_id, mapping) -> (rows: list[dict], errors: list[str])` — each row is `dict(idx, txn_date, posted_date, amount: Decimal, description, status, source_txn_hash, fingerprint, occurrence)`. Dates are `YYYY-MM-DD`, `MM/DD/YYYY` or `M/D/YY`. `debit_credit`: amount = credit − debit, taking absolute values of each (banks vary). `outflow_positive`: negate. Status maps text containing "pend" (case-insensitive) → pending, else posted. `occurrence` counts repeats of the same fingerprint within this file in order. Errors read like `'Row 7: date not recognized.'` and never echo cell text.
- `preview(conn, batch_id, mapping) -> dict(new, duplicate, possible_duplicate, errors, first_date, last_date, sample: first 20 new rows)`. A row is a duplicate if an `import` source record already exists for (account, csv, source_txn_hash), or else for (account, csv, fingerprint, occurrence).
- `commit(conn, batch_id, mapping, *, period_start, period_end, actor, now=None) -> dict(new, duplicate)` — refuses when `errors` is non-empty, `period_start > period_end`, or the period doesn't contain every row date. Creates txns via `finance_ledger.create_txn(..., source='csv', batch_id=...)`, saves the mapping as the account's `csv_profile`, inserts `finance_coverage(source='import')`, sets `state='committed'` and deletes the staged rows. Everything happens in one transaction.
- `discard(conn, batch_id)` — only for staged batches.
- `rollback(conn, batch_id, *, actor, confirm_edited=False) -> dict(removed, edited)` — `edited` = txns in the batch with `review='accepted'` or `updated_by IS NOT NULL`. If `edited` and not `confirm_edited`, raises `FinanceStoreError('Some imported transactions were already reviewed. Confirm to remove them too.')`. Restores `replaced` pending txns linked to removed ones. Deletes the batch's txns (cascade: source records, allocations, links) and its coverage. Sets `state='rolled_back'`.
- `batches(conn) -> list[dict]` newest first, with the account display name.

- [ ] **Step 1: Failing tests (acceptance test 12 + Review Focus 1–3)**

```python
import pytest
from decimal import Decimal
from finance_store import FinanceStoreError
import finance_import as I, finance_ledger as L
from test_finance_budget import ledger_db, cat, CHECKING, CARD_S

CSV = b"Date,Description,Amount\n11/01/2026,COFFEE,-4.50\n11/01/2026,COFFEE,-4.50\n11/02/2026,PAYROLL ACME 123456789,3000.00\n"
MAP = {'date': 0, 'description': 1, 'amount': 2, 'sign': 'outflow_negative'}

def run(conn, data, account=CHECKING, start='2026-11-01', end='2026-11-30'):
    b = I.stage(conn, account_id=account, data=data, label='nov', actor='s')
    return I.commit(conn, b, MAP, period_start=start, period_end=end, actor='s')

def test_reimport_is_idempotent_and_keeps_identical_rows(tmp_path):  # Test 12
    conn = ledger_db(tmp_path)
    assert run(conn, CSV) == {'new': 3, 'duplicate': 0}
    t = conn.execute("SELECT id FROM finance_txns WHERE amount='-4.50' ORDER BY id").fetchone()['id']
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, 'Shared dining and entertainment'), person='shared', amount='-4.50')], actor='s')
    assert run(conn, CSV) == {'new': 0, 'duplicate': 3}
    assert conn.execute('SELECT COUNT(*) FROM finance_txns').fetchone()[0] == 3
    assert L.get_txn(conn, t)['review'] == 'accepted'

def test_overlapping_export_adds_only_new_rows(tmp_path):
    conn = ledger_db(tmp_path)
    run(conn, CSV)
    later = CSV + b"11/03/2026,COFFEE,-4.50\n"
    assert run(conn, later) == {'new': 1, 'duplicate': 3}

def test_parse_amount_formats(tmp_path):
    conn = ledger_db(tmp_path)
    data = b'Date,Payee,Debit,Credit\n2026-11-04,SHOP,"$1,020.00",\n2026-11-05,REFUND,,(3.25)\n'
    b = I.stage(conn, account_id=CARD_S, data=data, label='x', actor='s')
    rows, errors = I.normalize(conn, b, {'date': 0, 'description': 1, 'debit': 2, 'credit': 3, 'sign': 'debit_credit'})
    assert errors == [] and [r['amount'] for r in rows] == [Decimal('-1020.00'), Decimal('3.25')]

def test_bom_and_blank_rows(tmp_path):
    conn = ledger_db(tmp_path)
    b = I.stage(conn, account_id=CHECKING, data=b'\xef\xbb\xbfDate,Description,Amount\n2026-11-01,A,-1\n\n,,\n', label='x', actor='s')
    rows, errors = I.normalize(conn, b, MAP)
    assert len(rows) == 1 and errors == []

def test_errors_do_not_echo_content_and_block_commit(tmp_path):
    conn = ledger_db(tmp_path)
    b = I.stage(conn, account_id=CHECKING, data=b'Date,Description,Amount\nnot-a-date,SECRET 999,-1\n', label='x', actor='s')
    rows, errors = I.normalize(conn, b, MAP)
    assert errors == ['Row 2: date not recognized.']
    with pytest.raises(FinanceStoreError):
        I.commit(conn, b, MAP, period_start='2026-11-01', period_end='2026-11-30', actor='s')

def test_descriptions_are_masked_and_raw_file_not_kept(tmp_path):
    conn = ledger_db(tmp_path)
    run(conn, CSV)
    descs = [r[0] for r in conn.execute('SELECT original_description FROM finance_txns')]
    assert 'PAYROLL ACME ••••' in descs
    assert conn.execute('SELECT COUNT(*) FROM finance_staged_rows').fetchone()[0] == 0

def test_size_limits(tmp_path):
    conn = ledger_db(tmp_path)
    with pytest.raises(FinanceStoreError):
        I.stage(conn, account_id=CHECKING, data=b'a,b\n' + b'1,2\n' * 5001, label='x', actor='s')

def test_rollback_requires_confirmation_for_reviewed(tmp_path):
    conn = ledger_db(tmp_path)
    run(conn, CSV)
    batch = conn.execute("SELECT id FROM finance_import_batches WHERE state='committed'").fetchone()['id']
    t = conn.execute("SELECT id FROM finance_txns WHERE amount='3000.00'").fetchone()['id']
    L.classify(conn, t, kind='income', allocations=[dict(category_id=cat(conn, 'Income'), person='shared', amount='3000')], actor='s')
    with pytest.raises(FinanceStoreError):
        I.rollback(conn, batch, actor='s')
    assert I.rollback(conn, batch, actor='s', confirm_edited=True) == {'removed': 3, 'edited': 1}
    assert conn.execute('SELECT COUNT(*) FROM finance_coverage').fetchone()[0] == 0

def test_rollback_restores_replaced_pending(tmp_path):
    conn = ledger_db(tmp_path)
    p = L.create_txn(conn, account_id=CHECKING, txn_date='2026-11-01', amount='-4.50', description='COFFEE', actor='s', status='pending')
    run(conn, CSV)
    q = conn.execute("SELECT id FROM finance_txns WHERE amount='-4.50' AND status='posted' ORDER BY id").fetchone()['id']
    L.link(conn, kind='pending_posted', from_id=p, to_id=q, actor='s')
    batch = conn.execute("SELECT id FROM finance_import_batches WHERE state='committed'").fetchone()['id']
    I.rollback(conn, batch, actor='s', confirm_edited=True)
    assert L.get_txn(conn, p)['status'] == 'pending'

def test_manual_entry_flags_possible_duplicate(tmp_path):
    conn = ledger_db(tmp_path)
    m = L.create_txn(conn, account_id=CHECKING, txn_date='2026-11-02', amount='3000', description='paycheck', actor='s')
    run(conn, CSV)
    dup = conn.execute("SELECT possible_duplicate_of FROM finance_txns WHERE amount='3000.00' AND id != ?", (m,)).fetchone()[0]
    assert dup == m
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Pass. **Step 5:** Commit `Add idempotent CSV import with staging and rollback`.

### Task 5: Month summary — coverage, data quality, spending, plan

**Files:** Create `finance_ledger_math.py`; Test `tests/test_finance_ledger_math.py`

**Interfaces (produces):**
`month_summary(conn, month: str, today: date) -> dict` with:
```python
{
 'month': 'YYYY-MM', 'currency': 'USD',
 'coverage': [{'account_id', 'name', 'role', 'missing': [(start_iso, end_iso), ...]}],  # included accounts only
 'quality': 'complete' | 'provisional' | 'insufficient', 'quality_reasons': [str],
 'known_spending': {'posted': D, 'pending': D, 'total': D},   # outflow spending, positive numbers, includes uncategorized
 'uncategorized': {'count': int, 'amount': D},                 # outflows with no allocations, kind not transfer/card_payment
 'unreviewed_inflows': {'count': int, 'amount': D},
 'income': D,                                                  # accepted income allocations
 'categories': [{'id','name','parent_id','type','target': D|None,'basis','target_note','posted': D,'pending': D,'spent': D,'pct_used': int|None}],
 'plan': {'income': D, 'outflows': D, 'surplus': D, 'label': 'validated'|'provisional'|'incomplete',
          'unvalidated': int, 'missing': [str]},
 'other_currencies': {code: D},                               # spending in non-primary currencies
}
```
Rules:
- Rows considered: `txn_date` in the month, `status IN ('posted','pending')`.
- Category spending = −Σ allocation amounts for kinds expense/refund/reimbursement. A parent row's `spent` includes its children's.
- `pct_used = round(spent*100/target)` only when `target > 0`, else `None`.
- Coverage window: month start → `min(month end, today)`. A month entirely in the future has an empty window and `quality='insufficient'` with reason `'Month has not started.'`. `missing` = window days not covered by the union of `finance_coverage` ranges for that account.
- Quality: `insufficient` if any included account has missing days. Else `provisional` if uncategorized count > 0, any txn has `review != 'accepted'`, or any pending exists. Else `complete`.
- Plan: income = Income-type targets. Outflows = targets of every active category with type in operating/capped/sinking/savings (a parent's own target plus its children's). `missing` = active top-level operating categories with no target on themselves or any child, plus Savings if it has no target, plus 'Income' if planned income is 0. The label is `incomplete` if `missing`, else `validated` if every used target basis is `historical`, else `provisional`. `unvalidated` = count of used targets whose basis is not `historical`.

- [ ] **Step 1: Failing tests (acceptance tests 1-spent, 4, 5, 6, 9, 11, 13, review "$400 uncategorized", Review Focus 4–5)**

```python
from datetime import date
from decimal import Decimal
import finance_budget as B, finance_ledger as L
from finance_ledger_math import month_summary
from test_finance_budget import ledger_db, cat, CHECKING, CARD_S, CARD_H

def cover_all(conn, start='2026-11-01', end='2026-11-30'):
    for a in (CHECKING, CARD_S, CARD_H):
        B.declare_coverage(conn, account_id=a, start=start, end=end, actor='s')

def row(summary, name):
    return next(c for c in summary['categories'] if c['name'] == name)

def spend(conn, account, amount, name, person='shared', day='2026-11-10', status='posted'):
    t = L.create_txn(conn, account_id=account, txn_date=day, amount=amount, description=name, actor='s', status=status)
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, name), person=person, amount=amount)], actor='s')
    return t

def test_dining_spent_independent_of_card(tmp_path):  # Test 1 (spent side), Test 4
    conn = ledger_db(tmp_path); cover_all(conn)
    spend(conn, CARD_S, '-140', 'Shared dining and entertainment')
    spend(conn, CARD_H, '-100', 'Shared dining and entertainment')
    s = month_summary(conn, '2026-11', date(2026, 12, 2))
    r = row(s, 'Shared dining and entertainment')
    assert (r['target'], r['spent'], r['pct_used']) == (Decimal('650.00'), Decimal('240.00'), 37)
    assert s['quality'] == 'complete'

def test_card_payment_not_spending_and_refund_nets(tmp_path):  # Tests 6, 9
    conn = ledger_db(tmp_path); cover_all(conn)
    spend(conn, CARD_S, '-500', 'Travel')
    hotel = conn.execute('SELECT id FROM finance_txns').fetchone()['id']
    out = L.create_txn(conn, account_id=CHECKING, txn_date='2026-11-20', amount='-500', description='PAY', actor='s')
    inn = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-20', amount='500', description='PAY', actor='s')
    L.link(conn, kind='card_payment', from_id=out, to_id=inn, actor='s')
    refund = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-25', amount='500', description='Travel', actor='s')
    L.link(conn, kind='refund', from_id=refund, to_id=hotel, actor='s')
    s = month_summary(conn, '2026-11', date(2026, 12, 2))
    assert row(s, 'Travel')['spent'] == Decimal('0.00')
    assert s['known_spending']['total'] == Decimal('0.00')

def test_pending_replacement_counts_once(tmp_path):  # Test 11
    conn = ledger_db(tmp_path); cover_all(conn)
    p = spend(conn, CARD_S, '-50', 'Shared dining and entertainment', status='pending')
    q = L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-11', amount='-60', description='x', actor='s')
    L.link(conn, kind='pending_posted', from_id=p, to_id=q, actor='s')
    assert row(month_summary(conn, '2026-11', date(2026, 12, 2)), 'Shared dining and entertainment')['spent'] == Decimal('60.00')

def test_missing_card_makes_month_insufficient(tmp_path):  # Test 13
    conn = ledger_db(tmp_path)
    for a in (CHECKING, CARD_S):
        B.declare_coverage(conn, account_id=a, start='2026-11-01', end='2026-11-30', actor='s')
    s = month_summary(conn, '2026-11', date(2026, 12, 2))
    assert s['quality'] == 'insufficient'
    gap = next(c for c in s['coverage'] if c['account_id'] == CARD_H)
    assert gap['missing'] == [('2026-11-01', '2026-11-30')]

def test_uncategorized_counts_in_known_spending(tmp_path):  # review test
    conn = ledger_db(tmp_path); cover_all(conn)
    L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-12', amount='-250', description='AMAZON', actor='s')
    L.create_txn(conn, account_id=CARD_S, txn_date='2026-11-13', amount='-150', description='TARGET', actor='s')
    s = month_summary(conn, '2026-11', date(2026, 12, 2))
    assert s['uncategorized'] == {'count': 2, 'amount': Decimal('400.00')}
    assert s['known_spending']['total'] == Decimal('400.00')
    assert s['quality'] == 'provisional'

def test_plan_incomplete_names_missing_categories(tmp_path):
    conn = ledger_db(tmp_path)
    plan = month_summary(conn, '2026-11', date(2026, 11, 15))['plan']
    assert plan['label'] == 'incomplete'
    assert 'Groceries and household essentials' in plan['missing'] and 'Savings' in plan['missing']
    assert 'Housing and fixed obligations' not in plan['missing']

def test_plan_provisional_then_validated(tmp_path):
    conn = ledger_db(tmp_path)
    for c in B.categories(conn):
        if c['parent_id'] is None and c['type'] in ('operating', 'savings'):
            B.set_target(conn, category_id=c['id'], effective_month='2026-11', amount='100', basis='historical', note='', actor='s', today=date(2026, 11, 1))
    plan = month_summary(conn, '2026-11', date(2026, 11, 15))['plan']
    assert plan['label'] == 'provisional' and plan['missing'] == []
    assert plan['surplus'] == plan['income'] - plan['outflows']

def test_current_month_coverage_only_to_today(tmp_path):
    conn = ledger_db(tmp_path); cover_all(conn, end='2026-11-14')
    assert month_summary(conn, '2026-11', date(2026, 11, 14))['quality'] == 'complete'

def test_future_month_is_insufficient_without_division(tmp_path):
    conn = ledger_db(tmp_path)
    s = month_summary(conn, '2027-03', date(2026, 11, 14))
    assert s['quality'] == 'insufficient' and all(c['pct_used'] in (None, 0) for c in s['categories'])
```

Step 1 also covers Test 5 (split into Christmas and personal). Add:

```python
def test_split_hits_three_categories(tmp_path):  # Test 5
    conn = ledger_db(tmp_path); cover_all(conn)
    t = L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-04', amount='-200', description='TARGET', actor='h')
    L.classify(conn, t, kind='expense', allocations=[
        dict(category_id=cat(conn, 'Groceries and household essentials'), person='shared', amount='-80'),
        dict(category_id=cat(conn, 'Gifts and Christmas'), person='shared', amount='-70'),
        dict(category_id=cat(conn, 'Heather personal'), person='Heather', amount='-50')], actor='h')
    s = month_summary(conn, '2026-11', date(2026, 12, 2))
    assert [row(s, n)['spent'] for n in ['Groceries and household essentials', 'Gifts and Christmas', 'Heather personal']] == [Decimal('80.00'), Decimal('70.00'), Decimal('50.00')]
    assert s['known_spending']['total'] == Decimal('200.00')
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement as pure reads in one deferred read transaction (the same pattern as `finance_book.view`). **Step 4:** Pass. **Step 5:** Commit `Add budget month summary with coverage and plan completeness`.

### Task 6: Routes, templates, navigation

**Files:** Create `finance_budget_routes.py` and the templates listed in the file structure. Modify `finance_routes.py` (`init_app` end: `import finance_budget_routes; finance_budget_routes.register(app)`) and `templates/finance.html` (include the sub-nav under the header). Append to `static/css/finance.css`. Test `tests/test_finance_budget_routes.py`.

**Interfaces:**
- `register(app)` defines the routes. It reads `finance_routes._enabled`, `finance_routes.require_recent_mfa`, `finance_routes._csrf`, `finance_routes._open` and `finance_routes.UpgradeNeeded` **at register time**, so the existing test fixture's monkeypatch of `require_recent_mfa` applies.
- A `_budget_open(write=False)` context wraps `finance_routes._open` and raises `BudgetUpgradeNeeded` when `finance_budget.is_initialized(conn)` is false. Pages then render "Budgeting update needed" with status 200 for GET and 503 for POST.
- The actor is `current_user.id` (import `current_user` from `flask_login` into this module so tests can monkeypatch `finance_budget_routes.current_user`).

Routes (endpoint names in parentheses):

| Method | Path | Behavior |
|---|---|---|
| GET | `/finance/budget` (`finance_budget`) | `month` query `YYYY-MM` (default today's month). Renders `month_summary`: quality pill + reasons, coverage gaps, plan line with label and missing names, known spending/uncategorized, a category table (target with basis label, posted, pending, spent, % used), prev/next month links |
| GET | `/finance/transactions` (`finance_transactions`) | `view=review` (default; `review != 'accepted'` or `possible_duplicate_of` set, status not void/replaced) or `all`; filters `month`, `account`, `category`. Max 200 rows, newest first. Suggested rows show a "Suggested" pill + category; accepted rows show the category name(s) or "Split (n)" |
| POST | `/finance/transactions/accept` (`finance_accept_suggested`) | `txn_id` list → `accept_suggestions`; redirect back with the count in the query `?accepted=n` |
| GET/POST | `/finance/transactions/new` (`finance_txn_new`) | Manual entry: account, date, amount (signed, help text: "negative = money out"), description, status |
| GET | `/finance/transactions/<int:txn_id>` (`finance_txn`) | Detail: evidence (original description, source records, batch), classify form (kind select; allocation rows `alloc_category`, `alloc_person`, `alloc_amount`, `alloc_note` as repeated fields; 5 rows shown, prefilled, blank rows ignored), policy text for the selected categories (all categories' includes/excludes rendered in a `<details>`), link section (existing links with Unlink buttons; candidates per kind with Link buttons), Void |
| POST | `/finance/transactions/<int:txn_id>/classify` (`finance_txn_classify`) | Builds allocations from the repeated fields, skipping rows where amount and category are both blank. Kind `transfer` means no allocations (leaves the txn unmatched until linked). `keep_both=1` clears `possible_duplicate_of` |
| POST | `/finance/transactions/<int:txn_id>/link` (`finance_txn_link`) | `kind`, `other_id` (for `refund`, this txn is `from`; for `pending_posted`, `from` is whichever is pending) |
| POST | `/finance/links/<int:link_id>/unlink` (`finance_txn_unlink`) | `txn_id` for the redirect |
| POST | `/finance/transactions/<int:txn_id>/void` (`finance_txn_void`) | |
| GET | `/finance/imports` (`finance_imports`) | Upload form (account select of included budget accounts, file, label) + batch list |
| POST | `/finance/imports` (`finance_import_upload`) | `request.files['file'].read(MAX_BYTES + 1)` → `stage` → redirect to the batch |
| GET | `/finance/imports/<int:batch_id>` (`finance_import`) | Staged: mapping selects (from query params if present, else `default_mapping`), with preview counts, errors, sample and period fields prefilled with first/last date. Committed: summary + Rollback form (with "also remove reviewed" checkbox) |
| POST | `/finance/imports/<int:batch_id>/commit` (`finance_import_commit`) | Mapping from form fields `map_date`, `map_description`, `map_amount`, `map_debit`, `map_credit`, `map_posted_date`, `map_status`, `map_id`, `sign`; `action=preview` redirects to GET with the mapping as query params; `action=commit` commits |
| POST | `/finance/imports/<int:batch_id>/discard` (`finance_import_discard`) | |
| POST | `/finance/imports/<int:batch_id>/rollback` (`finance_import_rollback`) | `confirm_edited` checkbox |
| GET | `/finance/budget/settings` (`finance_budget_settings`) | People, accounts (included/role per finance account, provider and manual), coverage table (first/last covered date per account, declared-coverage form), categories (edit forms incl. policy includes/excludes, rollover, cap), targets (current target per category + "set from month" form) |
| POST | `/finance/budget/settings/<section>` (`finance_budget_settings_save`) | `section ∈ {people, account, category, target, coverage}` → the matching `finance_budget` function; redirect to `#section` |

Errors: `FinanceStoreError` messages from the budget modules are fixed, safe strings. Show them as the page error with status 400. `OSError`/`sqlite3.Error` → 503 "Budgeting is unavailable. Check the finance setup."

- [ ] **Step 1: Failing tests**

```python
import types
from decimal import Decimal
import pytest
import finance_store
from test_finance_routes import finance_app
from test_finance_budget import CHECKING, CARD_S, CARD_H

@pytest.fixture
def budget_app(finance_app, monkeypatch):
    import finance_budget_routes
    monkeypatch.setattr(finance_budget_routes, 'current_user', types.SimpleNamespace(id='alice'))
    app, path = finance_app
    from test_finance_budget import ledger_db
    ledger_db(path.parent.parent).close()
    return app, path

def csrf(client):
    client.get('/finance/budget')
    with client.session_transaction() as s:
        return s['csrf_token']

POSTS = ['/finance/transactions/accept', '/finance/transactions/new', '/finance/transactions/1/classify',
         '/finance/transactions/1/link', '/finance/links/1/unlink', '/finance/transactions/1/void',
         '/finance/imports', '/finance/imports/1/commit', '/finance/imports/1/discard', '/finance/imports/1/rollback',
         '/finance/budget/settings/people']
GETS = ['/finance/budget', '/finance/transactions', '/finance/transactions/new', '/finance/imports', '/finance/budget/settings']

def test_every_budget_post_requires_csrf_and_pages_are_private(budget_app):  # Test 18
    app, _ = budget_app
    client = app.test_client()
    for url in POSTS:
        assert client.post(url, data={}).status_code == 400, url
    for url in GETS:
        r = client.get(url)
        assert r.status_code == 200, url
        assert r.headers['Cache-Control'] == 'no-store'

def test_budget_routes_require_real_recent_mfa(app, tmp_path):  # Test 18 with the real gate
    app.config.update(FINANCE_ENABLED=True, FINANCE_DB_PATH=str(tmp_path / 'private' / 'finance.db'))
    client = app.test_client()
    for url in GETS:
        assert '/login' in client.get(url).location
    client.get('/login')
    with client.session_transaction() as s:
        token = s['csrf_token']
    client.post('/login', data={'username': 'alice', 'password': 'password1', 'csrf_token': token})
    for url in GETS:
        assert '/mfa' in client.get(url).location
    for url in POSTS:
        assert '/mfa' in client.post(url, data={}).location

def test_import_flow_end_to_end(budget_app):
    app, path = budget_app
    client = app.test_client(); token = csrf(client)
    data = {'csrf_token': token, 'account_id': CHECKING, 'label': 'nov',
            'file': (__import__('io').BytesIO(b'Date,Description,Amount\n11/01/2026,COFFEE,-4.50\n'), 'nov.csv')}
    r = client.post('/finance/imports', data=data, content_type='multipart/form-data')
    assert r.status_code == 302
    page = client.get(r.location)
    assert b'1 new' in page.data
    batch = r.location.rsplit('/', 1)[1]
    r = client.post(f'/finance/imports/{batch}/commit', data={'csrf_token': token, 'action': 'commit', 'map_date': '0',
        'map_description': '1', 'map_amount': '2', 'sign': 'outflow_negative', 'period_start': '2026-11-01', 'period_end': '2026-11-30'})
    assert r.status_code == 302
    assert b'COFFEE' in client.get('/finance/transactions').data

def test_split_classify_via_form_and_mismatch_error(budget_app):
    app, path = budget_app
    client = app.test_client(); token = csrf(client)
    client.post('/finance/transactions/new', data={'csrf_token': token, 'account_id': CARD_H, 'txn_date': '2026-11-04',
        'amount': '-200', 'description': 'TARGET', 'status': 'posted'})
    conn = finance_store.connect(str(path))
    ids = {r['name']: r['id'] for r in conn.execute('SELECT id, name FROM finance_budget_categories')}
    tid = conn.execute('SELECT id FROM finance_txns').fetchone()['id']; conn.close()
    form = {'csrf_token': token, 'kind': 'expense',
            'alloc_category': [ids['Groceries and household essentials'], ids['Gifts and Christmas'], ids['Heather personal']],
            'alloc_person': ['shared', 'shared', 'Heather'], 'alloc_amount': ['-80', '-70', '-40'], 'alloc_note': ['', '', '']}
    bad = client.post(f'/finance/transactions/{tid}/classify', data=form)
    assert bad.status_code == 400 and b'add up' in bad.data
    form['alloc_amount'] = ['-80', '-70', '-50']
    assert client.post(f'/finance/transactions/{tid}/classify', data=form).status_code == 302

def test_budget_page_shows_quality_and_plan_labels(budget_app):
    app, _ = budget_app
    page = app.test_client().get('/finance/budget?month=2026-11').data
    assert b'Insufficient data' in page and b'Plan incomplete' in page and b'Groceries and household essentials' in page
    assert b'planning' in page and b'estimate' in page

def test_budget_upgrade_needed_does_not_break_worksheet(finance_app):
    app, path = finance_app
    conn = finance_store.connect(str(path))
    conn.execute('DROP TABLE finance_txns'); conn.commit(); conn.close()
    client = app.test_client()
    assert b'Budgeting update needed' in client.get('/finance/budget').data
    assert client.get('/finance').status_code == 200
    assert b'Budgeting update needed' not in client.get('/finance').data

def test_bad_month_query_is_rejected_safely(budget_app):
    app, _ = budget_app
    assert app.test_client().get('/finance/budget?month=2026-13').status_code == 400
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement the routes and templates. Templates extend `base.html`, use `{% block fonts %}{% endblock %}` and `finance.css`, and include `finance_subnav.html`, which marks the active tab with `aria-current="page"`. Mobile: tables sit in `.finance-table-wrap` (horizontal scroll), and allocation rows stack below 640px. Status pills: `.budget-pill--complete|provisional|insufficient`, `.budget-pill--suggested`. Basis labels render as small text next to target amounts. **Step 4:** Pass, then run the whole suite. **Step 5:** Commit `Add budgeting pages: month view, transactions, imports, settings`.

### Task 7: Runbook and final verification

**Files:** Modify `docs/runbooks/finance.md` (a short "Budgeting (Phase 1)" section: what it stores, that it needs `scripts/migrate_finance.py` after deploy with a backup first, that CSV files aren't retained, and that coverage must be declared or imported for every included account); update `finance_store.py`'s module docstring line "There is no transaction storage" to point at `finance_ledger`.

- [ ] **Step 1:** Edit the docs.
- [ ] **Step 2:** `.venv/bin/python -m pytest -q` → all pass.
- [ ] **Step 3:** Launch the app locally with a demo finance DB and click through import → review → split → month view on desktop and a 390px viewport.
- [ ] **Step 4:** Commit `Document budgeting migration and data handling`.
