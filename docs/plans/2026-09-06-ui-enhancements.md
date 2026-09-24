# Home HQ: UI enhancement handoff

Source-based review, September 6, 2026. Reviewed Flask routes, Jinja templates,
CSS, recipe storage, shopping interactions, and the existing meal-planning plan.
No connected browser was available; visual proposals need desktop/mobile review.
These are proposed tasks, not implemented changes. The research addendum below
updates the priorities and distinguishes verified issues from design hypotheses.

## Direction and constraints

Keep the warm cream, ink, and deep green identity. Make it feel like a useful
household app with a little ledger character. The current 780px shell, single
large card, pervasive uppercase monospace, and long scrolling tab strip give
unrelated content similar weight. Reduce that repetition and make daily actions
immediately reachable.

Stay with Flask/Jinja and progressive enhancement. Keep recipes in markdown and
mutable inventory in SQLite. Preserve authentication and import approval. Follow
the existing planning decisions: fresh ingredients always default onto proposed
shopping lists; inventory matches do not establish sufficient quantities; no unit
conversion or deficit arithmetic. A calendar is explicitly deferred in the
existing plan, so do not introduce one as part of this refresh.

## 1. Rebuild the shared shell and visual hierarchy — medium

Evidence: `templates/base.html` places eight fixed destinations plus every catalog
category in one horizontally scrolling strip. `static/css/style.css` applies
small uppercase monospace to navigation, buttons, metadata, and labels.

Implement:

- Desktop: a compact navigation rail grouped into Food and Household, with catalog
  categories nested under Catalog. Mobile: Home, Inventory, Recipes, Shopping,
  and More; Inventory exposes Pantry/Freezer without merging their data models.
- Put Capture, Import/review, Catalog, and Report in the secondary navigation.
  Move logout into one shared account control.
- Use IBM Plex Sans for controls and body text; reserve Special Elite for the
  wordmark and monospace for occasional quantities. Use sentence case labels.
- Use a light neutral page background, cream content surfaces, and green primary
  buttons. Allow wide list/dashboard layouts around 1100px, while keeping recipe
  prose at a comfortable reading width.
- Define shared spacing, typography, surface, focus, and status tokens. Correct
  the global `color-scheme: dark` so native controls match their light surfaces.

Acceptance: all existing destinations remain reachable; the active section is
clear with `aria-current`; no page-level horizontal overflow at 390px; primary
controls have at least 44px touch targets; navigation works with keyboard only.
Verify text contrast, safe-area padding, and long recipe titles.

Files: `templates/base.html`, `static/css/style.css`, page headers across templates.

## 2. Make inventory faster to operate — medium

Evidence: alerts enumerate items above the inventory, unsorted items appear again
in a separate form, and the eight-field add form sits below all rows. Quantity
changes redirect without preserving `q`.

Implement:

- Put Search and Add item at the top. Add opens a compact form with name, quantity,
  unit, and section; disclose location and date settings under More details.
- Replace long alert lists with summary controls: All, Past date, Due soon, Unsorted.
  Selecting a control filters the rows. Keep estimated dates explicitly marked.
- Align name/details and quantity controls consistently; use compact stacked rows
  on phones. Make section groups collapsible and move bulk sorting into its own mode.
- Preserve search/filter and item position after quantity changes and edits. Start
  with safe query parameters and row anchors; optionally enhance updates in place.
- Distinguish an empty inventory from no matching results, with Clear filters.

Acceptance: search for rice, adjust a quantity, and remain on filtered rice results;
  add an item without scrolling past the inventory; failures are visible and retain
  entered values; estimated dates never become unqualified expiry claims.

Files: `templates/inventory.html`, inventory routes in `app.py`, shared CSS/JS.

## 3. Turn Recipes into a decision screen — medium

Evidence: recipes have category/effort filters but no text search. Metadata is a
single inline string, and zero filter matches use the same message as an empty library.

Implement:

- Search by recipe name and ingredient; combine it with existing filters.
- Start with a structured list: title first, category second, then aligned effort,
  total time, and servings when recorded. Compare a compact two-column collection
  on desktop before choosing it; photos remain optional. A grid is a design
  hypothesis, not a requirement or an evidence-backed universal improvement.
