# Budgeting Phase 2: Funds, Cash and Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the budget month view into the household dashboard. It covers:
- remaining amounts with rollover
- sinking funds backed by real allocations, with a reserve funding status
- dated recurring commitments
- a checking forecast that never double-counts card spending
- the six-dimension status panel and financial exceptions
- CSV exports

**Architecture:** New modules sit beside the Phase 1 ledger:
- `finance_funds.py`: fund movements, balances and reserve status
- `finance_recurring.py`: commitments and occurrence generation
- `finance_cashflow.py`: the cash path only
- `finance_health.py`: composes the budget path and the cash path into dimensions and exceptions
- `finance_export.py`: CSV exports

Remaining amounts and rollover go in `finance_ledger_math.py`, which stays the budget
path. Schema and seed changes go through `finance_budget.initialize`, seed version 3.

**Tech Stack:** Python 3.9+, Flask 3, Jinja, sqlite3, stdlib `csv`/`decimal`/`calendar`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-budgeting-phase2-design.md`, with its parent
`docs/superpowers/specs/2026-09-23-budgeting-design.md`.

## Global Constraints

- Money is `Decimal`, quantized to `0.01`, and stored as `TEXT`. Never use float.
- The budget path (`finance_ledger_math`) and the cash path (`finance_cashflow`) share no aggregation functions. Card purchases never appear in the cash path, and card settlements never appear in the budget path.
- A fund balance changes only through user-recorded movements and categorized spending or refunds. Schedules never credit a fund.
- "Unknown" is never shown as `0.00`. Setup-needed funds, missing backing and undated commitments each say what's missing.
- Every new route uses `_enabled` → `require_recent_mfa` → `_csrf` (POST) and POST-redirect-GET, and every GET export sends `no-store`.
- New tables are added to `finance_budget.TABLES`, so budget pages report "Budgeting update needed" until the migration runs.
- Seed data is never `historical`. Seeded commitments have no `next_date`.

## Review Focus

1. **A month before `budget_start`, or a start month with no target**, must not produce a rollover chain or a fake remaining figure. Pinned in Task 1 (`test_no_target_means_no_remaining`, `test_chain_starts_at_budget_start`).
2. **A monthly commitment anchored on the 31st** must fall on the last day of shorter months and never skip a month. Pinned in Task 3 (`test_month_end_anchor_clamps`).
3. **A forecast whose checking balance is several days old** must not re-subtract items dated between the balance date and today. They are listed as past-due-confirm. Pinned in Task 4 (`test_items_before_balance_date_are_not_resubtracted`).
4. **A fund with spending before its opening date** must not count that older spending. Pinned in Task 2 (`test_spending_before_opening_is_ignored`).
5. **A CSV export with a merchant named `=HYPERLINK(...)`** must be neutralized. Pinned in Task 6 (`test_formula_cells_are_neutralized`).

---

## File structure

| File | Responsibility |
|---|---|
| `finance_budget.py` (modify) | Schema for `finance_fund_movements` and `finance_recurring`; `TABLES`; seed v3 (commitments); settings helpers `money_setting` / `set_money_setting` |
| `finance_ledger_math.py` (modify) | `category_spending`, `allowances` (remaining and rollover); `month_summary` gains an `allowances` key |
| `finance_funds.py` (new) | `record_movement`, `funds`, `movements`, `reserve_status` |
| `finance_recurring.py` (new) | `save_commitment`, `commitments`, `occurrences` |
| `finance_cashflow.py` (new) | `forecast` |
| `finance_health.py` (new) | `dashboard` |
| `finance_export.py` (new) | `transactions_csv`, `categories_csv` |
| `finance_budget_routes.py` (modify) | Dashboard, funds, forecast, commitments, exports, new settings |
| Templates (new) | `finance_funds.html`, `finance_forecast.html`, `finance_commitments.html` |
| Templates (modify) | `finance_budget.html`, `finance_budget_settings.html`, `finance_subnav.html` |
| `static/css/finance.css` (append) | Styles for the new pages |
| Tests (new) | `tests/test_finance_rollover.py`, `test_finance_funds.py`, `test_finance_recurring.py`, `test_finance_cashflow.py`, `test_finance_health.py`, `test_finance_export.py`; route tests appended to `test_finance_budget_routes.py` |

Shared helpers come from `tests/test_finance_budget.py` (`ledger_db`, `cat`, `CHECKING`, `CARD_S`, `CARD_H`).
Tests for the new modules also use this helper, placed in `tests/test_finance_funds.py` and imported elsewhere:

```python
SAVINGS = 'a' * 64
HSA = 'b' * 64

def full_db(tmp_path, checking='4000', savings='5000', hsa='3000', balance_at='2026-10-15T12:00:00Z'):
    """ledger_db plus savings and HSA accounts, all with real balances as of balance_at."""
    conn = ledger_db(tmp_path)
    accounts = [(CHECKING, 'Checking', checking), (CARD_S, 'Steve card', '0'), (CARD_H, 'Heather card', '0'),
                (SAVINGS, 'Savings', savings), (HSA, 'HSA', hsa)]
    finance_store.record_sync(conn, {'accounts': [dict(id=i, label=l, currency='USD', balance=Decimal(b), balance_at=balance_at)
                                                  for i, l, b in accounts], 'warnings': [], 'complete': True},
                              now=datetime(2026, 10, 15, 12, tzinfo=timezone.utc))
    B.set_account(conn, SAVINGS, included=True, role='savings')
    B.set_account(conn, HSA, included=True, role='hsa')
    return conn
