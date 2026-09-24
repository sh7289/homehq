# Household budgeting: technical design

Product source of truth: `home_hq_budgeting_product_spec.md` (September 23, 2026),
referred to below as "the product spec" with its section numbers. This document
maps that spec onto the Home HQ codebase and splits it into buildable phases.
It does not restate product behavior the spec already defines.

## Repository assessment (product spec §9.1, §11.1)

Verified against `main` at `fab934d`:

- Finance lives in a private SQLite database outside the repo (`finance_store.connect`,
  0600 file, 0700 parent, no symlinks). Money is stored as decimal `TEXT`, never float.
- Schema changes are additive and happen only through `finance_book.initialize`, run by
  the operator (`scripts/migrate_finance.py`) or sync. Web GETs open the DB read-only
  and never migrate; an old schema raises `UpgradeNeeded`.
- All `/finance*` routes use `_enabled` + `require_recent_mfa`; POSTs use `_csrf` and
  POST-redirect-GET; responses get `no-store` and framing headers via `protect_finance`.
- Accounts: `finance_accounts` (provider SHA-256 ids and `manual-<uuid>` ids) with
  nickname, owner, group → bucket (liquid/illiquid/debt/unassigned), `included`,
  `debt_sign`. Card payments: `finance_payments` (card, funding account, amount, date,
  planned/scheduled/paid/cancelled).
- SimpleFIN is called with `balances-only=1`. There is no transaction storage anywhere.
- Users are two env-configured logins (`current_user.id` is the username).
- UI: server-rendered Jinja, one `finance.html` worksheet, custom CSS, little JS.
- Test suite: 726 passing (pytest). Finance tests use temp private DBs.

Conclusion: nothing blocks building on the existing Finance DB, account ids and
security gates. SimpleFIN transaction sync stays out of V1, as the product spec recommends (§3.3).

## Decomposition

The product spec is too large for one implementation plan. It is built in five phases,
each with its own plan, branch-level review, and merge. Each phase leaves Finance working.

| Phase | Delivers | Product spec acceptance tests |
|---|---|---|
| **1. Ledger foundation** | Canonical transactions, splits, transfers/card payments/refunds, pending→posted, CSV import + manual entry with idempotent dedupe, coverage, categories, effective-dated targets, account budget roles, review inbox, monthly spending by category | 4, 5, 6 (spending side), 9, 11, 12, 13, 18 |
| **2. Budget dashboard** | Remaining/rollover per category, sinking funds and fund movements, recurring commitments and forecast occurrences, `finance_payments` integration, the three distinct cash numbers | 1, 2, 3, 7, 8, 10, 14, 15, 16 |
| **3. Weekly review and month close** | Weekly check-in record, versioned month closes, reopen/revise | 17 |
| **4. Reports and exports** | V1 reports (§8.2), CSV exports (§8.3), baseline variance alerts (§6.3) | none (report checks) |
| **5. Automation** | SimpleFIN transaction ingestion in the sync CLI, merchant rules, later AI suggestions | Needs a separate decision after checking provider data |

The rest of this document specifies Phase 1 in full and fixes the cross-phase
data model decisions that later phases depend on.

## Cross-phase decisions

**Storage.** New tables live in the private finance DB, prefixed `finance_budget_*` /
`finance_txn*`. They are created by a new `finance_budget.initialize(conn)` called from
`finance_book.initialize`, so the existing migration CLI picks them up. Budget routes
check their own tables (`finance_budget.is_initialized`) and show an "upgrade needed"
state. The existing worksheet's `is_initialized` check is unchanged, so deploying
code before migrating never breaks the current Finance page.

**Sign convention.** A canonical transaction's `amount` is signed from the account's
point of view: negative means money leaving the household through that account
(checking debit, card purchase); positive means money coming in (deposit, refund,
the card-side credit of a card payment). The CSV importer normalizes each file with
a per-account setting: outflows negative, outflows positive, or separate debit/credit
columns. Nothing assumes a positive number is income (§7.4). Amounts are decimal
`TEXT` limited to 2 decimal places for supported ISO currencies. Budget totals only
use the primary operating currency (USD by default). Other currencies are listed
separately and never converted.

