# Household Finance Snapshots Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development task-by-task.

**Goal:** Make finance useful as an identifiable, grouped household snapshot.
**Architecture:** Add household metadata/manual entries/frozen snapshots to the private
SQLite store; preserve existing sync data and MFA; Flask handles private forms.
**Tech Stack:** Python 3.9-compatible Flask/Jinja/SQLite/Decimal; no new dependencies.
**Spec:** ../specs/2026-09-07-finance-snapshots-design.md (approved conversation design).

## Global constraints

No real finance data or credentials in repo/tests. No changes to the separate UI tree.
All monetary arithmetic uses exact Decimal with sufficient context. No FX conversion.
No new provider requests in web handlers. Migration only by CLI/sync, never GET.
All finance writes require recent MFA and synchronizer CSRF.

## Task 1 — Data and provider identity (implementation agent)

Files: simplefin.py, finance_store.py, new finance_book.py, scripts/migrate_finance.py,
provider/store tests and tests/test_finance_book.py. Own these exclusively.

Interfaces (binding for UI):
- finance_book.initialize(conn): additive/idempotent schema migration; called by finance_store.connect.
- finance_book.view(conn, now=None): dict with accounts (existing dashboard row keys plus
  nickname, provider_name, institution, owner, group_id, position, included bool, source
  'provider'/'manual', debt_sign 'unconfirmed'/'positive'/'negative'), groups
  [{id,name,bucket,position}], sections [{group:group dict, accounts:list,
  totals:{currency:Decimal}}], summary:{bucket:{currency:Decimal}}, coverage:{missing:int,
  stale:int,unassigned:int,excluded:int,unconfirmed_debt:int}, complete:bool,
  last_attempt and last_success (original sync contract).
- finance_book.update_account(conn, account_id, *, nickname, owner, group_id,
  position, included, debt_sign): metadata only, validation fails FinanceStoreError.
- finance_book.save_group(conn, *, group_id=None, name, bucket, position): returns id.
- finance_book.add_manual(conn, *, nickname, currency, balance, balance_at, owner,
  group_id, position=0): returns id; balance input decimal string, date ISO YYYY-MM-DD.
- finance_book.update_manual(conn, account_id, *, balance, balance_at): date not future.
- finance_book.save_snapshot(conn, *, actor, now=None): returns integer id.
- finance_book.list_snapshots(conn): [{id,captured_at,actor,complete}]
- finance_book.snapshot(conn, snapshot_id): frozen view dict plus id,captured_at,actor,
  previous_id, changes:[{group_id,group_name,currency,current:Decimal,previous:Decimal,
  delta:Decimal}], scope_changed:bool. Unknown id raises FinanceStoreError.

Steps:
- [x] Write/run failing tests for provider identity sanitization, old schema migration,
  sync preserving metadata, exact currency totals, manual date/amount validation,
  omitted/stale/unclassified coverage, debt convention, immutable snapshots after
  subsequent sync/edit, previous comparison scope changes, invalid mutation rollback.
- [x] Implement additive schema, validated mutations and transactional frozen snapshots.
  Use `BEGIN IMMEDIATE` for multi-read/write operations, no nested implicit commits.
- [x] Expose CLI `scripts/migrate_finance.py --db ABSOLUTE_PATH` requiring existing
  database, outside Git, private permissions; only prints generic migration success.
- [x] Run focused tests and report interface details. No commit until parent integration.

## Task 2 — Household worksheet UI (controller)

Files: finance_routes.py, templates/finance.html and new finance_account.html,
finance_snapshot.html, static/css/finance.css, tests/test_finance_routes.py.

Steps:
- [x] Add failing route tests for csrf/recent-MFA on every mutation, names/grouped
  rows/manual source, migration-needed state, edit persistence, snapshot freezing.
- [x] Implement GET /finance worksheet; GET/POST /finance/accounts/<id>/edit;
  POST /finance/manual, /finance/groups, /finance/snapshots; GET /finance/snapshots/<id>.
  Use the Task1 contract; validate integer/form inputs, return safe errors, no provider imports.
- [x] Render compact section tables Account/Owner/Balance/Updated and subtotals;
  inline tools for manual/group creation and snapshot saving; dedicated edit forms.
  Empty/not migrated states explain server migration; snapshots show frozen rows,
  original dates and prior comparison with explicit scope/incomplete caveats.
- [x] Add deployment upgrade guide (migration before restart, next sync enriches names,
  existing nicknames retained, finance DB now receives local edits), test full suite,
  independently review and commit to codex/finance-snapshots. No deployment this session.

## Task 3 — Manually recorded card-payment schedule (user addition)

Files: new finance_payments.py and tests/test_finance_payments.py (data agent);
finance_book.py initialization/frozen view, finance_routes.py, templates/finance_payments.html,
templates/finance.html and finance_snapshot.html (controller).
- [x] Add/run tests for dates, finite positive Decimal amounts, same-currency accounts,
  card debt/funding liquid validation, status changes, overdue totals and rollback.
- [x] Implement finance_payments.initialize(conn), save(conn, payment_id=None, card_id,
  funding_id, amount, payment_date, status), view(conn,now=None). Save returns integer id;
  view returns payments list, upcoming_totals and scheduled_totals (currency:Decimal).
- [x] Integrate initialization and frozen snapshots; add MFA+CSRF POST /finance/payments
  and manual schedule form/table. Never invoke bank payment APIs or infer dates.
- [x] Verify route security, frozen schedule persistence and full regression.


## Completion evidence

Implemented in the isolated `codex/finance-snapshots` branch from main `67b5905`.
Final full suite: **499 passed**, two existing Google authentication Python 3.9
end-of-life warnings. Baseline was 451 passed. No new dependencies were needed.

Independent review covered metadata/migrations/snapshots, then the user-added payment
schedule. Fixed unavailable totals being treated as zero, omitted previous incomplete
coverage, and payment-status edits blocked by account regrouping. Regression tests also
cover manual data exclusion from bank-only daily history, atomic multi-field edits,
MFA/CSRF on all finance mutations, frozen payment schedules, and the reserved ungrouped
inbox. The payment data implementer hit a usage cap; the controller completed, reviewed
and verified the persisted code rather than assuming its unfinished report was success.

Browser runtime reported no available browsers; visual QA remains unverified. Templates
and integration behaviors are tested. No real balances, account names, tokens, payment
instructions or credentials were used in fixtures. No deployment/merge was performed.
The separate UI enhancement worktree was not modified. Upgrade and rollback instructions
are in docs/runbooks/finance.md; no new token or MFA enrollment is required.
