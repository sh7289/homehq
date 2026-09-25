# Budgeting Phase 2: funds, cash and export — design

Parent design: `2026-09-23-budgeting-design.md`, which covers the decomposition, the six
sustainability dimensions and the two-calculation-paths invariant. Product source:
`home_hq_budgeting_product_spec.md`, plus the adopted adversarial review. This document
gives Phase 2 the detail Phase 1 got.

The household asked for this phase to continue autonomously. Decisions below are
marked **Ruling** where the parent design or product spec left a choice open.

## Scope

1. Remaining amounts and rollover for monthly capped categories (spec §6.4, Tests 1–3).
2. Sinking funds that hold only user-confirmed allocations, plus the reserve funding
   status (spec §6.5, review Finding 3, Test 14).
3. Recurring commitments: minimal dated schedules for income and bills (spec §5.5,
   Test 10). **Ruling:** these move forward from Phase 3. A checking forecast with no
   paycheck dates would report false shortfalls, and the review's "card payment
   before paycheck" test needs dated income. Phase 3 keeps matching occurrences to
   actual transactions, fund gaps, the savings plan, HSA measures and bonus proposals.
4. A dated checking forecast in its own module, `finance_cashflow.py`
   (spec §7.5, Tests 15–16, review Finding 9).
5. The three distinct numbers, the six-dimension status panel and financial
   exceptions (spec §5.1, review Findings 8 and 11).
6. CSV exports for transactions with splits and for monthly category totals (spec §8.3).

## Remaining and rollover (budget accounting)

Remaining amounts apply to `capped` categories only. For month *m*:

```
available(m) = target(m) + carry_in(m)
remaining(m) = available(m) − spent(m)          # spent as in Phase 1: posted + pending, net of refunds
carry_in(m)  = 0                                 if m is the budget start month, a month before it, or rollover = reset
             = remaining(m−1)                    if rollover = carry
             = min(cap, target(m) + remaining(m−1)) − target(m)   if rollover = capped
```

This matches Test 3: a $300 target with $100 left gives $400 available. A capped
category never has more than `cap` available, even after a new month's allocation.
**Ruling:** overspending carries forward as a negative into carry and capped
categories, so a $50 overspend in household wants leaves $250 the next month. A
reset category starts clean (Test 2). The chain starts at `budget_start` and never
reaches further back. A month with no target has no remaining figure; it shows "no
target". Remaining is labeled provisional whenever the month's data quality is not
complete.

## Sinking funds

A fund is a `sinking` category. There is a new table, `finance_fund_movements`
(`id, category_id, kind, amount, movement_date, note, created_by, created_at`), where
`kind` is one of:
- `opening`: the allocation already set aside. There is at most one per fund, and
  it can be replaced.
- `contribution`: unallocated cash moved into the fund. Positive.
- `release`: allocation moved back out to unallocated cash. Stored positive, and
  subtracted.
- `adjustment`: a signed correction.

```
balance = opening + Σ contributions − Σ releases + Σ adjustments − fund spending
fund spending = −Σ allocation amounts in the fund category (and its children) on expense/refund/reimbursement
                transactions dated on/after the opening movement date, status posted or pending
```

Movements are allocations, never spending (Test 14). Refunds restore the fund
(Test 9). A fund without an opening movement shows **Setup needed**, not $0. A
scheduled or planned contribution never touches a fund; only a movement a person
records does (review Finding 3).

**Reserve funding status.** Backing cash is the balance of accounts whose budget
role is `savings` or `reserve`, in the primary currency and not missing. Holds are a
settings value, `reserve_holds`, defaulting to 0.

```
unallocated reserve = backing cash − Σ positive fund balances − reserve_holds
```

- A negative result is a **funding shortfall** (the review's $7,000-against-$5,000
  test).
- If no reserve accounts are set up, or a backing account is stale or missing, the
  status is **unknown** and says why.

## Recurring commitments

New table `finance_recurring`: `id, name, direction (in|out), amount, amount_kind
(exact|estimate|variable|placeholder), cadence (weekly|biweekly|monthly|quarterly|
semiannual|annual|once), next_date NULL, end_date NULL, account_id NULL, category_id
NULL, status (active|paused|ended), notes, created_by, created_at, updated_by, updated_at`.

- **Occurrences** are generated from `next_date` forward, stepping by cadence and
  stopping at `end_date` (Test 10). Monthly steps keep the day of month, clamped to
  the month's length. A paused or ended commitment generates nothing. Occurrences are
  **forecasts, never transactions**.
- **Seed** (seed version 3): the §2.2 bills and the $11,952 income are created
  `active` with **no `next_date`**, because the brief records amounts but not
  dates. The fence and RAV4 notes carry their expected end. Their amounts come from
  the recorded figures, labeled with the same kinds as their targets; the mattress is
  a `placeholder`. Until each has a date and an account, the forecast lists it under
  **needs a date**. The forecast is then labeled incomplete; these commitments are
  never silently dropped or guessed.
