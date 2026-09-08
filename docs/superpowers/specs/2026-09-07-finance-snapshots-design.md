# Household finance snapshots

Approved in conversation: familiar account identification, editable groups and ordering,
a compact spreadsheet-like layout, manual entries, and saved dated comparisons.
This feature extends the existing private finance store and MFA boundary. The separate
UI branch remains untouched. No production configuration or financial data changes.

Provider institution/account display names are sanitized (controls removed, long digit
sequences masked), stored privately, and shown as identification hints. Never retain
raw IDs, account-number fields, transactions, or credential URLs. User nicknames take
precedence and survive sync. Existing databases migrate additively during an operator
migration/sync; GET never creates or migrates a database.

Groups default to Cash & spending, Accessible investments, Retirement, Other illiquid
assets, Debts, and Needs grouping. Users can rename/add/reorder groups and assign their
liquidity bucket (liquid/illiquid/debt/unassigned). Account metadata includes nickname,
owner, group, order and whether it is included. Cash and investments retain separate
section subtotals even though both contribute to liquid totals. No automatic liability
sign inference: signed balances remain visible and debt rows need a confirmed sign
convention before a debt summary is computed. Currency totals remain separate.

Manual entries support name, currency, signed balance, and observation date. Both
manual and connected balances show source and freshness. Missing connected accounts
remain visible and excluded from totals. Excluded accounts stay editable. Coverage
shows missing, stale, unassigned, excluded and unconfirmed debt counts.

Save snapshot freezes account names, grouping, balances, provider/manual dates, flags,
and per-currency section totals. Snapshot creation is atomic, records actor and UTC
capture time, and never backdates bank data. History is immutable, may include multiple
captures per day, and compares currency/section totals to the previous saved snapshot.
Incomplete snapshots can be saved but are explicitly marked; changes in account scope
are flagged and differences are balance differences, not investment returns.

All finance GET/POST routes require login plus recent MFA. Every finance POST requires
session CSRF and uses POST-redirect-GET. Errors never expose raw data. Provider fetches
remain exclusive to sync CLI. Sensitive pages use private headers and local assets.
The web service can now write household metadata/snapshots to the private finance DB;
bank connections remain read-only. SQLite transactions serialize sync and local edits.

## Approved addition: upcoming credit-card payments

User requested next scheduled card payment dates to understand cash draws. SimpleFIN
has no standard payment-date/amount fields (protocol Account schema checked September7).
Add a manual schedule with card, funding account, positive amount, payment date and
planned/scheduled/paid/cancelled status. Scheduled means the user confirmed setup with
the issuer; this app never initiates payments or verifies settlement. Support one-off
entries and explicit edits; never invent recurring payments or guess autopay dates.

Card must be in a debt group, funding account in a liquid group, same monetary currency.
Show overdue active entries and totals for the next30days including overdue. Preserve
payment schedules inside frozen snapshots, clearly label user-entered source, and never
subtract these entries from account balances or net totals (which would double count
transfers once banks report them). All writes have existing MFA/CSRF gates.

The default Needs grouping inbox remains unassigned so new connections cannot silently
enter liquid/debt totals. Its name/order can change; move accounts out to classify them.