```

---

### Task 1: Remaining and rollover

**Interfaces (produces):**
- `category_spending(conn, month) -> dict[int, dict(posted=D, pending=D)]`: direct spending per category, net of refunds, for expense/refund/reimbursement allocations in posted or pending transactions. `month_summary` is refactored to use it, so the result is identical.
- `allowances(conn, month, today) -> list[dict(id, name, rollover, cap, target, carry_in, available, spent, remaining, pct_used, no_target)]` for active `capped` categories, in category order. Rules come from the design (spent includes children). With `no_target=True`, `available`, `remaining` and `pct_used` are `None`.
- `month_summary(...)['allowances']` equals `allowances(conn, month, today)`.

- [ ] **Step 1: Failing tests** in `tests/test_finance_rollover.py`:

```python
from datetime import date
from decimal import Decimal
import finance_budget as B, finance_ledger as L
from finance_ledger_math import allowances, month_summary
from test_finance_budget import ledger_db, cat, CARD_S

def spend(conn, name, amount, day):
    t = L.create_txn(conn, account_id=CARD_S, txn_date=day, amount=amount, description=name, actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, name), person='shared', amount=amount)], actor='s')

def row(conn, name, month):
    return next(a for a in allowances(conn, month, date(2027, 1, 31)) if a['name'] == name)

def test_dining_remaining_and_reset(tmp_path):  # Tests 1 and 2
    conn = ledger_db(tmp_path)
    spend(conn, 'Shared dining and entertainment', '-240', '2026-10-05')
    oct_ = row(conn, 'Shared dining and entertainment', '2026-10')
    assert (oct_['available'], oct_['spent'], oct_['remaining']) == (Decimal('650.00'), Decimal('240.00'), Decimal('410.00'))
    nov = row(conn, 'Shared dining and entertainment', '2026-11')
    assert (nov['carry_in'], nov['available']) == (Decimal('0.00'), Decimal('650.00'))

def test_household_wants_capped_rollover(tmp_path):  # Test 3
    conn = ledger_db(tmp_path)
    spend(conn, 'Household wants', '-200', '2026-10-05')
    assert row(conn, 'Household wants', '2026-11')['available'] == Decimal('400.00')
    assert row(conn, 'Household wants', '2026-12')['available'] == Decimal('700.00')
    assert row(conn, 'Household wants', '2027-01')['available'] == Decimal('900.00')  # capped, not 1000
    assert conn.execute('SELECT COUNT(*) FROM finance_txns').fetchone()[0] == 1       # no bank transaction created

def test_overspend_carries_for_capped_rollover(tmp_path):
    conn = ledger_db(tmp_path)
    spend(conn, 'Household wants', '-350', '2026-10-05')
    assert row(conn, 'Household wants', '2026-11')['available'] == Decimal('250.00')

def test_carry_rollover_unbounded(tmp_path):
    conn = ledger_db(tmp_path)
    B.save_category(conn, category_id=cat(conn, 'Steve personal'), name='Steve personal', type='capped',
                    default_person='Steve', rollover='carry')
    assert row(conn, 'Steve personal', '2027-01')['available'] == Decimal('1400.00')

def test_chain_starts_at_budget_start(tmp_path):
    conn = ledger_db(tmp_path)
    B.set_target(conn, category_id=cat(conn, 'Household wants'), effective_month='2026-09', amount='300',
                 basis='planning', note='', actor='s', today=date(2026, 9, 1))
    assert row(conn, 'Household wants', '2026-10')['carry_in'] == Decimal('0.00')

def test_no_target_means_no_remaining(tmp_path):
    conn = ledger_db(tmp_path)
    a = row(conn, 'Shared dining and entertainment', '2026-09')
    assert a['no_target'] and a['remaining'] is None and a['available'] is None

def test_summary_includes_allowances(tmp_path):
    conn = ledger_db(tmp_path)
    s = month_summary(conn, '2026-10', date(2026, 10, 15))
    assert [a['name'] for a in s['allowances']][:2] == ['Shared dining and entertainment', 'Household wants']
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. The chain helper iterates month by month from `max(budget_start month, first target month)` up to `m`. **Step 4:** Pass, and the Phase 1 math tests stay green. **Step 5:** Commit `Add remaining amounts with reset, capped and carry rollover`.

### Task 2: Sinking funds and reserve status

**Schema** (added to `finance_budget.SCHEMA` and `TABLES`):

```sql
CREATE TABLE IF NOT EXISTS finance_fund_movements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER NOT NULL REFERENCES finance_budget_categories(id),
  kind TEXT NOT NULL CHECK (kind IN ('opening','contribution','release','adjustment')),
  amount TEXT NOT NULL, movement_date TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL, created_at TEXT NOT NULL);
```