- **Ruling:** editing a commitment's amount doesn't change the category's target.
  Targets are intentions and commitments are schedules; they stay separate (spec §2.2,
  "must not automatically ... raise discretionary budgets").

## Checking forecast (cash path, `finance_cashflow.py`)

- **Accounts:** included budget accounts with role `checking`, in the primary currency.
- **Start:** the sum of their current balances, as of the oldest `balance_at` among
  them. Any missing account makes the forecast **unknown**, and a stale one makes it
  **provisional**. A manual balance counts as stale after the worksheet's own rules.
- **Window:** from the as-of date through the end of the next calendar month, at
  least 35 days.
- **Events** dated after the as-of date:
  - occurrences of active recurring commitments whose `account_id` is a checking
    account (`in` adds, `out` subtracts)
  - `finance_payments` rows with status planned or scheduled whose `funding_id` is
    a checking account, excluding any linked to an actual transaction through
    `finance_txn_links.payment_id`.
- **Never subtracted:** card purchases, pending items, uncategorized transactions or
  category targets (Test 15, review Finding 9). Settlement is the only way card
  spending reaches this path.
- **Result:** a dated timeline with a running balance, the lowest point and its date,
  the first date below `checking_minimum` (a settings value, default 0), and the
  ending balance.
- **Past-due items:** these are listed as "date passed: confirm" and kept out of the
  projection, because the balance may already include them:
  - planned or scheduled card payments dated on or before the as-of date (they carry
    an explicit status to update)
  - commitment occurrences in the 7 days up to the as-of date (older ones are assumed
    settled).
  Commitments without a date or an account are listed under "needs a date or account".

**Available cash** = the checking start balance − `checking_minimum`.
**Projected cash after commitments** = the forecast's ending balance.
**Remaining category budget** comes from the budget path. The dashboard shows all
three side by side, never combined (Test 16).

## Status panel and exceptions

`finance_health.py` composes the two paths without merging them. Each dimension gets
`good | attention | unknown` and one sentence:

| Dimension | good | attention | unknown |
|---|---|---|---|
| Operating affordability | plan validated or provisional, surplus ≥ 0 | surplus < 0 | plan incomplete |
| Discretionary compliance | every capped category remaining ≥ 0 | any capped category over | month quality insufficient |
| Reserve adequacy | unallocated reserve ≥ 0 | shortfall | no reserve accounts, or backing stale/missing |
| Savings progress | — | — | always unknown in Phase 2 ("Savings plan arrives in a later update") |
| Near-term liquidity | forecast never drops below the minimum | a dated drop below the minimum | forecast unknown, or commitments need dates |
| Data reliability | quality complete | quality provisional | quality insufficient |

**Exceptions** are shown above everything else, and only for real risks (review
Finding 8):
- Checking is projected below the minimum on a date (shows the date and amount).
- A card payment falls on a day the running balance goes negative.
- Fund allocations exceed backing cash.
- A checking or card account in the budget is missing or stale.

Being $20 over dining is not an exception.

## Exports

These are GET routes under `/finance/exports/`, behind the same gates, sent as
`text/csv` downloads with `no-store`:

- **`transactions.csv?month=`**: one row per allocation, or one row for an
  unclassified transaction. Columns: date, posted_date, account (nickname), amount,
  currency, kind, status, review, merchant, category, person, allocation_amount,
  linked_to (ids), note.
- **`categories.csv?month=`**: one row per category. Columns: category, parent, type,
  target, basis, posted, pending, spent, remaining.

Text cells starting with `= + - @` or a tab get a leading `'`, to prevent formula
injection. Amount cells are left numeric. No credentials, hashes, source ids or raw
columns are exported.

## UI

- **Budget page** (the dashboard), top to bottom:
  1. Exceptions, if any.
  2. The six-dimension panel.
  3. The three numbers.
  4. Monthly allowances with large remaining figures.
  5. The data-quality and plan blocks from Phase 1.
  6. Operating and other tables.

  On phones the cash figures and allowances come before the tables (spec §5.1).
- **Funds** (`/finance/funds`): one card per fund with balance or "Setup needed", this
  month's spending and contributions, and forms to record an opening, contribution,
  release or adjustment. Also the reserve funding status and a movement history.
- **Cash forecast** (`/finance/forecast`): start balance and date, a timeline table,
  lowest point, past-due items, commitments that need dates, and a link to edit
  commitments.
- **Commitments** (`/finance/commitments`): a list plus add/edit forms.
- **Settings:** `checking_minimum` and `reserve_holds` fields.
- **Sub-nav:** Accounts · Budget · Transactions · Funds · Forecast · Imports · Budget settings.
  Commitments are reached from Forecast and Settings.

## Acceptance tests

- Spec Tests 1, 2, 3, 7, 8, 10, 14, 15 and 16.
- Review tests: funds exceed backing, and a card payment before the paycheck.
- The status panel never lets a good dimension hide an unknown or attention one.
- Exports: correct rows and columns, formula cells neutralized, security gates.