- Offer quick filters for Favorites and low effort, plus expandable advanced filters.
  Clearly identify unknown effort rather than presenting it as known low effort.
- Show matching count versus total, active filters, Clear filters, and a proper
  no-results state. Put Add recipe at the top with Paste and Manual choices.

Acceptance: name and ingredient queries work together with category/favorites;
  clearing filters restores all recipes; missing time/effort produces no invented
  values; long names do not collide with metadata.

Files: `templates/recipes.html`, `recipe_store.py`, recipe route in `app.py`.

## 4. Add a focused cooking view — medium

Evidence: `recipe_detail.html` interleaves ingredients/method with editing links,
and potentially many metadata fields share one small paragraph. Existing scaling
and list/table views provide a useful starting point.

Implement:

- Show servings, effort, and recorded time prominently; collapse secondary metadata.
- Desktop: ingredients beside the method. Mobile: one column with clear section jumps.
- Add Cook mode with larger numbered steps, temporary ingredient/step checkmarks,
  and a persistent servings control. Group editing links into an Edit menu.
- Preserve both servings and list/table selection when changing either control.
  Keep the engineering table as an optional view.
- Add clean recipe printing that excludes controls and navigation.

Acceptance: changing portions retains the selected method view; cooking checkmarks
  never mutate inventory; recipes without structured steps remain readable; print
  preview contains ingredients and method without edit buttons.

Files: `templates/recipe_detail.html`, shared CSS and progressive JS.

## 5. Separate shopping from putting groceries away — larger

Evidence: Check off currently opens inventory matching/new-item forms. The user
must reconcile storage while working through the shopping list; every item has
only pantry/freezer storage choices, despite fresh foods being deliberately untracked.

Implement:

- Add a persisted purchased state with a large checkbox, progress count, and a
  collapsed Purchased section. Checking/unchecking should be reversible.
- Add Finish shopping to reconcile purchased shelf-stable/frozen items using the
  existing matching flow. Fresh/untracked purchases can complete without creating
  inventory records; represent this explicitly rather than forcing pantry storage.
- Keep quick add near the top and support textual amount notes alongside the
  existing numeric quantity. Show source recipe where available.

Acceptance: purchased state survives refresh; checking a box alone never increments
  inventory; finishing twice cannot double-increment; fresh items can complete
  without pantry entries; a suggested match remains subject to user review.

Files: `templates/shopping_list.html`, shopping routes in `app.py`, `db.py`.
Requires migration and state-transition tests; implement separately from the shell.

## 6. Give Home useful household status — medium

Evidence: `home.html` is primarily destination tiles; its food section shows only
the recipe count, while catalog counts/value receive more detailed treatment.

Implement a short action dashboard: shopping items remaining, items approaching
their recorded/estimated dates, pending import reviews, and a few favorite recipes.
Each summary links to the corresponding filtered view. Put Add groceries and Open
shopping list near the top. Keep catalog totals in a quieter Household section.

Acceptance: counts agree with destination pages; zero states suggest a useful action;
  long attention lists are summarized; no placeholder or invented activity appears.

Files: `templates/home.html`, home route in `app.py`, existing DB/expiry helpers.

## 7. Connect a recipe to shopping before building the AI planner — medium

This is already outlined in Phase 2 of `2026-09-05-recipes-and-meal-planning.md`,
but is not present in the reviewed recipe page. It is the strongest functional
addition: choose dinner, review ingredients, add selected items to the list.

Implement the existing four-bucket design: Fresh, On hand, Check, Missing. Preserve
recipe amounts as text, show matched pantry/freezer rows, and let the user decide
whether there is enough. Add only reviewed selections and honor the chosen servings.
The older plan's later references to three buckets should not override its explicit
fresh-food rule. Prevent accidental duplicate submission.

Acceptance: fresh always defaults selected; a fuzzy name match never claims enough
  stock; no computed deficits; scaled amounts carry into list notes; only selected
  ingredients are added. Test these domain rules and duplicate submissions.