**Interfaces (produces):**
- `finance_budget.money_setting(conn, key) -> Decimal | None` and `set_money_setting(conn, key, value_text_or_blank)`. The keys are `checking_minimum` and `reserve_holds`. A blank value deletes the setting.
- `finance_funds.record_movement(conn, *, category_id, kind, amount, movement_date, note, actor, today=None) -> int`: the category must be an active `sinking` category, the date ≤ today, and the amount non-negative except for `adjustment`, which is signed and non-zero. An `opening` replaces any existing opening.
- `finance_funds.funds(conn, month) -> list[dict(id, name, setup_needed, opened_on, balance: D|None, spent_total, month_spent, month_added, target_note)]`: `balance` is `None` when setup is needed. `month_added` is contributions + adjustments − releases dated in the month.
- `finance_funds.movements(conn, category_id=None) -> list[dict]`: newest first, with the category name.
- `finance_funds.reserve_status(conn, now=None) -> dict(state in ('ok','shortfall','unknown'), reason: str, backing: D|None, allocated: D, holds: D, unallocated: D|None, accounts: [names])`.

- [ ] **Step 1: Failing tests** in `tests/test_finance_funds.py` (the `full_db` helper above goes at the top):

```python
def test_contribution_is_allocation_not_spending(tmp_path):  # Test 14
    conn = full_db(tmp_path)
    xmas = cat(conn, 'Gifts and Christmas')
    F.record_movement(conn, category_id=xmas, kind='opening', amount='500', movement_date='2026-10-01', note='', actor='s', today=date(2026, 10, 15))
    F.record_movement(conn, category_id=xmas, kind='contribution', amount='200', movement_date='2026-10-02', note='', actor='s', today=date(2026, 10, 15))
    t = L.create_txn(conn, account_id=CARD_H, txn_date='2026-10-05', amount='-150', description='GIFTS', actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=xmas, person='shared', amount='-150')], actor='s')
    fund = next(f for f in F.funds(conn, '2026-10') if f['id'] == xmas)
    assert fund['balance'] == Decimal('550.00')
    assert month_summary(conn, '2026-10', date(2026, 10, 15))['known_spending']['total'] == Decimal('150.00')

def test_split_and_refund_move_fund(tmp_path):  # Tests 5 and 9
    conn = full_db(tmp_path)
    travel, xmas = cat(conn, 'Travel'), cat(conn, 'Gifts and Christmas')
    for c in (travel, xmas):
        F.record_movement(conn, category_id=c, kind='opening', amount='1000', movement_date='2026-10-01', note='', actor='s', today=date(2026, 10, 15))
    t = L.create_txn(conn, account_id=CARD_H, txn_date='2026-10-04', amount='-200', description='TARGET', actor='h')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, 'Groceries and household essentials'), person='shared', amount='-80'),
        dict(category_id=xmas, person='shared', amount='-70'), dict(category_id=cat(conn, 'Heather personal'), person='Heather', amount='-50')], actor='h')
    hotel = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-06', amount='-500', description='HOTEL', actor='s')
    L.classify(conn, hotel, kind='expense', allocations=[dict(category_id=travel, person='shared', amount='-500')], actor='s')
    refund = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-09', amount='500', description='HOTEL', actor='s')
    L.link(conn, kind='refund', from_id=refund, to_id=hotel, actor='s')
    balances = {f['id']: f['balance'] for f in F.funds(conn, '2026-10')}
    assert balances[xmas] == Decimal('930.00') and balances[travel] == Decimal('1000.00')

def test_setup_needed_is_not_zero(tmp_path):
    conn = full_db(tmp_path)
    fund = next(f for f in F.funds(conn, '2026-10') if f['name'] == 'Travel')
    assert fund['setup_needed'] and fund['balance'] is None

def test_spending_before_opening_is_ignored(tmp_path):
    conn = full_db(tmp_path)
    travel = cat(conn, 'Travel')
    t = L.create_txn(conn, account_id=CARD_S, txn_date='2026-09-20', amount='-300', description='FLIGHT', actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=travel, person='shared', amount='-300')], actor='s')
    F.record_movement(conn, category_id=travel, kind='opening', amount='800', movement_date='2026-10-01', note='', actor='s', today=date(2026, 10, 15))
    assert next(f for f in F.funds(conn, '2026-10') if f['id'] == travel)['balance'] == Decimal('800.00')

def test_movement_validation(tmp_path):
    conn = full_db(tmp_path)
    with pytest.raises(FinanceStoreError):
        F.record_movement(conn, category_id=cat(conn, 'Household wants'), kind='contribution', amount='5', movement_date='2026-10-01', note='', actor='s', today=date(2026, 10, 15))
    with pytest.raises(FinanceStoreError):
        F.record_movement(conn, category_id=cat(conn, 'Travel'), kind='contribution', amount='-5', movement_date='2026-10-01', note='', actor='s', today=date(2026, 10, 15))
    with pytest.raises(FinanceStoreError):
        F.record_movement(conn, category_id=cat(conn, 'Travel'), kind='contribution', amount='5', movement_date='2026-10-20', note='', actor='s', today=date(2026, 10, 15))

def test_funds_exceeding_backing_show_shortfall(tmp_path):  # review test
    conn = full_db(tmp_path, savings='5000')
    for name, amount in [('Gifts and Christmas', '2000'), ('Travel', '3000'), ('Home/car/pet reserve', '2000')]:
        F.record_movement(conn, category_id=cat(conn, name), kind='opening', amount=amount, movement_date='2026-10-01', note='', actor='s', today=date(2026, 10, 15))
    r = F.reserve_status(conn, now=datetime(2026, 10, 15, 13, tzinfo=timezone.utc))
    assert (r['state'], r['backing'], r['allocated'], r['unallocated']) == ('shortfall', Decimal('5000.00'), Decimal('7000.00'), Decimal('-2000.00'))

def test_reserve_unknown_without_accounts_or_when_stale(tmp_path):
    conn = full_db(tmp_path)
    B.set_account(conn, SAVINGS, included=True, role='checking')
    assert F.reserve_status(conn, now=datetime(2026, 10, 15, 13, tzinfo=timezone.utc))['state'] == 'unknown'
    B.set_account(conn, SAVINGS, included=True, role='savings')
    assert F.reserve_status(conn, now=datetime(2026, 10, 25, tzinfo=timezone.utc))['state'] == 'unknown'

def test_holds_reduce_unallocated(tmp_path):
    conn = full_db(tmp_path, savings='5000')
    B.set_money_setting(conn, 'reserve_holds', '1000')
    r = F.reserve_status(conn, now=datetime(2026, 10, 15, 13, tzinfo=timezone.utc))
    assert (r['state'], r['unallocated']) == ('ok', Decimal('4000.00'))
```

