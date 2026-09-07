# Finance connections implementation plan

> Execute task-by-task with Superpowers subagent-driven development and TDD.

**Goal:** Add private, read-only SimpleFIN balances and history to Home HQ, with
recent MFA and safe operator setup. Implementation authorized September 7, 2026.

**Architecture:** A scheduled CLI fetches balances into a separate SQLite DB.
The Flask app reads that DB and never reads provider credentials. A shared MFA
module gates Finance while leaving ordinary household pages password-only.

**Spec:** The review decisions in the owner's private
`docs/plans/private/2026-09-05-simplefin-security-prereqs.md`, September 7 review.
This public plan contains implementation contracts, not server vulnerability status.

## Global Constraints

- Balances only; no transactions, payments, AI requests, FX, or documents vault.
- Finance is disabled unless HOMEHQ_FINANCE_ENABLED=true; enabling requires an
  absolute HOMEHQ_FINANCE_DB_PATH outside every Git working tree ancestor.
- Finance pages require login and MFA within 900 seconds. No balance information
  appears on Home, public pages, or in browser cookies.
- Exact decimal strings; per-currency connected-account totals, never a claimed
  complete household net worth. Preserve provider signs and show stale data.
- Provider credentials only in a separate 0600 sync environment file; no raw
  credential URL, response body, transaction, or provider exception in logs/UI.
- HTTPS, explicit Bridge hostname allowlist, TLS verification, no redirects,
  no ambient netrc/proxy auth, bounded network time and response size.
- Existing household routes keep functioning. Isolated worktree starts at 748d4b6.
- Tests: PYTHONPATH=/private/tmp/homehq-finance-deps:. /Users/stephenhughes/Projects/meal-planning/.venv/bin/python -m pytest
  Local interpreter is 3.9.12; production requires a maintained Python version.
  Deterministic tests use temporary databases and fake HTTP transport, never real credentials.

## Task 1: Provider, balance store, setup and sync tools

**Files:** create `simplefin.py`, `finance_store.py`, `scripts/setup_simplefin.py`,
`scripts/sync_finance.py`, `tests/test_simplefin.py`, `tests/test_finance_store.py`,
`tests/test_finance_cli.py`; add `requests==2.32.5` to requirements.txt. No app/template edits.

**Interfaces:** `simplefin.fetch_balances(access_url, session=None)` returns a
validated normalized dict with accounts and sanitized warnings. Request
`version=2&balances-only=1`. `simplefin.claim_token(token, session=None)` returns
a validated access URL for private file output only. `SimpleFINError` has only
safe fixed messages. `finance_store.connect(path, repo_dir=None)` returns a
Row-factory connection and initializes tables; `record_sync(conn,payload,now=None)`
transactionally ingests normalized data; `record_failure(conn,now=None)` saves a
generic failure state; `dashboard(conn,now=None)` returns `accounts`, `totals`,
`history`, `last_attempt`, `last_success`, `warnings`. Document exact nested keys
in module docstrings and task report so Task 3 consumes them correctly.

- [x] Write failing tests for HTTPS/host validation, redirect rejection, safe
  402/403/network errors, v2 connection-scoped account identity, legacy org
  compatibility, decimals, no persisted transactions, stale/omitted accounts,
  partial errors, duplicate daily snapshots, and private file modes.
  Example independent expectation: balances 100.10 and -20.05 in USD yield
  Decimal('80.05'); an EUR 7.00 balance must never enter that sum.
- [x] Run the new tests and record the missing behavior before implementation.
- [x] Implement normalized accounts with SHA256 connection+account identity,
  opaque stable display labels (not presumed account-number suffixes), currency,
  decimal balance, provider balance timestamp. Never retain provider names or extras.
  Preserve v2 `conn_id`; legacy identity includes org identifiers; reject ambiguous
  duplicate identities. Sanitize warnings to fixed categories, including unknown errors.
- [x] Use allowlisted `bridge.simplefin.org` and `beta-bridge.simplefin.org` only,
  no other providers in v1. Validate claim path `/simplefin/claim/...`; access URL
  requires user+password, nonempty safe path, no fragment/query/control characters,
  standard HTTPS port only. Decode base64 strictly; limit input/response sizes.
  Extract Basic Auth from URL before requests; default requests.Session uses
  trust_env=False, allow_redirects=False, timeout=(5,30), streaming size cap 2 MiB.
  Close responses/sessions. Never expose raw response errors.
- [x] Keep exact finite Decimal values (reject NaN/Inf, unreasonably large values
  and malformed timestamp/currency fields). Unknown/custom currencies get a
  nonfinancial display bucket, no arbitrary URL fetch. Support optional local
  alias JSON via HOMEHQ_FINANCE_ALIASES_FILE mapping hashed IDs to safe labels.