Files: new `pantry_check.py`, `app.py`, `db.py`, recipe/shopping templates.

## Execution and verification

Ship 1–3 first for the largest immediate improvement, then 4. Build 5 and 7 as
separate behavioral changes, and finish 6 once its filtered destinations exist.
The research addendum identifies foundational fixes to incorporate before or
alongside 1–3, and an editor improvement that should precede the larger features.
Update existing tests when markup changes; add meaningful tests for new behavior.
Review Home, a long pantry list, filtered recipes, a long recipe, and shopping on
390px and 1440px screens. Include empty, no-results, validation error, and pending
states. Capture before/after screenshots when a browser is available.

Reconcile the older recipe plan with current code before using it as an execution
spec: it still says “not started” although recipe storage, editing, extraction,
scaling, and structured steps now exist. Its git-staging description is also stale:
`catalog_writer.git_commit_and_push` currently stages `content` and `photos`, not
all repository files. Decide explicitly how future recipe persistence integrates
with git rather than assuming the catalog helper already handles it.

## Research addendum: additional feedback

Prepared September 6, 2026 for the owner and the executing agent. Scope: usability,
visual hierarchy, accessibility, and error recovery in the current household app.
Assumption: phone and desktop are equally important until the owner says otherwise.
Superpowers brainstorming was used to compare approaches; Deep research was used
to check primary guidance and counterevidence. No dedicated UI Superpowers skill
was found among installed skills. This is advisory work, not an approved redesign.

### Choose a direction before changing every screen

| Direction | What changes | Tradeoff |
| --- | --- | --- |
| Workflow-focused household app — recommended | Keep the warm identity; simplify navigation, editing, shopping, and recovery | More work than CSS polish, but addresses specific friction in current code |
| Light ledger refresh | Typography, contrast, spacing, row alignment, clearer buttons | Fastest visible change; leaves import and editing complexity intact |
| Recipe-first kitchen app | Make meal selection/cooking the home experience; use optional food photography | Stronger food identity; reduces prominence of the broader household catalog |

Recommendation is an inference from this project, not a research finding about
this household. Use the first direction with the second direction's visual restraint.
Do not require recipe photography, a new framework, or a full dashboard to make
the first release feel substantially better.