- [ ] **Steps 2–5:** RED → implement → GREEN → commit `Add sinking-fund movements, balances and reserve funding status`.

### Task 3: Recurring commitments

**Schema:**

```sql
CREATE TABLE IF NOT EXISTS finance_recurring (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('in','out')), amount TEXT NOT NULL,
  amount_kind TEXT NOT NULL CHECK (amount_kind IN ('exact','estimate','variable','placeholder')),
  cadence TEXT NOT NULL CHECK (cadence IN ('weekly','biweekly','monthly','quarterly','semiannual','annual','once')),
  next_date TEXT, end_date TEXT, account_id TEXT REFERENCES finance_accounts(id),
  category_id INTEGER REFERENCES finance_budget_categories(id),
  status TEXT NOT NULL CHECK (status IN ('active','paused','ended')), notes TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_by TEXT, updated_at TEXT);
```

Seed v3 (`finance_budget._seed_v3`) runs once, only when `finance_recurring` is empty. It adds:
- Income: in, 11952, estimate, monthly, category Income, note "Deposit timing unknown; add the paydays"
- Each §2.2 bill: out, monthly, category = its subcategory, with the amount and kind listed below. Notes carry the end expectations.

| Bill | Amount | Kind |
|---|---:|---|
| Mortgage | 2910.21 | exact |
| Liz | 1000 | exact |
| Haley | 250 | exact |
| House cleaning | 300 | estimate |
| Yard service | 75 | estimate |
| Student loan | 490.35 | exact |
| Fence financing | 560 | exact |
| Home and auto insurance | 459 | estimate |
| T-Mobile | 177 | estimate |
| Georgia Power | 215 | variable |
| AT&T | 90 | estimate |
| Water | 130 | estimate |
| Natural gas | 35 | variable |
| Mattress financing | 250 | placeholder |
| RAV4 financing | 784.93 | exact |
| Tirzepatide | 599 | estimate (quarterly) |

All are `active` with no `next_date` and no account.

**Interfaces (produces):**
- `save_commitment(conn, *, commitment_id=None, name, direction, amount, amount_kind, cadence, next_date, end_date, account_id, category_id, status, notes, actor) -> int`. Blank `next_date`, `end_date`, `account_id` and `category_id` become NULL. Amount > 0. `end_date` ≥ `next_date` when both are set. The account must have budget settings, and the category must exist.
- `commitments(conn) -> list[dict]`: amount as `Decimal`, plus `account_name`, `category_name` and `needs_date` (active and no `next_date`).
- `occurrences(commitment: dict, start: date, end: date) -> list[date]`: a pure function giving the dates within `[start, end]`.

- [ ] **Step 1: Failing tests** in `tests/test_finance_recurring.py`:

```python
def c(**kw):
    base = dict(cadence='monthly', next_date='2026-10-31', end_date=None, status='active')
    base.update(kw); return base

def test_month_end_anchor_clamps():
    assert R.occurrences(c(), date(2026, 10, 1), date(2027, 3, 31)) == [
        date(2026, 10, 31), date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)]

def test_end_date_stops_occurrences():  # Test 10
    fence = c(next_date='2026-10-15', end_date='2027-04-15')
    dates = R.occurrences(fence, date(2026, 10, 1), date(2027, 12, 31))
    assert dates[-1] == date(2027, 4, 15) and len(dates) == 7

def test_other_cadences_and_status():
    assert R.occurrences(c(cadence='biweekly', next_date='2026-10-02'), date(2026, 10, 1), date(2026, 10, 31)) == [date(2026, 10, 2), date(2026, 10, 16), date(2026, 10, 30)]
    assert R.occurrences(c(cadence='quarterly', next_date='2026-08-20'), date(2026, 10, 1), date(2027, 3, 1)) == [date(2026, 11, 20), date(2027, 2, 20)]
    assert R.occurrences(c(cadence='once', next_date='2026-10-10'), date(2026, 10, 1), date(2026, 12, 1)) == [date(2026, 10, 10)]
    assert R.occurrences(c(status='paused'), date(2026, 10, 1), date(2026, 12, 1)) == []
    assert R.occurrences(c(next_date=None), date(2026, 10, 1), date(2026, 12, 1)) == []

def test_seeded_commitments_need_dates(tmp_path):
    conn = ledger_db(tmp_path)
    rows = {r['name']: r for r in R.commitments(conn)}
    assert rows['Mortgage']['amount'] == Decimal('2910.21') and rows['Mortgage']['needs_date']
    assert rows['Income']['direction'] == 'in' and rows['Mattress financing']['amount_kind'] == 'placeholder'
    assert rows['Tirzepatide']['cadence'] == 'quarterly'
    B.initialize(conn)
    assert len(R.commitments(conn)) == len(rows)

def test_save_commitment_validation_and_target_untouched(tmp_path):
    conn = ledger_db(tmp_path)
    mortgage = next(r for r in R.commitments(conn) if r['name'] == 'Mortgage')
    R.save_commitment(conn, commitment_id=mortgage['id'], name='Mortgage', direction='out', amount='2950', amount_kind='exact',
        cadence='monthly', next_date='2026-11-01', end_date='', account_id=CHECKING, category_id=mortgage['category_id'],
        status='active', notes='', actor='s')
    assert B.target_for(conn, cat(conn, 'Mortgage'), '2026-11')['amount'] == Decimal('2910.21')
    with pytest.raises(FinanceStoreError):
        R.save_commitment(conn, name='X', direction='out', amount='10', amount_kind='exact', cadence='monthly',
            next_date='2026-11-01', end_date='2026-10-01', account_id='', category_id='', status='active', notes='', actor='s')
    with pytest.raises(FinanceStoreError):
        R.save_commitment(conn, name='X', direction='sideways', amount='10', amount_kind='exact', cadence='monthly',
            next_date='', end_date='', account_id='', category_id='', status='active', notes='', actor='s')
```

- [ ] **Steps 2–5:** RED → implement (months via `calendar.monthrange`; anchor day = the day of `next_date`) → GREEN → commit `Add recurring commitments with dated occurrences`.

### Task 4: Checking forecast (cash path)

**Interfaces (produces):** `finance_cashflow.forecast(conn, today: date, now=None) -> dict`:

```python
{'state': 'ok'|'provisional'|'unknown', 'reasons': [str], 'accounts': [name], 'as_of': 'YYYY-MM-DD'|None,
 'start_balance': D|None, 'minimum': D, 'available_cash': D|None, 'end_date': 'YYYY-MM-DD',
 'events': [dict(date, label, amount, kind in ('income','bill','card_payment'), running)],
 'lowest': dict(date, balance)|None, 'below_minimum': dict(date, balance)|None,
 'uncovered_card_payments': [dict(date, label, amount, running)],
 'end_balance': D|None, 'past_due': [dict(date, label, amount, kind)], 'needs_date': [name]}
```

Rules come from the design. `end_date` = the last day of the month after `today`, at least `today + 35` days. Events are sorted by (date, income first, then outflows). `uncovered_card_payments` lists card payment events whose running balance is < 0.

- [ ] **Step 1: Failing tests** in `tests/test_finance_cashflow.py`:

```python
NOW = datetime(2026, 10, 15, 13, tzinfo=timezone.utc)
TODAY = date(2026, 10, 15)

def commit(conn, name, direction, amount, next_date, account=CHECKING, cadence='once'):
    return R.save_commitment(conn, name=name, direction=direction, amount=amount, amount_kind='exact', cadence=cadence,
        next_date=next_date, end_date='', account_id=account, category_id='', status='active', notes='', actor='s')

def clear_seeds(conn):
    conn.execute('DELETE FROM finance_recurring'); conn.commit()

def test_forecast_without_double_counting(tmp_path):  # Test 15
    conn = full_db(tmp_path, checking='4000'); clear_seeds(conn)
    commit(conn, 'Paycheck', 'in', '3000', '2026-10-20')
    commit(conn, 'Other bill', 'out', '1000', '2026-10-25')
    finance_payments.save(conn, card_id=CARD_S, funding_id=CHECKING, amount='2000', payment_date='2026-10-22', status='scheduled')
    t = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-10', amount='-2000', description='STUFF', actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, 'Household wants'), person='shared', amount='-2000')], actor='s')
    f = C.forecast(conn, TODAY, now=NOW)
    assert f['start_balance'] == Decimal('4000.00') and f['end_balance'] == Decimal('4000.00')
    assert [e['kind'] for e in f['events']] == ['income', 'card_payment', 'bill']

def test_card_payment_before_paycheck_is_flagged(tmp_path):  # review test
    conn = full_db(tmp_path, checking='1500'); clear_seeds(conn)
    finance_payments.save(conn, card_id=CARD_S, funding_id=CHECKING, amount='2000', payment_date='2026-10-18', status='scheduled')
    commit(conn, 'Paycheck', 'in', '3000', '2026-10-20')
    f = C.forecast(conn, TODAY, now=NOW)
    assert f['below_minimum'] == {'date': '2026-10-18', 'balance': Decimal('-500.00')}
    assert f['uncovered_card_payments'][0]['date'] == '2026-10-18'
    assert f['end_balance'] == Decimal('2500.00')

def test_hsa_purchase_leaves_checking_forecast_alone(tmp_path):  # Test 7
    conn = full_db(tmp_path, checking='4000'); clear_seeds(conn)
    t = L.create_txn(conn, account_id=HSA, txn_date='2026-10-12', amount='-599', description='PHARMACY', actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, 'Tirzepatide'), person='shared', amount='-599')], actor='s')
    assert C.forecast(conn, TODAY, now=NOW)['end_balance'] == Decimal('4000.00')
    s = month_summary(conn, '2026-10', TODAY)
    assert next(c for c in s['categories'] if c['name'] == 'Health and medical')['spent'] == Decimal('599.00')

def test_hsa_reimbursement_is_transfer_not_wages(tmp_path):  # Test 8
    conn = full_db(tmp_path); clear_seeds(conn)
    exp = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-28', amount='-200', description='DOCTOR', actor='s')
    L.classify(conn, exp, kind='expense', allocations=[dict(category_id=cat(conn, 'Health and medical'), person='shared', amount='-200')], actor='s')
    out = L.create_txn(conn, account_id=HSA, txn_date='2026-11-03', amount='-200', description='HSA REIMB', actor='s')
    inn = L.create_txn(conn, account_id=CHECKING, txn_date='2026-11-03', amount='200', description='HSA REIMB', actor='s')
    L.link(conn, kind='transfer', from_id=out, to_id=inn, actor='s')
    oct_, nov = month_summary(conn, '2026-10', date(2026, 12, 1)), month_summary(conn, '2026-11', date(2026, 12, 1))
    assert next(c for c in oct_['categories'] if c['name'] == 'Health and medical')['spent'] == Decimal('200.00')
    assert nov['income'] == Decimal('0.00') and nov['known_spending']['total'] == Decimal('0.00')

def test_items_before_balance_date_are_not_resubtracted(tmp_path):
    conn = full_db(tmp_path, balance_at='2026-10-12T12:00:00Z'); clear_seeds(conn)
    commit(conn, 'Water', 'out', '130', '2026-10-10')
    commit(conn, 'Gas', 'out', '35', '2026-10-13')
    f = C.forecast(conn, TODAY, now=NOW)
    assert [p['label'] for p in f['past_due']] == ['Water']
    assert [e['label'] for e in f['events']] == ['Gas']
    assert f['state'] == 'provisional'  # balance is three days old: stale window is 48h

def test_needs_date_and_unknown(tmp_path):
    conn = full_db(tmp_path)
    f = C.forecast(conn, TODAY, now=NOW)
    assert 'Mortgage' in f['needs_date'] and f['state'] == 'provisional'
    B.set_account(conn, CHECKING, included=False, role='checking')
    assert C.forecast(conn, TODAY, now=NOW)['state'] == 'unknown'

def test_minimum_and_available_cash(tmp_path):
    conn = full_db(tmp_path, checking='4000'); clear_seeds(conn)
    B.set_money_setting(conn, 'checking_minimum', '1000')
    commit(conn, 'Bill', 'out', '3500', '2026-10-20')
    f = C.forecast(conn, TODAY, now=NOW)
    assert f['available_cash'] == Decimal('3000.00')
    assert f['below_minimum'] == {'date': '2026-10-20', 'balance': Decimal('500.00')}

def test_paid_or_linked_card_payment_excluded(tmp_path):
    conn = full_db(tmp_path, checking='4000'); clear_seeds(conn)
    pid = finance_payments.save(conn, card_id=CARD_S, funding_id=CHECKING, amount='700', payment_date='2026-10-22', status='scheduled')
    out = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-14', amount='-700', description='PAY', actor='s')
    inn = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-14', amount='700', description='PAY', actor='s')
    L.link(conn, kind='card_payment', from_id=out, to_id=inn, actor='s', payment_id=pid)
    assert C.forecast(conn, TODAY, now=NOW)['events'] == []
```

(These tests need the funds setup from Task 2 for `full_db`, and `finance_payments.save` from Phase 0.)

- [ ] **Steps 2–5:** RED → implement → GREEN → commit `Add dated checking forecast that never double-counts card spending`.

### Task 5: Status panel, three numbers, exceptions

**Interfaces (produces):** `finance_health.dashboard(conn, month, today, now=None) -> dict(summary, allowances, funds, reserve, forecast, numbers, dimensions, exceptions)`, where:
- `numbers = dict(remaining_budget: D|None, available_cash: D|None, projected_cash: D|None)`. `remaining_budget` = the sum of `remaining` over allowances that have targets, or `None` if none do.
- `dimensions` = a list of six `dict(key, label, state, message)` in the design's order.
- `exceptions` = a list of `str`.

Forecast and reserve are about *now*, so the dashboard always computes them for `today`, whichever month is shown.

- [ ] **Step 1: Failing tests** in `tests/test_finance_health.py`:

```python
def dims(d): return {x['key']: x['state'] for x in d['dimensions']}

def test_budget_is_not_checking_cash(tmp_path):  # Test 16
    conn = full_db(tmp_path, checking='600'); conn.execute('DELETE FROM finance_recurring'); conn.commit()
    t = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-03', amount='-50', description='POTS', actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, 'Household wants'), person='shared', amount='-50')], actor='s')
    R.save_commitment(conn, name='Bill', direction='out', amount='500', amount_kind='exact', cadence='once', next_date='2026-10-20',
        end_date='', account_id=CHECKING, category_id='', status='active', notes='', actor='s')
    d = H.dashboard(conn, '2026-10', TODAY, now=NOW)
    wants = next(a for a in d['allowances'] if a['name'] == 'Household wants')
    assert wants['remaining'] == Decimal('250.00') and d['numbers']['projected_cash'] == Decimal('100.00')

def test_good_dimension_does_not_hide_others(tmp_path):
    conn = full_db(tmp_path, savings='5000')
    for name, amount in [('Gifts and Christmas', '4000'), ('Travel', '3000')]:
        F.record_movement(conn, category_id=cat(conn, name), kind='opening', amount=amount, movement_date='2026-10-01', note='', actor='s', today=TODAY)
    d = H.dashboard(conn, '2026-10', TODAY, now=NOW)
    states = dims(d)
    assert states['discretionary'] == 'unknown'  # no coverage, so compliance can't be judged
    assert states['reserve'] == 'attention' and states['savings'] == 'unknown' and states['affordability'] == 'unknown'
    assert any('exceed' in e for e in d['exceptions'])

def test_small_overspend_is_not_an_exception(tmp_path):
    conn = full_db(tmp_path, checking='50000'); conn.execute('DELETE FROM finance_recurring'); conn.commit()
    for a in (CHECKING, CARD_S, CARD_H, SAVINGS, HSA):
        B.declare_coverage(conn, account_id=a, start='2026-10-01', end='2026-10-15', actor='s', today=TODAY)
    t = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-03', amount='-670', description='DINNERS', actor='s')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, 'Shared dining and entertainment'), person='shared', amount='-670')], actor='s')
    d = H.dashboard(conn, '2026-10', TODAY, now=NOW)
    assert dims(d)['discretionary'] == 'attention' and d['exceptions'] == []

def test_projected_shortfall_is_an_exception(tmp_path):
    conn = full_db(tmp_path, checking='100'); conn.execute('DELETE FROM finance_recurring'); conn.commit()
    R.save_commitment(conn, name='Mortgage', direction='out', amount='2910.21', amount_kind='exact', cadence='once', next_date='2026-11-01',
        end_date='', account_id=CHECKING, category_id='', status='active', notes='', actor='s')
    d = H.dashboard(conn, '2026-10', TODAY, now=NOW)
    assert dims(d)['liquidity'] == 'attention'
    assert any('2026-11-01' in e for e in d['exceptions'])
```

- [ ] **Steps 2–5:** RED → implement → GREEN → commit `Add status panel, three cash numbers and financial exceptions`.

### Task 6: CSV exports

**Interfaces:** `finance_export.transactions_csv(conn, month) -> str` and `categories_csv(conn, month, today) -> str`, with the columns from the design. `_cell(text)` neutralizes values starting with `= + - @ \t \r` by prefixing `'`. It applies to text columns only.

- [ ] **Step 1: Failing tests** in `tests/test_finance_export.py`:

```python
def test_transactions_export_rows_per_allocation(tmp_path):
    conn = ledger_db(tmp_path)
    t = L.create_txn(conn, account_id=CARD_H, txn_date='2026-10-04', amount='-200', description='TARGET', actor='h')
    L.classify(conn, t, kind='expense', allocations=[dict(category_id=cat(conn, 'Groceries and household essentials'), person='shared', amount='-150'),
        dict(category_id=cat(conn, 'Heather personal'), person='Heather', amount='-50')], actor='h')
    L.create_txn(conn, account_id=CARD_H, txn_date='2026-10-05', amount='-9', description='MYSTERY', actor='h')
    rows = list(csv.DictReader(io.StringIO(E.transactions_csv(conn, '2026-10'))))
    assert [(r['merchant'], r['category'], r['allocation_amount']) for r in rows] == [
        ('TARGET', 'Groceries and household essentials', '-150.00'), ('TARGET', 'Heather personal', '-50.00'), ('MYSTERY', '', '')]
    assert rows[0]['account'] == 'Heather card' and 'source' not in rows[0]

def test_formula_cells_are_neutralized(tmp_path):
    conn = ledger_db(tmp_path)
    L.create_txn(conn, account_id=CARD_H, txn_date='2026-10-04', amount='-5', description='=HYPERLINK("x")', actor='h')
    rows = list(csv.DictReader(io.StringIO(E.transactions_csv(conn, '2026-10'))))
    assert rows[0]['merchant'].startswith("'=") and rows[0]['amount'] == '-5.00'

def test_categories_export_includes_remaining(tmp_path):
    conn = ledger_db(tmp_path)
    rows = {r['category']: r for r in csv.DictReader(io.StringIO(E.categories_csv(conn, '2026-10', date(2026, 10, 15))))}
    assert rows['Shared dining and entertainment']['target'] == '650.00'
    assert rows['Shared dining and entertainment']['remaining'] == '650.00'
    assert rows['Groceries and household essentials']['target'] == ''
```

- [ ] **Steps 2–5:** RED → implement → GREEN → commit `Add CSV exports for transactions and category totals`.

