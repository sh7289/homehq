# Budgeting Phase 3: planning — design

Parents: `2026-09-23-budgeting-design.md` and `2026-09-24-budgeting-phase2-design.md`.
Product source: `home_hq_budgeting_product_spec.md`, plus the adopted adversarial review.
The household asked for this phase to continue autonomously; **Ruling** marks each
choice made here.

## Scope

1. **Actual supersedes forecast:** match a bank transaction to an expected bill or
   payday (spec §5.5).
2. **Financing that is ending:** show commitments whose final payment is near
   (spec §2.2, Test 10).
3. **Fund goals:** a target amount and date for each fund, the remaining gap, and the
   monthly amount needed to close it (review Finding 4).
4. **Savings progress:** planned savings against money actually added to savings
   accounts (review Finding 2). This fills in the dashboard's "Savings progress"
   dimension.
5. **HSA measures:** spending split by what paid for it (review Finding 10).
6. **Bonus planner:** the spec §6.6 split as an editable proposal. Nothing is recorded
   until someone confirms each line (review bonus test).

Out of scope, and left for Phase 4 or later: weekly review, month close, reports,
baseline variance, the §6.7 fence-reallocation scenario.

## 1. Matching actuals to expectations

New table `finance_recurring_matches`:
`recurring_id, occurrence_date, txn_id UNIQUE, created_by, created_at`, with
`UNIQUE (recurring_id, occurrence_date)`.

- **Candidates** for a transaction are occurrences of active commitments where:
  - the account is the same
  - the direction matches the sign (money in for `in`, money out for `out`)
  - the occurrence falls within 7 days of the transaction date
  - the amount matches: exactly for `exact` amounts, within ±25% for estimates,
    variable amounts and placeholders
  - the occurrence isn't already matched.
- **Matching is always a person's choice, never automatic** (**Ruling**: suggestions
  only, like categories). A due date alone never counts as paid.
- **Forecast:** a matched occurrence is removed from both the projection and the
  past-due list. The checking balance already includes the actual transaction, so
  it isn't subtracted again.
- **Unmatching** restores the occurrence.
- **Bills & paydays** shows each commitment's latest matched actual (date and amount)
  next to the expected amount.

## 2. Ending financing

The Forecast page gets an "Ending soon" list: active commitments with an `end_date`
within the next 90 days, plus ones that have already passed their end date but are
still marked active (these prompt the household to mark them *ended*). Payoffs never
change any target automatically (spec §2.2).

## 3. Fund goals

New table `finance_fund_goals`:
`category_id PRIMARY KEY, target_amount NULL, target_date NULL, expected_reimbursements (default '0'), note, updated_by, updated_at`.

```
gap            = max(0, target_amount − balance − expected_reimbursements)
months_left    = max(1, months from this month to the target month, counting the target month)
needed_monthly = gap / months_left   (rounded up to the cent)
```

- **Status:**
  - *setup needed* if the fund has no opening
  - *needs a target* if there's no amount or date
  - *on track* if the gap is 0
  - *due* if the target date has passed and there's still a gap
  - *needs X a month* otherwise.
- **Seed version 4** adds goals **without amounts**, because the brief has dates but
  no totals:
  - Gifts and Christmas: target 2026-12-24, note "Includes $520 planned for Gloria/Peter
    gifts; not the whole Christmas budget".
  - Celebrations: target 2027-02-01, note "Steve's and Siena's February birthdays".
- **Ruling:** fund goals are shown only as proposals. The monthly amount needed is
  never credited to a fund, and never added to the operating plan automatically.

## 4. Savings progress

- **Planned:** the Savings category's target for the month. If there isn't one, the
  status is *unknown* ("Set a monthly savings goal").
- **Actual:** the net of posted transactions in the month on accounts whose budget
  role is `savings` or `reserve`: deposits, transfers in and interest, minus
  withdrawals. **Ruling:** interest counts as accumulation, and fund contributions do
  not, because they are allocations inside cash the household already has.
- **Coverage:** if a savings or reserve account lacks coverage for the whole month (or
  up to today, for the current month), the status is *unknown*.
- **Dimension:**
  - *good* when actual ≥ planned
  - *attention* when actual < planned: "Saved $X of $Y planned"
  - *unknown* when there's no target or coverage is missing.
- The dashboard also shows "Set aside in funds this month" separately, so allocations
  and savings are never mixed.

## 5. Spending by funding source

`month_summary` gains
`funding: {hsa: D, everyday: D, total: D}`: known spending grouped by the paying
account's budget role. `hsa` is the `hsa` role; `everyday` is everything else. The
dashboard shows "Total spending $T, of which $H was paid from the HSA". No savings-rate
percentage is shown (review Finding 10).

## 6. Bonus planner

`/finance/bonus`:

- **The amount** comes from a pick-list of posted income transactions from the last
  120 days, or is typed in.
- **Proposed split** (spec §6.6), with editable percentages that must total 100:

  | Share | Destination |
  |---:|---|
  | 35% | savings |
  | 25% | Travel |
  | 15% | Gifts and Christmas |
  | 10% | Celebrations |
  | 15% | Home/car/pet reserve |

- **Shown beside it:** reserve funding status, fund gaps, and savings progress, so the
  household can prioritize existing shortfalls first.
- **Nothing is saved when the page loads.** A line is recorded only if its box is
  ticked:
  - Fund lines become fund *contributions* dated today, noted "Bonus allocation".
  - The savings line records nothing. Moving money into savings is a bank transfer
    the household makes itself, and it shows up in savings progress once imported.
- A bonus is never forecast as income (spec §6.6).

## Acceptance tests

- Matching: the forecast excludes a matched occurrence, unmatching restores it, the
  tolerance for estimates works, and one transaction matches at most one occurrence.
- Review: "budget within limits but savings below plan" → the savings dimension shows
  attention while discretionary shows good.
- Review: "planned $300 savings contribution can't be funded" → nothing is recorded
  automatically, and the dimension shows the shortfall.
- Review bonus test: loading the planner writes nothing; only ticked lines create
  contributions.
- Fund goals: gap and monthly amount, setup-needed and needs-a-target states, a due
  target, and the seeded Christmas goal with no amount.
- HSA: HSA-funded spending is counted in the total and reported separately.
- Security gates on every new route.
