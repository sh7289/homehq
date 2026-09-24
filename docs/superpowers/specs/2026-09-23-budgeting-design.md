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

The product spec is too large for one implementation plan. Following the adversarial
review (`home_hq_budget_adversarial_review.md`, §4), work ships as three releases.
Each release is made of phases, and each phase gets its own plan, review and merge
and leaves Finance working.

| Release | Phase | Delivers | Acceptance tests |
|---|---|---|---|
| **R1: prove the workflow** | **1. Ledger foundation** | Canonical transactions, splits, transfers/card payments/refunds, pending→posted, CSV import + manual entry with idempotent dedupe, coverage, categories and household category policy, effective-dated targets (including income and planned savings), account budget roles, review inbox, month view with spending vs plan, data-quality status, operating-plan affordability line | Spec 4, 5, 6 (spending side), 9, 11, 12, 13, 18; review "uncategorized $400" |
| R1 | **2. Funds, cash and export** | Remaining/rollover per category, sinking funds (actual allocations only) with reserve funding status, simple dated checking forecast from balances + existing `finance_payments`, the three distinct cash numbers, the six sustainability dimensions panel, financial-exception alerts, transaction/category CSV export | Spec 1, 2, 3, 6, 7, 8, 9, 14, 15, 16; review "funds exceed backing", "card payment before paycheck" |
| **R2: forecasting and annual planning** | **3. Planning** | Recurring commitments and forecast occurrences, financing end dates, near-term fund gaps and catch-up contributions, savings plan vs actual cash accumulation, HSA measures, bonus allocation proposals (never automatic) | Spec 10; review "savings shortfall", "planned contribution unfunded", bonus test |
| R2 | **4. Review and close** | Weekly check-in record, month close with *originally closed* and *restated actuals* views, reopen/revise, V1 reports (§8.2) with variances shown without automatic judgment, user "mark for attention" | Spec 17 |
| **R3: reduce effort** | **5. Automation** | SimpleFIN transaction ingestion in the sync CLI, merchant rules, later AI suggestions, drift detection once history exists | Needs a separate decision after checking provider data |

R1 succeeds when Heather and Steve use it for one complete month and trust the numbers.
The rest of this document specifies Phase 1 in full, fixes the cross-phase
decisions later phases depend on, and adds the review's P0 requirements.

## Household financial sustainability (adversarial review, adopted)

Staying within discretionary targets never stands in for overall financial health. The
budget pages evaluate six dimensions separately. Each dimension shows its own status:
good, attention, or unknown. A good status in one dimension never hides an
attention or unknown status in another.

| Dimension | Phase | Basis |
|---|---|---|
| Operating affordability | 1 | Planned income − fixed − expected variable − allowances − fund contributions − planned savings = plan surplus/deficit |
| Discretionary compliance | 1 (spent) / 2 (remaining) | Capped categories vs targets |
| Reserve adequacy | 2 | Eligible reserve cash − fund allocations − holds; shortfall shown |
| Savings progress | 3 | Committed savings vs actual cash accumulation |
| Near-term liquidity | 2 | Dated checking forecast; first date below the configured minimum |
| Data reliability | 1 | complete / provisional / insufficient data (below) |

**Plan completeness.** The affordability line is labeled *validated* only when
every contributing target has basis `historical`. If any target is `planning` or
`estimate`, it is labeled *provisional*, with the count of unvalidated lines. An
operating category with no expected amount makes the plan *incomplete* and is
named; it is never treated as $0.

**Data-quality status per month.** *Insufficient data*: an included account has no
coverage for part of the month. *Provisional*: coverage is full, but unreviewed or
uncategorized outflows exist, or pending items are unresolved. *Complete*: neither.
Uncategorized outflows always count in total known spending and are shown as their
own line. Category remaining figures are labeled provisional while the month is not
complete.

**Two calculation paths (architectural invariant).** Budget accounting (what was
earned or consumed, by category and period, at purchase date) lives in
`finance_ledger_math.py`. Cash forecasting (what enters or leaves which account and
when, including card settlements) lives in a separate `finance_cashflow.py` (Phase 2).
They share transaction rows but not aggregation functions. A test asserts that a card
purchase appears only in budget accounting and its settlement only in cash flow.

**Funds hold only actual allocations.** A scheduled or planned contribution never
credits a fund. Only a user-confirmed allocation movement does. Unknown opening
balances stay "setup needed".

**Categorization is material, not forensic.** Splitting is optional. Settings hold a
short household category policy (per-category "includes / excludes" text, agreed
during setup) that is shown next to the category picker. Categorizing by
predominant purpose is a first-class option. Personal categories may use `reset`
or `carry` rollover. The seeded default stays `reset` until the household decides.

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
finance_budget_settings   key/value: primary_currency, budget_start ('2026-10-01', chosen by the household on 2026-09-24)
finance_budget_accounts   account_id PK → finance_accounts.id, included 0/1,
                          role (checking | savings | reserve | card | hsa | loan | other),
                          csv_sign (outflow_negative | outflow_positive | debit_credit_columns)
finance_budget_categories id, name, parent_id, type (operating | capped | sinking |
                          income | savings | transfer | other), default_person, active,
                          rollover (reset | capped | carry), rollover_cap, position,
                          policy_includes, policy_excludes, notes
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
total available), plus §6.1 targets effective `2026-10` with basis `planning` (moved from the spec's November start at the household's request).
Sinking funds get no balances; that is Phase 2 and starts at "setup needed".
Operating categories get no targets, because §2.3 forbids inventing baselines, so the plan starts
*incomplete* until the household enters them. Seeded alongside: an Income category
with the $11,952 figure as an `estimate` target, a Savings category with no target (the
savings objective is an open household decision, so the plan stays incomplete until it is
set), and each §2.2 commitment as a subcategory of its §6.2 parent (Mortgage under Housing,
Liz and Haley under Childcare, etc.) with its recorded amount as an `estimate` target and
the spec's caveat in the target note (Georgia Power: $215, note "range $200–230"; mattress:
note "placeholder"; RAV4 and fence: note with the expected end). A parent's planned amount
is the sum of its children's targets plus its own.
Tirzepatide ($199.67 monthly equivalent of $599 quarterly, `estimate`, HSA funding unconfirmed)
and a Routine medical line with no target sit under Health and medical. When a top-level category
has no target of its own, the plan names each of its subcategories that lacks one, so one known
line never makes a whole category look planned.

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

**Month view** (`/finance/budget?month=YYYY-MM`, Phase 1 version): the data-quality
status and a coverage banner naming each included account and the dates it lacks
(Test 13); the operating-plan affordability line with its validated / provisional /
incomplete label; total known spending, with an uncategorized line; spending by
category (posted and pending shown separately) against its target, labeled
planning / estimate / historical. Phase 2 turns
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
  coverage gaps (Test 13), data-quality status, uncategorized $400 counted in known
  spending, affordability line and its validated/provisional/incomplete label,
  no zero-denominator percentages.
- `test_finance_budget_routes.py`: MFA gate and CSRF on every new route, no-store
  headers, upgrade-needed state (Test 18), and the main flows end to end.
- A migration test: an existing snapshot-era DB upgrades additively and keeps its data.

## Out of scope for Phase 1

Rollover math, sinking-fund balances, reserve funding, cash forecasts, exports
(Phase 2); recurring commitments, savings actuals, HSA measures (Phase 3); weekly
review, closes, reports (Phase 4); merchant rules, SimpleFIN transactions, AI (Phase 5).