**Allocations carry meaning, transactions carry evidence.** A transaction's
`kind` (expense, income, refund, reimbursement, transfer, card_payment) says what
happened. Its allocation rows (category, person, amount) say what it was for. A
classified transaction's allocations sum to its signed amount exactly, and a
mismatched split is rejected (§5.3). Transfers and card payments have no category
allocations. They are excluded from spending by kind, and their two sides are
linked. Economic spending for a category is the negated sum of its allocation
amounts on expense, refund and reimbursement transactions, so refunds net
against the category they restore (§7.7, Test 9).

**Person attribution** is a property of an allocation: `shared` or a household
username. It is never inferred from the card owner (Test 4). Personal categories
carry a default person.

**Links** live in one table with a `kind` column: transfer (two sides), card_payment
(checking debit ↔ card credit), refund (credit → original expense), pending_posted
(pending → posted replacement). A pending transaction that has been replaced gets
status `replaced` and is excluded from totals, but it stays traceable (Test 11).
One side of a transfer can be recorded without the other; it is then flagged
unmatched, and nothing is invented (§7.6).

**Evidence is immutable.** `source_records` keep the sanitized original CSV columns
and the original description. The canonical transaction keeps an editable merchant
name and classification. Reimporting never overwrites user edits (§7.9, Test 12).

**Centralized math.** All derived numbers (spending by category, remaining,
fund balances, coverage) come from one pure module, `finance_ledger_math.py`, that
views and exports call. Views never compute totals themselves (§8.1).

## Phase 1 data model

```
finance_budget_settings   key/value: primary_currency, budget_start ('2026-11-01', unconfirmed)
finance_budget_accounts   account_id PK → finance_accounts.id, included 0/1,
                          role (checking | savings | reserve | card | hsa | loan | other),
                          csv_sign (outflow_negative | outflow_positive | debit_credit_columns)
finance_budget_categories id, name, parent_id, type (operating | capped | sinking |
                          income | transfer | other), default_person, active,
                          rollover (reset | capped | carry), rollover_cap, position, notes
finance_budget_targets    category_id, effective_month 'YYYY-MM', amount, basis
                          (planning | estimate | historical), note
                          -- the target for a month is the latest effective_month <= that month
finance_import_batches    id, account_id, filename_label, imported_by, imported_at,
                          period_start, period_end (user-confirmed coverage), row_count,
                          new_count, duplicate_count, rolled_back_at
finance_source_records    id, batch_id, account_id, source (csv | manual),
                          source_txn_id NULL, fingerprint, occurrence, columns_json, txn_id
                          UNIQUE(account_id, source, fingerprint, occurrence)
                          UNIQUE(account_id, source, source_txn_id) WHERE source_txn_id NOT NULL
finance_txns              id, account_id, txn_date, posted_date, amount, currency,
                          original_description, merchant, status (pending | posted |
                          void | replaced), kind, review (unreviewed | suggested | accepted),
                          suggested_category_id, suggestion_source, note,
                          created_by, created_at, updated_by, updated_at
finance_txn_allocations   id, txn_id, category_id, person, amount, note
finance_txn_links         id, kind, from_txn_id, to_txn_id NULL, created_by, created_at
```

**Seed data** (inserted once, when the categories table is created): the §6.2 category
hierarchy with its types and §6.4 rollover defaults (household wants: capped at $900
total available), plus §6.1 targets effective `2026-11` with basis `planning`.
Sinking funds get no balances; that is Phase 2 and starts at "setup needed".
Operating categories get no targets, because §2.3 forbids inventing baselines.

## Phase 1 behavior