- [x] Store current accounts, UTC daily snapshots, and sync status in private
  SQLite storage (DB 0600, dedicated new parent 0700; reject symlinks and repo paths).
  Omitted accounts remain marked missing. Do not backdate observations. Partial
  results update available accounts but mark aggregate/history incomplete; retain
  last complete success. Do not replace fresher provider balances with older ones.
  Stale threshold 48 hours, explicit flags. Same UTC date upserts snapshots.
- [x] Implement hidden-input setup CLI `--output FILE`: exclusively reserve a
  0600 output outside repo before claiming once; write HOMEHQ_SIMPLEFIN_ACCESS_URL
  without printing secret; safe errors and no overwrite. Do not load .env automatically.
  Implement sync CLI `--demo` deterministic fixture / otherwise env credential,
  required HOMEHQ_FINANCE_DB_PATH, exclusive nonblocking file lock, safe status exit
  codes, and no live calls for demo. Demo refuses to overwrite an existing live store.
- [x] Run focused tests, then existing suite; record evidence and self-review.

## Task 2: Scoped MFA, login CSRF, enrollment and session lifecycle

**Files:** create `mfa_security.py`, `templates/mfa.html`,
`scripts/enroll_totp.py`, `tests/test_mfa.py`; modify `app.py`,
`templates/login.html`, `tests/conftest.py`, requirements.txt (`pyotp==2.9.0`).

**Interfaces:** `mfa_security.init_app(app,get_db,users)` registers routes and
hooks; `require_recent_mfa` decorator gates future Finance routes using current_user.
`get_db` is the existing pantry DB accessor. Recovery hashes, last-used TOTP time
step, rate limits, and enrollment version belong in new tables in that DB.
Per-user secrets come from HOMEHQ_USER1_TOTP_SECRET / USER2_TOTP_SECRET, matched
to HOMEHQ_USER1_NAME / USER2_NAME. Store no plaintext secrets/codes in cookies/DB.

- [x] Write failing behavioral tests: pantry remains accessible; protected dummy
  route challenges; correct/incorrect/previous-window TOTP; 900-second expiry;
  replay across independent sessions; single-use recovery; per-user throttling;
  missing enrollment fails closed; login/logout clears grants; external next URL
  rejected; missing/invalid CSRF rejected on login and MFA POST.
- [x] Run those tests before writing production code.
- [x] Use PyOTP with 30-second steps and ±1 window, atomically record a successful
  unused step under a SQLite immediate transaction. Throttle both TOTP and recovery
  failures per authenticated user (5 failures => 5-minute lock), persist across
  sessions. Recovery hashes use SHA256 of cryptographically random high-entropy
  codes, atomically consumed; enrollment stores a secret fingerprint to invalidate
  old codes/grants when rotated. A code verification and grant creation cannot race
  past replay protection. Mask codes in logs and return fixed errors.
- [x] MFA grant includes current username, verification time and credential
  fingerprint, never the actual secret. Check Flask-Login freshness before accepting
  the grant; changed browser identity must not preserve recent MFA. Clear grants
  on login/logout, including login as the other household member. Prevent future
  timestamps from granting access. Return only to a validated local path (default
  /finance), never follow user-provided external/protocol-relative destinations.
- [x] Protect login and MFA POST using random session synchronizer CSRF tokens,
  constant-time verification and hidden fields. Expose csrf_token() in Jinja.
  Existing tests may use a test-client helper that fetches real login CSRF; new
  security tests use a raw app.test_client() to exercise missing/invalid tokens.
  Do not disable security under TESTING. Existing ordinary forms remain outside
  this scoped change. Initialize app hooks without requiring finance configuration.
- [x] MFA template: labeled six-digit field with autocomplete=one-time-code,
  alternative recovery input, accessible errors and clear return action. No
  external assets on MFA/Finance: add a base.html head block if needed (coordinate
  with Task 3); system fonts suffice. no-store and frame/referrer headers on MFA.
- [x] Enrollment CLI `--slot 1|2 --username NAME --db PATH --output-dir DIR`
  creates a new private directory outside git, writes totp.env, provisioning.txt
  (URI plus manual setup secret), recovery-codes.txt (10 codes), all 0600. No
  secret printed to stdout; no external QR generation. Register only fingerprint
  and recovery hashes in DB. Existing output directory is refused. Rotation is
  explicit via the same administrative CLI with a fresh output directory; require
  confirmation before replacing existing enrollment. No live enrollment this session.