### Task 7: Pages and routes

**Routes** (all registered in `finance_budget_routes.register` with the existing `route()` helper):

| Method | Path | Endpoint | Behavior |
|---|---|---|---|
| GET | `/finance/budget` | `finance_budget` | Now renders `finance_health.dashboard` (exceptions, dimensions, numbers, allowances with remaining) above the Phase 1 content |
| GET | `/finance/funds` | `finance_funds` | Fund cards, reserve status, movement history (latest 50), movement forms |
| POST | `/finance/funds/movements` | `finance_fund_movement` | `category_id, kind, amount, movement_date, note` |
| GET | `/finance/forecast` | `finance_forecast` | Forecast timeline, past-due list, needs-date list |
| GET | `/finance/commitments` | `finance_commitments` | List + add form + edit forms |
| POST | `/finance/commitments` | `finance_commitment_save` | Fields of `save_commitment` (`commitment_id` optional) |
| GET | `/finance/exports/transactions.csv` | `finance_export_transactions` | `month` required; attachment `transactions-YYYY-MM.csv` |
| GET | `/finance/exports/categories.csv` | `finance_export_categories` | Same for categories |

Settings gains a `limits` section (`checking_minimum`, `reserve_holds`) on the existing `settings/<section>` POST. The sub-nav gains Funds and Forecast. The Budget page links the exports.

- [ ] **Step 1: Failing tests** appended to `tests/test_finance_budget_routes.py`:

```python
P2_GETS = ['/finance/funds', '/finance/forecast', '/finance/commitments',
           '/finance/exports/transactions.csv?month=2026-10', '/finance/exports/categories.csv?month=2026-10']
P2_POSTS = ['/finance/funds/movements', '/finance/commitments', '/finance/budget/settings/limits']

def test_phase2_routes_are_gated_and_private(budget_app):
    app, _ = budget_app
    client = app.test_client()
    for url in P2_POSTS:
        assert client.post(url, data={}).status_code == 400, url
    for url in P2_GETS:
        r = client.get(url)
        assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store', url
    r = client.get('/finance/exports/transactions.csv?month=2026-10')
    assert r.mimetype == 'text/csv' and 'attachment' in r.headers['Content-Disposition']

def test_phase2_routes_require_real_mfa(app, tmp_path):
    app.config.update(FINANCE_ENABLED=True, FINANCE_DB_PATH=str(tmp_path / 'private' / 'finance.db'))
    client = app.test_client()
    for url in P2_GETS:
        assert '/login' in client.get(url).location, url

def test_fund_movement_and_dashboard(budget_app):
    app, path = budget_app
    client = app.test_client(); token = csrf(client)
    conn = finance_store.connect(str(path))
    travel = conn.execute("SELECT id FROM finance_budget_categories WHERE name='Travel'").fetchone()['id']
    conn.close()
    r = client.post('/finance/funds/movements', data={'csrf_token': token, 'category_id': travel, 'kind': 'opening',
                                                      'amount': '1200', 'movement_date': '2026-09-01', 'note': ''})
    assert r.status_code == 302
    page = client.get('/finance/funds').data
    assert b'1,200.00' in page and b'Setup needed' in page  # other funds still need setup
    dash = client.get('/finance/budget').data
    assert b'Remaining category budget' in dash and b'Projected cash after commitments' in dash and b'Available cash' in dash
    assert b'Savings progress' in dash

def test_commitment_form_and_bad_month_export(budget_app):
    app, _ = budget_app
    client = app.test_client(); token = csrf(client)
    r = client.post('/finance/commitments', data={'csrf_token': token, 'name': 'Paycheck', 'direction': 'in', 'amount': '3000',
        'amount_kind': 'exact', 'cadence': 'biweekly', 'next_date': '2026-10-02', 'end_date': '', 'account_id': CHECKING,
        'category_id': '', 'status': 'active', 'notes': ''})
    assert r.status_code == 302
    assert b'Paycheck' in client.get('/finance/commitments').data
    assert client.get('/finance/exports/transactions.csv?month=bad').status_code == 400

def test_limits_settings(budget_app):
    app, _ = budget_app
    client = app.test_client(); token = csrf(client)
    assert client.post('/finance/budget/settings/limits', data={'csrf_token': token, 'checking_minimum': '1000', 'reserve_holds': ''}).status_code == 302
    assert client.post('/finance/budget/settings/limits', data={'csrf_token': token, 'checking_minimum': 'abc', 'reserve_holds': ''}).status_code == 400
```

- [ ] **Steps 2–5:** RED → implement routes and templates → GREEN → full suite → commit `Add dashboard, funds, forecast, commitments and export pages`.

### Task 8: Docs, visual check, review, PR

- [ ] Runbook: add a Budgeting Phase 2 subsection covering setting up reserve accounts, fund openings, adding paydays and due dates to commitments, the checking minimum, and exports. Note that exports contain household financial data.
- [ ] Update the parent design's decomposition table: recurring commitments moved to Phase 2.
- [ ] Visual check at desktop width and in a 390px iframe (the throwaway preview harness).
- [ ] Full suite; final whole-branch self-review; fix Critical and Important findings with tests.
- [ ] Push `budget-phase2` and open a PR stacked on `budget-october-start`.