Keep frequent destinations visible. NN/G's 2016 study involved 179 UK participants
and six public websites; its exact effect sizes should not be projected onto a
familiar two-person household app. A 2025 follow-up found better menu-icon
recognition but did not remeasure task success/time. The practical recommendation
is visible daily actions plus a labeled secondary menu, with contextual Add
groceries links even if Capture moves out of the main navigation.
[NN/G study methodology, June 26, 2016](https://www.nngroup.com/articles/hidden-navigation-methodology/);
[NN/G menu recognizability, Kate Kaplan, June 13, 2025](https://www.nngroup.com/articles/hamburger-menu-icon-recognizability/).

### 8. Replace recipe syntax entry with ordinary controls — high priority, medium/large

**Observed:** `recipe_ingredients.html` requires `name | quantity | unit | flags`;
`recipe_steps.html` requires `id | action | inputs`, exact ingredient names, and
references to earlier step IDs. This makes the storage representation part of the
normal editing experience.

**Task:** provide ingredient rows with Name, Amount, Unit, Fresh, and Staple controls;
provide numbered method steps with action text and an ingredient/previous-step
picker. Keep raw text under Advanced. Use Add row, Remove, Move up, and Move down
buttons; drag gestures can be optional. Explain Fresh/Staple in plain language.
The normal UI should generate stable step IDs and preserve valid references.

**Acceptance:** edit an amount and reorder steps without typing delimiters or IDs;
block an invalid dependency with a correction message; switching text/form modes
does not silently lose content; save/reload preserves ingredient flags and valid
step references. The engineering table still renders. Test round trips and
dependency errors, including an attempted move before a step's prerequisite.

**Files:** ingredient/step templates, corresponding routes, serializers in
`recipe_writer.py`, validation/rendering in `recipe_steps.py`.
This applies the principle of showing selectable information instead of requiring
recall; the specific editor design is our proposal.
[NN/G usability heuristics, Jakob Nielsen](https://www.nngroup.com/articles/ten-usability-heuristics/).

### 9. Fix amber readability and shared interaction semantics — high priority, small/medium

**Calculated from CSS:** `#c98a2c` on `#f7f1e1` is approximately **2.60:1**;
on `#efe6cc`, **2.36:1**. The `.tag-soon` 8% amber background composited over cream
gives approximately **2.43:1**. These are token calculations, not browser measurements.
They are below the 4.5:1 requirement for ordinary text. Darken status text; retain
amber as a restrained background/accent. Muted ink on cream calculates to 5.43:1,
so do not replace every muted color indiscriminately.
[W3C: Contrast (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).

**Observed:** the shared edit toggle changes `hidden` without exposing expanded
state. Cancel can hide the panel containing the focused button. Add synchronized
`aria-expanded`, panel references, and return focus to the opener when closing
from inside a panel. Add navigation/main landmarks, a skip link, and an explicit
accessible label for inventory search. Prefer native disclosure where appropriate.
[W3C: Disclosure pattern](https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/).

**Acceptance:** all small text meets 4.5:1 against its actual background; expanded
states are announced; keyboard users can open, edit, cancel, and continue without
losing focus. Check reflow at **320 CSS px**, plus 200% text enlargement. Keep
necessary two-dimensional recipe tables in their own scroll area; ordinary content
must reflow. Sticky mobile controls must not cover focused fields.
[W3C: Reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html).

Keep 44×44 CSS px as the project's comfortable touch-target goal. It corresponds
to the enhanced AAA target-size criterion, with exceptions. WCAG 2.2 AA's minimum
is 24×24 CSS px or qualifying spacing/exceptions. A 36px button is not automatically
an AA failure. Measure actual targets before making conformance claims.
[W3C: Minimum targets](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html);
[W3C: Enhanced targets](https://www.w3.org/WAI/WCAG22/Understanding/target-size-enhanced.html).

**Files:** shared CSS, `base.html`, search and disclosure controls across templates.

### 10. Make extraction progress and partial failure visible — high priority, medium

**Observed:** `import_upload` collects per-photo failures but only displays them
when no rows were staged. If one image succeeds and another fails, redirecting to
review drops the failure messages. Capture/upload/paste templates also have no
explicit pending state. This is a source-confirmed control-flow issue; it was not
reproduced with a live AI request.

**Task:** show Reading photos or Preparing recipe after submission, prevent repeat
submission, and announce completion/error. Do not show a made-up percentage. Carry
batch results through the redirect: “8 items ready to review; 1 photo could not be
read.” Identify the failed file and offer retry of that file only. Preserve
successful staged items; use server-side request/batch identity for retry safety.

**Acceptance:** mock one extraction success and one failure; review shows both
outcomes. Retrying the failed file never duplicates the successful batch. Pending
buttons recover after errors. Show Saved only after persistence succeeds. Dynamic
status messages use appropriate live status semantics; do not announce every
keystroke or apply alerts indiscriminately to full-page navigations.
[W3C: Status Messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html).

**Files:** `app.py`, capture/upload/paste/review templates, batch persistence if needed.

### 11. Put source evidence beside AI review — medium priority, medium

**Observed:** staged photo rows store `source_image_path`, but `import_review.html`
does not display the source photo. A user correcting an extracted quantity cannot
compare it with the original within that review screen.

**Task:** group review rows by source photo; show a thumbnail with an accessible
enlarge control. Desktop can show image and editable rows beside each other;
mobile can show a source preview above that image's rows. Display matched inventory
name, quantity, unit, and storage before approving a merge. Keep AI-generated
values visibly provisional without inventing confidence scores. Provide Save and
review next plus a remaining count; never auto-approve untouched items.

**Acceptance:** review two photos and distinguish their rows; correct an amount
while seeing its source; reach the next pending item after approval. Serve previews
through an authenticated endpoint using a server-owned identifier, not an arbitrary
filesystem path. Text captures must have an appropriate no-photo presentation.
This is a project-specific application of recognition and error-prevention guidance.
[NN/G usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/).

**Files:** `import_review.html`, protected preview route in `app.py`, staging queries.

### 12. Give adding things a coherent entry point — medium priority, small/medium

**Observed:** Capture, Import, manual inventory entry, recipe paste, and manual
recipe entry are separate destinations. Several pages explain where to go when
the user chose the wrong input route; “Read it” does not describe the result.

**Task:** introduce a shared Add chooser organized by intent: Groceries, Recipe,
Household item. Within the chosen intent offer relevant methods such as Type,
Photo, and Paste. Contextual buttons should skip irrelevant choices: Add recipe
opens recipe methods directly. Reuse existing backends and review requirements.
Use labels such as Preview groceries, Prepare recipe, and Review extracted items.
Keep optional help and infrequent settings disclosed, but required inputs and
review consequences visible.
[NN/G: Progressive Disclosure, Jakob Nielsen, December 3, 2006](https://www.nngroup.com/articles/progressive-disclosure/);
[GOV.UK: Details](https://design-system.service.gov.uk/components/details/).

**Acceptance:** from Pantry, start typing groceries or uploading a receipt without
understanding the term Capture; from Recipes, paste a recipe directly; each route
shows its destination and next action. This does not require a universal AI router.

**Files:** `base.html`, entry-page templates, contextual Add controls.

### 13. Make mistakes cheap to recover from — medium priority, medium

**Observed:** inventory deletion and shopping removal submit directly; catalog
deletion already has explicit confirmation. Generic error blocks lack linked
field-level recovery. Preserve the useful catalog confirmation.

**Task:** offer Undo for routine list removal/inventory deletion using a bounded
restore record or soft deletion. Do not implement undo by overwriting an old whole
inventory snapshot, which could erase another household member's changes. Give
validation failures an error summary linked to affected fields, matching inline
messages, and retained input. Reserve confirmation for consequential actions.
[NN/G usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/);
[GOV.UK: Error summary](https://design-system.service.gov.uk/components/error-summary/).

**Acceptance:** remove an item accidentally and restore its fields; repeat undo
does not duplicate it; a concurrent edit to another item survives. Keyboard focus
reaches the error summary and its links reach invalid fields. A long recipe draft
survives a validation error. Define the undo window and keep recovery reachable.

**Files:** relevant mutation routes, `db.py`, shared feedback/error components.

### Revised execution order and evidence limits

1. Incorporate 9 and the partial-failure fix from 10 into the foundation release.
2. Deliver shell/inventory/search improvements (1–3), with the coherent Add entry (12).
3. Prioritize ordinary recipe editors (8) and cooking view (4).
4. Improve review and recovery (11, 13), then larger shopping/recipe integration (5, 7).
5. Build Home summaries (6) once their useful destination views exist.

List versus card presentation should be compared with the same real recipes and
missing metadata. Search-visible NN/G guidance favors predictable lists for
scanning; the full Cards page could not be retrieved after one retry, so treat it
as supporting discovery evidence only. Do not infer that cards are always worse.
[NN/G: Cards, Page Laubheimer, November 6, 2016](https://www.nngroup.com/articles/cards-component/).

Suggested household walkthrough: find a familiar dinner; add a tin to Pantry;
change a quantity while filtered; correct a misread receipt; edit an ingredient;
recover an accidental shopping removal. Record completion, wrong turns, lost work,
and confusing labels before/after. Repeat tasks can benefit from learning, so do
not present faster times from two household users as statistically proven gains.

Research used repository inspection, CSS contrast calculations, W3C explanatory
standards documents, first-party NN/G research/guidance, and GOV.UK component
guidance. All links were consulted September 6, 2026. Publication dates are included
where verified; undated living guidance is identified by access date. W3C's
Understanding pages explain criteria rather than constitute a complete conformance
audit. GOV.UK patterns are transferable guidance, not requirements to adopt its look.
No connected browser or household usability session was available. Layout, focus,
touch targets, and error-flow acceptance checks remain for the executing agent.
Research stopped after the material recommendations had primary support or explicit
limitations and the navigation/list counterevidence was reconciled.