- [x] Run focused and regression tests; report exact init/decorator/CLI interfaces.

## Task 3: Finance UI and deployment/backup integration

**Files:** create `finance_routes.py`, `templates/finance.html`,
`static/css/finance.css`, `tests/test_finance_routes.py`,
`deploy/homehq-finance-sync.service`, `deploy/homehq-finance-sync.timer`,
`docs/runbooks/finance.md`, `docs/runbooks/rotate-secrets.md`;
modify `app.py`, `base.html`, `homehq.service`, `.env.example`, `.gitignore`,
`scripts/backup.py`, backup service, `tests/test_backup.py`, server-hardening runbook.

**Interfaces:** `finance_routes.init_app(app)` registers GET /finance, consuming
Task 1 dashboard and Task 2 require_recent_mfa. app config gets env enable/path
without reading the access URL. No provider import/call from the request handler.

- [x] Write failing route tests for disabled/no login/no MFA, complete and partial
  data, missing configured DB, stale data, currency separation, negative balances,
  no external assets/secrets, correct no-store headers, and no fetch during GET.
  Write backup tests for two DBs retained independently, snapshot failure cleanup,
  private files, and missing source failure instead of silent empty DB creation.
- [x] Run new tests to establish failures, then implement.
- [x] Finance page uses existing warm visual identity with clear account rows,
  last successful refresh, warning/stale labels, per-currency totals, and a small
  accessible history chart with equivalent table. Show only complete daily totals
  as valid trend points; gaps/partial history remain labeled rather than invented.
  Show opaque labels unless operator alias exists. Zero balances are real data;
  empty/not configured is distinct. No cross-currency net-worth headline.
- [x] Add a Finance nav link only when enabled; no amounts on Home/nav. Finance
  and MFA HTML must use self-hosted assets only. Set no-store, nosniff,
  Referrer-Policy: same-origin and frame protection. Validate configuration outside
  git; missing store shows setup-needed state after MFA rather than creating it.
- [x] Add systemd daily sync (06:00 UTC, randomized delay, persistent), restrictive
  UMask=0077, credentials from `.homehq-finance.env` only in sync unit, finance
  DB path in main config. Main app optionally loads private per-slot MFA files,
  never the finance credential file. No production changes executed automatically.
- [x] Extend backups for optional finance DB: independent retention per stem,
  private snapshots and ciphertext, cleanup even on snapshot/encryption failure,
  source existence/read-only checks; preserve CLI backward compatibility. Main
  backup service only needs main config+backup env, not provider credentials.
- [x] Document signup, verified pricing link (avoid baking a purchase assumption),
  account selection, one-time hidden token claim, per-user MFA manual enrollment,
  private file installation, sync preview/status, liability-sign and currency checks,
  off-box encrypted pull/restore drill, deployment/rollback and token rotation.
  No credentials in command arguments, shell history, docs, screenshots or chat.
  Correct SSH guidance using official AWS browser-SSH firewall constraints.
- [x] Run full suite once, review complete branch, and record rollout items that
  require owner access. Do not claim live deployment/provider verification.

## Review and rollout

Each task receives independent spec/quality review; final review covers the whole
branch. Live credentials and server mutations wait for owner-side setup and verified
prerequisites. Keep the branch/worktree available for review and integration with
the separate UI branch; do not merge or deploy without the final concrete handoff.

## Completion evidence — September 7, 2026

Implemented on `codex/finance-connections` in `.claude/worktrees/finance-connections`.
Final local suite: **434 passed**, with two pre-existing Google authentication
warnings about Python 3.9 end of life. Requests 2.32.5 and PyOTP 2.9.0 were used.
The deployment interpreter still needs validation on the server.

Independent provider/operations and final MFA/UI reviews completed. Fixed findings:
local-config mutation in a test (now isolated under a temporary directory), exact
Decimal aggregation, credential rotation output path, and three-decimal UI display.
Additional tests cover omitted accounts marking history incomplete, other Git
ancestor rejection, sanitized encoding exceptions, and actual Finance/MFA integration.
A real GPG encrypt/decrypt round trip with synthetic SQLite data passed integrity
and value checks. This is not a restore test of the owner's production backups.

Browser runtime reported no connected browsers, so desktop/mobile visual inspection
remains an explicit follow-up. Page rendering and security behavior are tested.
No real provider credentials, enrollment, bank connections, server deployment,
branch merge, or off-box production backup changes were performed.

Operator handoff: [Finance setup and recovery](../runbooks/finance.md).
Keep this worktree isolated until it is reviewed alongside the separate UI branch.