**CSV import** (`finance_import.py`): upload one file for one account → map columns
(date, description, amount or debit/credit, optional id/posted date/status) → preview
→ confirm coverage period → commit. Column mappings are remembered per account.
Limits: 2 MB and 5,000 rows per file, parsed within the request (bounded, §9.3).
Dedupe fingerprint: account + date + amount + normalized description. `occurrence`
counts repeats within one file, so two identical same-day purchases in one file
both import. Reimporting the same file matches all rows, and an overlapping export
only adds rows beyond the counts it already has (Test 12). A provider id, when a
column has one, takes precedence. Cross-source near-matches (a manual entry vs a
CSV row) are not merged automatically. They are shown as "possible duplicate" for
review. Rollback removes a batch's transactions. If any of them were accepted or
edited, rollback lists them and needs explicit confirmation.

**Manual entry**: the same validation path, creating a `manual` source record.

**Review inbox** (`/finance/transactions`): filters for needs review, month,
account and category. Rows show date, merchant/description, amount, account,
category, person, status, source, and a visible suggested vs accepted marker (§5.2).
Suggestions in Phase 1 come only from the user's most recent accepted
classification of the same normalized merchant (§7.10 step 3). "Accept all
suggested" works on the filtered list after a confirmation that shows the count.

**Transaction detail** (`/finance/transactions/<id>`): kind, merchant, note,
allocation rows (add/remove rows for splits, with a live remaining-to-allocate figure
and server-side exact-sum validation), link to a transfer counterpart / original
expense / pending original from a short candidate list (opposite-sign equal amount within 7 days for transfers and card payments; same merchant within 180 days for refunds; same account and merchant within 10 days for pending,
other accounts), mark reviewed. Accepting a card payment link can reference an
existing `finance_payments` row (Test 6); the full forecast integration is Phase 2.

**Settings** (`/finance/budget/settings`): per-account budget inclusion, role and CSV
sign; category add/edit/deactivate; new effective-dated target (never edits a past
target row in place); coverage table with first/last covered date per included account.

**Month view** (`/finance/budget?month=YYYY-MM`, Phase 1 version): spending by category
(posted and pending shown separately), uncategorized total, target where one
exists (labeled planning / estimate / historical), and a coverage banner naming each
included account and the dates it lacks for that month (Test 13). Phase 2 turns
this page into the full dashboard.

**Navigation**: a Finance sub-nav partial (Overview · Transactions · Budget · Settings)
added to `finance.html` and the new pages. The global nav is unchanged.

## Security (Test 18)

New routes live in `finance_budget_routes.py`, registered from
`finance_routes.init_app`, and reuse `_enabled`, `require_recent_mfa`, `_csrf` and
`_open`. `/finance/...` paths are already covered by `protect_finance`. CSV uploads
are parsed in memory, never written to disk or the uploads dir, and the raw file is
not retained. `columns_json` stores only the mapped columns, and values containing
long digit runs (account numbers) are masked the same way provider display names are.
Error messages are generic and never echo file content.

## Testing

TDD per module, in the existing pytest style with temp private DBs:

- `test_finance_ledger.py`: sign normalization, allocation exact-sum, split (Test 5),
  card owner vs category (Test 4), card payment excluded from spending (Test 6),
  refund netting (Test 9), pending replacement (Test 11).
- `test_finance_import.py`: idempotent reimport and overlapping files, legitimate
  identical rows (Test 12), corrections survive reimport, rollback, masking, size limits,
  malformed CSVs.
- `test_finance_ledger_math.py`: category totals, effective-dated target lookup,
  coverage gaps (Test 13), no zero-denominator percentages.
- `test_finance_budget_routes.py`: MFA gate and CSRF on every new route, no-store
  headers, upgrade-needed state (Test 18), and the main flows end to end.
- A migration test: an existing snapshot-era DB upgrades additively and keeps its data.

## Out of scope for Phase 1

Rollover math, sinking-fund balances, recurring commitments, forecasts, weekly
review, closes, exports, merchant rules, SimpleFIN transactions, AI categorization.
