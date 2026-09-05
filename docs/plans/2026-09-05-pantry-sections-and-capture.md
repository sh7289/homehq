# Pantry sections + faster inventory capture

**Date:** 2026-09-05
**Status:** Draft — not started
**Goal:** Two related problems. The pantry and freezer are flat lists that get
harder to scan as they grow, and adding items one form at a time is slow
enough that inventory drifts out of date. Fix the first with a fixed set of
sections, and the second by letting you describe or photograph what you're
adding and having Claude turn it into staged rows you approve.

Decisions already made: fixed section list with the AI assigning one on
import (corrected in review); text and photo capture only — no audio or video
subsystem (see "Decided against").

---

## Phase 1 — Sections

**Goal:** group pantry and freezer lists into sub-areas without disturbing the
existing `location` field, which is a different thing (the physical shelf,
free text) and stays as-is.

Two separate lists, because the pantry taxonomy doesn't describe frozen food:

```python
# sections.py
PANTRY_SECTIONS = (
    ("bulk-dry",    "Bulk & Dry Goods"),   # beans, pasta, rice, grains
    ("canned",      "Canned Goods"),
    ("sauces-oils", "Sauces, Oils & Sweeteners"),  # incl. honey, vinegar
    ("baking",      "Baking"),             # sugars, flours, leaveners
    ("spices",      "Spices & Herbs"),
    ("other",       "Other"),
)
FREEZER_SECTIONS = (
    ("meat", "Meat & Fish"), ("vegetables", "Vegetables"),
    ("prepared", "Prepared & Leftovers"), ("other", "Other"),
)
```

Tasks:
1. `section TEXT` column on `pantry_items` via the guarded `ALTER TABLE`
   pattern already in `init_db`. Nullable; null renders as "Other" so the 106
   backfilled rows keep working untouched.
2. Same column on `import_staging_items`.
3. Group the inventory template by section, with counts and an
   expiring-soon marker per section. Keep the existing search working across
   all sections.
4. Section `<select>` on the add and edit forms.
5. A bulk "assign sections" screen to sort the existing backfilled rows
   quickly — a single page of rows each with a section dropdown. Without this
   the feature launches with everything in "Other" and stays there.

- **Files:** new `sections.py`; `db.py`, `app.py`, `templates/inventory.html`,
  `templates/import_review.html`
- **Tests first:** `test_section_column_added_to_existing_db`,
  `test_add_item_persists_section`,
  `test_items_without_section_group_as_other`,
  `test_inventory_page_groups_by_section`,
  `test_freezer_uses_freezer_section_list`,
  `test_bulk_assign_updates_multiple_sections`
- **Effort:** M

---

## Phase 2 — Capture by description

**Goal:** type or dictate "two cans of chickpeas, a bag of jasmine rice, half
a bottle of olive oil" and get staged rows to approve.

**This is also the voice feature.** iOS keyboard dictation types straight into
a normal textarea, so a plain text box gets speech capture with no STT
service, no audio upload, no audio at rest, and nothing new to secure. Build
the textarea; voice comes free.

Tasks:
1. `ai_extract.extract_from_text(text, ...)` alongside the existing image
   path, sharing the JSON parsing and `ExtractionError` handling.
2. Prompt returns `{"kind": "pantry_items", "items": [{name, quantity, unit,
   storage, section}]}`. Section constrained to the enum from Phase 1.
   Quantity may be null — "some rice left" is a legitimate thing to say, and
   guessing a number is worse than leaving it for the review screen.
3. `/capture` route: textarea → extraction → staging rows → existing review UI.
4. Reuse the existing `import_review.html` rather than building a second
   review screen.

- **Files:** `ai_extract.py`, `app.py`, new `templates/capture.html`,
  `templates/base.html` (nav)
- **Tests first:** `test_extract_from_text_returns_items`,
  `test_extract_from_text_assigns_valid_section`,
  `test_extract_from_text_allows_null_quantity`,
  `test_extract_from_text_rejects_invalid_section`,
  `test_capture_route_creates_staging_rows`,
  `test_capture_route_requires_login`
- **Effort:** M

---

## Phase 3 — Shelf photos

**Goal:** photograph a shelf, get its contents staged. Extends the pipeline
that already reads receipts.

Tasks:
1. Add a `shelf` kind to `_PROMPT`, same `items[]` shape as Phase 2 so the
   staging path is shared. The model already decides receipt vs catalog item;
   this is a third branch.
2. Tell the prompt explicitly that quantity is often unknowable from a photo
   and null is correct — otherwise it invents counts.
