# Recipe database + AI meal planning

**Date:** 2026-09-05
**Status:** Draft — not started
**Goal:** Store the meals, bakes and sauces we actually like as human-editable
markdown, with enough structure that the app can tell us which ingredients we
already have; then let a free-text prompt on the shopping list page ("a Mexican
chicken thing, a simple fish + 2 veg, something Italian — two of ours and one
new") produce a plan we review, approve, and turn into a shopping list.

One thing up front, because it shapes everything below. **The app cannot net
recipe quantities against inventory quantities.** Netting "2 cups jasmine rice"
against `{name: "jasmine rice", quantity: 1, unit: "bag"}` needs to know how many
cups are in your bag and how full it is, and it never will — 90 of the 107 pantry
rows today carry `quantity 1, unit ''`. So the app answers **"do you have a thing
called X?"** and asks you the rest. See "The netting problem".

---

## Where things live

**Recipes are markdown + frontmatter in a new top-level `recipes/` directory —
*not* under `content/`.** Markdown beats SQLite here: recipes are read-mostly,
you'll want to fix a method paragraph by hand, and git history on "what did we
change about the tinga" comes free. Ingredients still get structure — a YAML list
in the frontmatter, enough for name-matching without giving up hand-editing.

They can't live in `content/` for a concrete reason: `content_loader.load_catalog`
turns *every* subdirectory of `HOMEHQ_CONTENT_DIR` into a catalog category. Drop
`content/recipes/` in and 40 recipes silently become a nav tab, a section of
`/report`, rows in `catalog_stats`, and line items in the insurance CSV. So: a
sibling directory (`HOMEHQ_RECIPES_DIR`, alongside a `HOMEHQ_SKILLS_DIR` for
Phase 3) with its own loader mirroring `content_loader.py` / `catalog_store.py`
rather than reusing them. Both sit inside the git repo, so
`catalog_writer.git_commit_and_push` (`git add -A` at repo root) already commits
them — no new plumbing.

The **hybrid** part: a plan is not a recipe. Plans are mutable, short-lived and
need review state, so they go in SQLite as `meal_plans`, exactly the way
`import_staging_items` handles staged imports.

### Recipe frontmatter

```yaml
---
name: Chicken Tinga Tacos
kind: meal                # meal | baked | sauce | side | breakfast
cuisine: mexican
favorite: true            # false = suggested, not yet vouched for
effort: 2                 # 1-5, human-owned. See Phase 6.
effort_inferred: false
active_minutes: 30
total_minutes: 75
serves: 2
tags: [chicken, weeknight, spicy]
source: https://example.com/tinga
added: 2026-09-05
last_made: 2026-08-22
ingredients:
  - {name: chicken thighs, quantity: 1.5, unit: lb}
  - {name: chipotle chili powder, quantity: 2, unit: tsp, staple: true}
  - {name: corn tortillas, quantity: 12, unit: count}
---
Method, in markdown. Freeform, no schema.
```

`staple: true` is the most valuable field here — it's how you say "this is a
spice, we always have it, stop asking". Without it every plan asks about cumin
four times.

**Loader note:** `content_loader._stringify_scalars` coerces every scalar to a
string, so `quantity: 2` reads back `"2"`. Right for insurance frontmatter, wrong
here. The recipe loader keeps `quantity`, `effort`, `serves` and `*_minutes`
numeric — a deliberate divergence, worth a comment in the file saying so.

### `meal_plans` table

```sql
CREATE TABLE IF NOT EXISTS meal_plans (
    id INTEGER PRIMARY KEY,
    prompt TEXT NOT NULL,                      -- what you typed
    plan_json TEXT NOT NULL,                   -- validated response, verbatim
    status TEXT NOT NULL DEFAULT 'pending',    -- pending | approved | discarded
    created_at TEXT
);
```

Storing the raw response means the review page survives a refresh without
re-calling the API, and approve is idempotent.

---

## The netting problem

**What the app will know:** whether the pantry or freezer holds a row whose name
fuzzy-matches the ingredient, via `matching.find_best_match` at the existing 0.72
cutoff — and what that row literally says (`Cumin · 200 g`).
**What it will not know:** how much of it you have in the units the recipe wants.
Not roughly. At all.

Each ingredient lands in one of three buckets; the human resolves bucket 2:

| Bucket | Condition | Default | UI |
|---|---|---|---|
| On hand | `staple: true` **and** a name match | not listed | collapsed, one tap to add anyway |
| **Check** | name match, not a staple | not listed | shows the pantry row verbatim; *Have enough* / *Add to list* |
| Missing | no name match | listed, pre-checked | shows the recipe's own amount as the note |

**No computed deficits, no unit conversion, no "you need 0.5 more cups."** A
fabricated number is worse than no number: it looks authoritative, it's wrong, and
the failure mode is a second trip to the shop. The sibling plan already declined
unit conversion; this is the same call made where it actually bites.

Quantities reach the shopping list as **text carried through**, not maths — `"1.5
lb"` for the chicken, so you can read it at the shelf. That needs a `note TEXT`
column on `shopping_list_items` (guarded `ALTER`, same as `section` on
`pantry_items`); the REAL `quantity_to_buy` stays untouched so the existing
check-off/increment flow keeps working. When three meals want onions they produce
**one** row whose note is `"1 lb + 2 count"` — concatenated, not summed. Same
principle: honest about what it can't add up.

---

## Phase 1 — The recipe database (no AI)

**Goal:** a browsable, hand-editable store of what we cook. Worth having on its
own even if every later phase is abandoned.

1. `recipe_loader.py` — modelled on `content_loader.py`: parse frontmatter,
   require `name`, log-and-skip a bad file. Keeps numerics numeric. Returns a
   `Recipe` dataclass.
2. `recipe_store.py` — `RecipeStore` with `reload()`, `all()`, `get(slug)`,
   `favorites()`, and filters by `kind` / `cuisine` / max `effort`. Same
   single-worker in-memory-cache justification as `CatalogStore`.
3. `recipe_writer.py` — `write_recipe(recipes_dir, name, frontmatter, body)`,
   importing `catalog_writer.slugify` and `_unique_path`. Don't fork the slug logic.
4. Routes `/recipes` (grouped by `kind`, filters), `/recipes/<slug>`,
   `/recipes/new`, `/recipes/<slug>/edit`; nav tab in `base.html` between Freezer
   and Shopping List.
5. `HOMEHQ_RECIPES_DIR` in `.env.example`, `create_app`, `tests/conftest.py`;
   `/reload` reloads the recipe store alongside the catalog.
6. Seed 5-10 real recipes by hand to shake out the frontmatter shape before
   writing more code against it.

- **Files:** new `recipe_loader.py`, `recipe_store.py`, `recipe_writer.py`,
  `templates/recipes.html`, `templates/recipe_detail.html`,
  `templates/recipe_form.html`; `app.py`, `templates/base.html`, `.env.example`,
  `tests/conftest.py`
- **Tests first:** `test_recipe_loader_parses_ingredients_as_list_of_dicts`,
  `test_recipe_loader_keeps_quantity_numeric`,
  `test_recipe_loader_skips_file_missing_name`,
  `test_recipe_loader_defaults_staple_to_false`,
  `test_recipe_store_favorites_excludes_non_favorites`,
  `test_recipe_store_filters_by_kind_and_max_effort`,
  `test_write_recipe_creates_slugged_file_with_frontmatter`,
  `test_write_recipe_does_not_overwrite_existing_slug`,
  `test_recipes_page_requires_login`, `test_recipes_page_groups_by_kind`,
  `test_recipe_detail_renders_ingredients_and_body`,
  `test_new_recipe_form_writes_file_and_appears_in_list`,
  `test_recipes_dir_is_not_scanned_by_catalog_store`
- **Verify:** the last test matters most — add a recipe, load `/report` and
  `/report.csv`, confirm nothing from `recipes/` appears.
- **Effort:** L

---

## Phase 2 — "Can I make this?" on a recipe page

**Goal:** ship the netting design against a single recipe with no LLM anywhere
near it. This is the hard part of the feature; de-risk it before adding a model.

1. `pantry_check.py` — `check_ingredients(ingredients, inventory)` returns one row
   per ingredient tagged `on_hand` / `check` / `missing`, carrying the matched
   inventory row when there is one. Pure function, no Flask, no DB. Matches across
   pantry *and* freezer (a recipe doesn't care where the peas live), reusing
   `matching.find_best_match` unchanged.
2. `note TEXT` on `shopping_list_items` via the guarded `ALTER` pattern;
   `add_shopping_list_item(..., note=None)`; render it in `shopping_list.html`
   beside the existing `quantity_to_buy`.
3. An "Ingredients we have" panel on `/recipes/<slug>` rendering the three
   buckets: missing pre-checked, check unchecked, staples collapsed.
4. `/recipes/<slug>/shop` (POST) — add the checked ingredients with `note` set to
   `"{quantity} {unit}"`.

- **Files:** new `pantry_check.py`; `db.py`, `app.py`,
  `templates/recipe_detail.html`, `templates/shopping_list.html`
- **Tests first:** `test_check_marks_exact_name_match_as_on_hand`,
  `test_check_marks_fuzzy_name_match_as_check`,
  `test_check_marks_unmatched_ingredient_as_missing`,
  `test_check_matches_across_pantry_and_freezer`,
  `test_staple_with_match_is_assumed_on_hand`,
  `test_staple_without_match_is_still_missing`,
  `test_check_never_computes_a_deficit_quantity`,
  `test_note_column_added_to_existing_shopping_list_db`,
  `test_shop_route_adds_only_checked_ingredients`,
  `test_shop_route_carries_recipe_amount_into_note`
- **Verify:** run it against a real recipe and the real 107-row pantry. Count how
  many ingredients land in "check" — if it's most of them, the fix is more staple
  flags, not more code.
- **Effort:** M

---

## Phase 3 — Household skills files

**Goal:** the "markdown skills for the agent" idea, made concrete before the
planner needs it. These are **prompt fragments kept in the repo and loaded at
request time** — standing household context the model gets every time, editable
by hand in git, with no UI to build.

```
skills/household.md     two adults; what "for 2" means; weeknight time budget
skills/dislikes.md      hard nos (no mushrooms, no blue cheese) and soft ones
skills/conventions.md   portioning, leftovers policy, how we buy meat
```

1. `skills_store.py` — `SkillsStore(dir)` with `reload()` and
   `as_system_prompt()`, concatenating every `*.md` in filename order under
   `## <stem>` headings. Cap at ~8000 chars with a warning log. Missing directory
   returns `""` — the app must run without it.
2. Load at boot next to `app.catalog`; refresh on `/reload`.
3. A read-only `/skills` page showing what's loaded, so you can see what the model
   is being told without SSHing in.
4. Ship the three starter files with real content.

**One caveat, worth writing into `dislikes.md` itself:** a system prompt is a
strong nudge, not enforcement. So Phase 4 also *checks* the hard nos in code — a
deny-list scan over returned ingredient names, flagged on the review screen.
Cheap, and it's the difference between a preference and a constraint.

- **Files:** new `skills_store.py`, `skills/*.md`, `templates/skills.html`;
  `app.py`, `.env.example`, `tests/conftest.py`
- **Tests first:** `test_skills_store_concatenates_files_in_filename_order`,
  `test_skills_store_returns_empty_string_when_dir_missing`,
  `test_skills_store_truncates_past_size_cap`,
  `test_skills_page_renders_loaded_content`, `test_reload_route_refreshes_skills`
- **Effort:** S

---

## Phase 4 — The meal plan request

**Goal:** free text in, a reviewable plan out. No writes to anything yet.

**What gets sent** — all of it; the payload is small enough that trimming is false
economy:

| Piece | Shape | ~Tokens |
|---|---|---|
| Skills files | system prompt | 400-1,500 |
| Favorites index | slug, name, kind, cuisine, effort, serves, tags, **ingredient names only** | ~35 × recipes (60 ≈ 2,100) |
| Full inventory | name, quantity, unit, section, storage — all 107 rows | ~1,500 |
| Your prompt | as typed | ~100 |

≈5-6k input, 1.5-2.5k output. Send the whole inventory; there's no version of
this where withholding it helps.

**The anti-hallucination rule, and it is not optional: for a stored favorite the
model returns only the slug.** The app looks up ingredients from the file. The
model never restates a favorite's ingredient list — it will paraphrase, drop the
anchovies, and hand you a shopping list for a recipe you don't own. Only *new*
suggestions carry model-supplied ingredients. The index sends ingredient names so
the model can reason about what's cookable; the plan is rebuilt from the files.

**Model: `claude-sonnet-5`**, not haiku. `ai_extract` uses haiku correctly —
reading a receipt is transcription. This is judgment: balance three meals, honour
standing constraints, invent one plausible new dish. Haiku produces bland,
repetitive plans and you stop using the feature. At $2/$10 per MTok a plan costs
~3.5¢; three a week is ~$5/year, so cost is not the constraint. Leave
`ai_extract.DEFAULT_MODEL` on haiku — this is a separate module with its own
constant.

**Structured outputs — not JSON-in-prose, and not tool use.** Pass
`output_config={"format": {"type": "json_schema", "schema": {...}}}` on
`messages.create`. The output nests three levels (plan → meals → ingredients) and
`_strip_code_fence` + `json.loads` is exactly the wrong tool at that depth.
Tool-use-as-schema was the pre-structured-outputs workaround; there's no function
to execute here, so don't pretend there is. (`output_config` exists in the pinned
`anthropic==0.125.0`; the local `.venv` is on 0.84.0 — `pip install -r
requirements.txt` before starting this phase.)

1. `meal_planner.py`, shaped exactly like `ai_extract`: module-level `_PROMPT` and
   `_SCHEMA` constants, `MealPlanError`, a pure `parse_meal_plan(data,
   recipe_store)` taking an already-decoded dict, and `request_meal_plan(prompt,
   inventory, favorites, skills, client=None)` with the injectable client so tests
   never hit the network.
2. `build_favorites_index(recipes)` and `build_inventory_context(items)` — pure
   and separately tested, so the payload shape is pinned by a test rather than by
   whatever the prompt happens to say.
3. Schema: `{"meals": [{"slug": str|null, "name": str, "why": str, "new_recipe":
   {…}|null}], "notes": str}`. Exactly one of `slug`/`new_recipe` per meal; reject
   both-or-neither in `parse_meal_plan`.
4. Deny-list check over every ingredient name against `dislikes.md` hard-nos;
   surface violations on the review screen, don't silently drop.
5. `/plan` (POST from a textarea on `shopping_list.html`, where it was asked for)
   → persist to `meal_plans` → redirect to `/plan/<id>`.
6. `/plan/<id>` review page: each meal with its source (favorite vs. new), full
   ingredient list, and the Phase 2 three-bucket check applied.

- **Files:** new `meal_planner.py`, `templates/meal_plan.html`; `db.py`, `app.py`,
  `templates/shopping_list.html`
- **Tests first:** `test_parse_meal_plan_resolves_favorite_slug_from_store`,
  `test_parse_meal_plan_ignores_model_supplied_ingredients_for_favorites`,
  `test_parse_meal_plan_keeps_ingredients_for_new_recipes`,
  `test_parse_meal_plan_rejects_meal_with_both_slug_and_new_recipe`,
  `test_parse_meal_plan_rejects_unknown_slug`,
  `test_favorites_index_omits_recipe_bodies`,
  `test_favorites_index_excludes_non_favorites`,
  `test_inventory_context_includes_all_items`,
  `test_request_meal_plan_sends_skills_as_system_prompt`,
  `test_request_meal_plan_uses_structured_output_schema`,
  `test_request_meal_plan_does_not_hit_network_with_injected_client`,
  `test_deny_list_flags_hard_no_ingredient`,
  `test_plan_route_persists_and_redirects_to_review`,
  `test_plan_review_page_survives_refresh_without_second_api_call`
- **Verify:** run the real prompt from the brief against the real recipe set and
  read the plan critically. The failure to watch for is the model ignoring the
  inventory and proposing three meals of things you don't own — if that happens
  the fix is prompt work, not architecture.
- **Effort:** L

---

## Phase 5 — Approve a plan

**Goal:** approved plan → shopping list, plus the suggested recipes kept.

**Suggested recipes get written to `recipes/` with `favorite: false` and `source:
ai-suggested`.** Not staged, not discarded. Discarding loses the method for a dish
you just decided to cook on Thursday; staging means a second review queue when the
plan page *is* the review. `favorite: false` keeps it out of the "pull our
favorites" pool until you've eaten it. It's a git-tracked file — a bad one is one
`rm`.

1. `/plan/<id>/approve` (POST): write each `new_recipe` via `recipe_writer`, add
   the checked ingredients to the shopping list, mark the plan `approved`,
   `git_commit_and_push` if `HOMEHQ_GITHUB_TOKEN` is set (same conditional as
   `import_approve`), reload the recipe store.
2. Per-ingredient checkboxes defaulted per the Phase 2 buckets. Nothing is added
   without a checkbox — including the "missing" ones.
3. Deduplicate across meals: one row per ingredient name, notes concatenated.
4. `/plan/<id>/discard`.
5. "We made it" (sets `last_made`, offers *mark favorite*) and "Never again"
   (deletes the file) on `/recipes/<slug>`. The real review of a suggestion
   happens after dinner.

- **Files:** `app.py`, `recipe_writer.py`, `db.py`, `templates/meal_plan.html`,
  `templates/recipe_detail.html`
- **Tests first:** `test_approve_writes_new_recipes_as_not_favorite`,
  `test_approve_does_not_rewrite_existing_favorite_recipes`,
  `test_approve_adds_only_checked_ingredients_to_shopping_list`,
  `test_approve_deduplicates_ingredient_across_meals`,
  `test_approve_concatenates_notes_rather_than_summing`,
  `test_approve_marks_plan_approved`, `test_approving_twice_does_not_double_add`,
  `test_discard_leaves_shopping_list_untouched`,
  `test_mark_favorite_updates_frontmatter`, `test_never_again_deletes_recipe_file`
- **Effort:** M

---

## Phase 6 — Effort inference and polish

**Goal:** stop the effort field going stale and unset.

**Effort is both AI and human, human wins.** The field is `effort: 1-5`, because
that's the question actually asked ("something easy tonight") and minutes don't
answer it — a 3-hour braise is 20 minutes of work; a risotto is 25 minutes of
standing at the stove. `active_minutes` and `total_minutes` come along as optional
objective extras since recipe sources usually state them. Leave `effort` blank on
save and it's inferred from the method body with a **haiku** call — this one *is*
transcription-shaped — writing `effort_inferred: true`. The UI renders "(est.)",
reusing the exact pattern `inventory.html` already uses for
`effective_expiry_estimated`. Any human edit clears the flag and the AI never
touches it again.

1. `meal_planner.infer_effort(name, body, client=None)` — haiku, small schema,
   injectable client.
2. Call it on save only when `effort` is blank; never overwrite.
3. "(est.)" rendering and an effort filter on `/recipes`.
4. Sort favorites by `last_made` so the rotation is visible.

- **Files:** `meal_planner.py`, `recipe_writer.py`, `app.py`,
  `templates/recipes.html`, `templates/recipe_form.html`
- **Tests first:** `test_infer_effort_returns_int_in_range`,
  `test_infer_effort_only_called_when_field_blank`,
  `test_inferred_effort_sets_estimated_flag`,
  `test_human_edit_clears_inferred_flag`,
  `test_recipes_page_filters_by_max_effort`
- **Effort:** S

---

## Decided against / deferred

- **Unit conversion and quantity arithmetic.** Reason above, and it isn't a
  resourcing decision: the inventory doesn't record amounts in a convertible form,
  and making it do so means weighing the pantry.
- **Recipes as SQLite tables.** Loses hand-editing and git history; gains querying
  we don't need at this scale. The structured part we actually need — ingredient
  names — fits fine in YAML.
- **Recipes under `content/`.** They'd land in the insurance report and CSV. The
  alternative — an exclusion list inside `load_catalog` — is a magic constant that
  bites whoever adds the next content type.
- **Routing suggested recipes through `import_staging_items`.** Its columns are
  shaped for pantry rows and catalog items; a recipe body and ingredient list
  don't fit without columns nothing else uses. And the plan page is already a
  review page — two queues is one too many.
- **Tool-use as the output mechanism.** Structured outputs do the same job without
  inventing a function that never runs.
- **Meal calendar / weekly schedule.** The ask is "what do we buy", not "what do
  we eat Tuesday". Days multiply the review UI for no shopping-list benefit.
- **Nutrition data.** Needs a food database, a vendor, and per-ingredient matching
  harder than the netting problem. Not asked for.
- **Scaling.** Everything here loads every recipe into memory per request.
  Correct at 50 and at 500; at 5,000 it's a different app.

---

## Open questions

1. **What does effort 3 mean?** Grade four real recipes — a 1, a 3, a 5 — before
   Phase 6. Without anchors the inference prompt has nothing to calibrate against
   and everything comes back a 3.
2. **What does "for 2" mean?** Two portions, or two dinners plus a lunch? It
   changes every quantity in every file, and belongs in `skills/household.md`
   before recipe #10, not after.
3. **How do the first 20-30 favorites get in?** The biggest launch risk in this
   plan, and not a coding problem: a recipe database with six entries makes a bad
   planner, and typing thirty by hand is an evening. `ai_extract.extract_from_text`
   already exists in the working tree, so paste-a-recipe → frontmatter is a new
   prompt and schema against machinery that's built. Worth slotting between
   Phases 1 and 4?
4. **Should the planner prefer recipes that use expiring inventory?** `expiry.py`
   already computes it, so it's nearly free to include. But it changes the
   planner's job from "what do we want" to "what should we use up", and those pull
   in different directions.
5. **Is the plan allowed to say no?** If a favorite needs eight things you don't
   have, should the model deprioritise it or plan freely and let the shopping list
   be long? Freely is the honest default, but it's your money.