3. Allow several photos in one submission (one per shelf), staged together.

- **Files:** `ai_extract.py`, `app.py`, `templates/import_upload.html`
- **Tests first:** `test_shelf_photo_returns_pantry_items`,
  `test_shelf_photo_null_quantity_is_preserved`,
  `test_multiple_photos_stage_into_one_review_batch`
- **Verify:** real photos of your own shelves — accuracy on cluttered,
  partially-occluded shelves is the open risk and only real images will tell
  you. If it's poor, Phase 2 still stands on its own.
- **Effort:** M

---

## Phase 4 — Don't create duplicates on approve

**Goal:** capture will surface things you already have, constantly. Approving
"black beans" when black beans are already in the pantry should increment,
not duplicate.

Reuse `matching.find_best_match` (cutoff 0.72) exactly as the shopping list
check-off already does. On approve: match against current inventory in the
same storage; on a hit, show "increment existing (qty 3 → 5)" as the default
with "add as new" available; on a miss, create.

- **Files:** `app.py`, `templates/import_review.html`
- **Tests first:** `test_approve_increments_fuzzy_matched_existing_item`,
  `test_approve_creates_new_item_when_no_match`,
  `test_approve_respects_explicit_add_as_new`,
  `test_match_is_scoped_to_same_storage`
- **Effort:** S — most of this already exists in the shopping list flow.

---

## Other suggested updates (not scheduled)

- **Python version mismatch.** The local `.venv` is Python 3.9 (past EOL, and
  `google-auth` warns about it); the server runs 3.10. Tests pass on a
  different interpreter than production. Worth rebuilding the venv on 3.10+.
- **Unit normalisation is the real hard part.** "2 cans", "1 bag", "half a
  bottle" don't aggregate. Suggest keeping units free text and *not* trying to
  convert — but constrain the AI to a small vocabulary (can, jar, bag, box,
  bottle, lb, oz, g, kg, count) so at least the strings match each other.
- **Low-stock → shopping list.** Once sections exist, a per-item "low at N"
  threshold that auto-suggests the shopping list is a natural follow-on.
- **`pantry.html` / `freezer.html` share one template already** — worth
  confirming that stays true once sections land rather than forking them.
- **UI pass** — see below.

---

## UI refresh

You asked about Figma and plugins. Honest take: **Figma won't help here unless
you personally want to mock things up.** It's a design tool, not a code
generator; there's a Figma MCP server that lets Claude read designs, but it
only pays off if a designer is producing files, which isn't the situation.
Adding a component library (Tailwind, Bootstrap, shadcn) would actively fight
the hand-built "household ledger" aesthetic in `static/css/style.css` that
you liked.

What actually works for this project, and is already installed:
1. The `frontend-design` skill for the design pass itself.
2. The `browse` skill for screenshot-driven iteration — render a page at
   375px, look at it, adjust, repeat. That loop is what produced the current
   design.

Suggested scope for a general refresh, in value order: the inventory tables
at phone width (the densest screen and the one you use most, and Phase 1's
sections change its structure anyway — so **do the UI pass after Phase 1**,
not before), then the import/review screen, then the index page.

- **Effort:** M

---

## Decided against / deferred

- **Video capture.** The Anthropic API doesn't accept video; it would mean
  ffmpeg frame extraction plus batching frames as images. A pan across a shelf
  yields the same item in many frames, so the dedupe problem dominates and
  accuracy is worst exactly where volume is highest. Phase 3 plus Phase 4 gets
  most of the bulk-capture benefit for a fraction of the work. Revisit only if
  shelf photos prove accurate and the bottleneck is genuinely photo count.
- **Audio upload + speech-to-text.** The API doesn't accept audio, so this
  needs a third-party STT (Whisper or similar) — a new vendor, a new key, and
  voice recordings at rest. iOS dictation into the Phase 2 textarea gets the
  same outcome for free.
- **Free-form sections.** Rejected in favour of the fixed enum; spellings
  drift and grouping degrades.
- **Replacing `location`.** Sections and physical location are different
  axes (a spice can be in the door rack or the back shelf). Both stay.
- **A second review screen for text capture.** Reuse `import_review.html`.

---

## Open questions

1. Do the freezer sections above match how you actually think about the
   freezer, or would you rather it stay flat for now?
2. Should capture ever write straight to the pantry without review, for
   obviously-simple input like "add 2 cans of chickpeas"? Or always stage?
3. For the bulk section-assignment screen — worth building, or would you
   rather sort the 106 existing rows by hand as you touch them?
