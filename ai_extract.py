import json
import os

import anthropic

import recipe_loader
import sections

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

_VALID_CATEGORIES = (
    "kitchen",
    "tools",
    "manuals",
    "musical-instruments",
    "vinyl-equipment",
    "vinyl-records",
    "jewelry",
    "electronics",
    "valuables",
)

_PROMPT = """You are looking at a photo that is one of: (a) a store receipt,
(b) a shelf, cupboard or freezer drawer holding several food items, or
(c) a single physical household item (a tool, kitchen item, appliance manual,
or a valuable/collectible like a record, instrument, or electronics).

Decide which one it is, then respond with ONLY a JSON object (no prose, no
markdown fences) in one of these exact shapes:

Receipt:
{"kind": "receipt", "items": [
  {"name": "...", "quantity": 1, "unit": "...", "storage": "pantry"}
]}
- "storage" must be "pantry" or "freezer" (guess based on the item -- frozen
  foods go to "freezer", everything else to "pantry").
- Skip non-food line items (bags, tax, discounts).

Shelf or cupboard of food:
{"kind": "shelf", "items": [
  {"name": "...", "quantity": null, "unit": null, "storage": "pantry",
   "section": "canned"}
]}
- Use this when you can see several distinct food items stored together.
- "quantity" is usually NOT knowable from a photo -- you cannot see how full
  a jar is or how many tins are behind the front one. Use null unless you can
  literally count the items. A wrong count is worse than a blank the human
  fills in.
- "section" must match the item's storage; see the section list below.
- Name what you can identify; skip anything you cannot read or recognise
  rather than guessing at a blurred label.

Single item:
{"kind": "catalog_item", "name": "...", "category": "kitchen", "brand": null,
 "model": null, "serial_number": null, "notes": "...", "estimated_value": null}
- "category" must be exactly one of:
  kitchen (cookware, utensils, kitchen appliances)
  tools (hand/power tools, hardware, garage/workshop items)
  manuals (appliance/product manuals and documentation)
  musical-instruments (instruments, and gear like pedals, amps, cables used to play them)
  vinyl-equipment (turntables, phono preamps, speakers/receivers for playing records)
  vinyl-records (the records themselves)
  electronics (laptops, tablets, phones, and other general electronics not covered above)
  jewelry
  valuables (anything else worth insurance-documenting that doesn't fit the above)
- Fill in whatever fields you can confidently read; use null for the rest.
- "notes" is free text: anything else useful you can see (condition,
  distinguishing marks, edition, etc).
- "estimated_value" is a ROUGH ballpark of current resale/replacement value,
  as a single plain number in USD with NO dollar sign, NO commas, and NO
  range (e.g. 200, not "$150-250" or "$200-300") -- pick one reasonable
  figure. NOT a real appraisal or live market lookup. Only provide one for
  recognizable, valuable-ish items (electronics, tools, collectibles); use
  null for everyday items or anything you're not reasonably confident about
  (a manual, a generic kitchen tool, an item you can't identify well
  enough to guess).

Valid "section" values, matching the item's storage:
{sections}
"""


def _section_prompt_lines():
    """Render the valid section keys so the prompt can't drift from sections.py."""
    pantry = ", ".join(key for key, _ in sections.sections_for("pantry"))
    freezer = ", ".join(key for key, _ in sections.sections_for("freezer"))
    return f"  pantry: {pantry}\n  freezer: {freezer}"


_TEXT_PROMPT = """The user is describing food they are adding to their pantry
or freezer, in their own words. It may be dictated speech, so expect run-on
phrasing and filler.

Respond with ONLY a JSON object (no prose, no markdown fences):

{{"kind": "pantry_items", "items": [
  {{"name": "...", "quantity": 2, "unit": "can", "storage": "pantry",
   "section": "canned"}}
]}}

- "storage" is "pantry" or "freezer" -- frozen things go to "freezer".
- "section" must be exactly one of these, matching the item's storage:
{sections}
- "quantity" and "unit" may be null. If the user did not say how much
  ("some rice left", "a bit of flour"), use null rather than guessing a
  number -- a wrong number is worse than a blank the human fills in.
- Split a list into one entry per distinct item.
- Ignore anything that isn't food being added.

The user said:
{text}
"""


_INGREDIENTS_PROMPT = """Below are cooking notes for a recipe. Pull out the
ingredients they mention.

Respond with ONLY a JSON object (no prose, no markdown fences):

{{"kind": "ingredients", "items": [
  {{"name": "ground beef", "quantity": 1, "unit": "lb", "fresh": true,
   "staple": false}}
]}}

- "quantity" and "unit" may be null. These notes are terse and often name an
  ingredient without an amount. Null is correct; do NOT invent quantities.
- "fresh" is true for produce, dairy, fresh meat and fish, and fresh herbs --
  anything bought for this week. Fresh items always go on the shopping list.
- "staple" is true for spices, dried herbs, oils, vinegars, flour, sugar and
  similar cupboard basics that a kitchen always has.
- An ingredient is not both fresh and staple. If neither applies (tinned,
  jarred, dried, frozen goods), set both false.
- Name the ingredient as you would write it on a shopping list: "ground beef",
  not "lean ground beef 90/10 preferred". Put nothing else in the name.
- Write names in lower case, except brand names and proper nouns
  ("parmesan", but "Fritos").
- Stock, bouillon and stock paste are staples.
- The recipe's NAME usually names the dish's main components, and those are
  always ingredients even when the notes never repeat them. "Pan-Fried Pork
  Chops" needs pork chops; "Salmon, Potato & Vegetable" needs all three. Some
  notes only discuss technique and name no ingredient at all -- read the title
  in that case rather than returning an empty list.
- Serving suggestions need judgement. Include an accompaniment only if it is
  one thing you would buy: sour cream, cheddar, avocado, tortillas. EXCLUDE
  accompaniments that are separate dishes needing their own cooking: mashed
  potatoes, salad, garlic bread, rice pilaf.
- When the notes offer alternatives -- "serve with pasta, mashed potatoes,
  rice, or bread" -- that is a choice made at dinnertime, not a shopping list.
  Include none of them, UNLESS the title names them as part of the dish.
- Where alternatives are written with slashes ("broccoli/asparagus/green
  beans"), pick the first as a stand-in rather than listing the slashed string.
- Beyond the title's own components, do not invent ingredients the notes do
  not mention, even if the dish would normally use them.

Recipe: {name}

Notes:
{text}
"""


_STEPS_PROMPT = """Turn this recipe into a dependency graph of cooking steps.

Respond with ONLY a JSON object (no prose, no markdown fences):

{{"kind": "steps", "items": [
  {{"id": "s1", "action": "season and sear", "inputs": ["chicken thighs"]}},
  {{"id": "s2", "action": "simmer 45 min", "inputs": ["s1", "tomatoes"]}},
  {{"id": "s3", "action": "serve in tortillas", "inputs": ["s2", "tortillas"]}}
]}}

- Each "inputs" entry is EITHER an ingredient name, spelled exactly as it
  appears in the ingredient list below, OR the id of an earlier step.
- Ids must be unique and a step may only reference steps defined before it.
  Never create a cycle.
- The last step is the finished dish. Work toward a single final step.
- "action" is a short imperative phrase with any timing or temperature that
  matters: "simmer 45 min", "bake at 200C for 25 min", "rest 10 min". Keep it
  under about eight words.
- Every ingredient in the list should be consumed by some step.
- Aim for 3-8 steps. Combine trivial actions rather than making a step per
  ingredient.
- Fill in ordinary technique the notes leave out, but stay recognisably the
  same dish. Do not add ingredients that are not in the list.

Recipe: {name}

Ingredients:
{ingredients}

Notes from the cook:
{notes}
"""


class ExtractionError(Exception):
    pass


def _build_client(api_key, client):
    """Return the injected client, or construct one from a validated key.

    The key ends up in an HTTP header, so a stray non-ASCII character (a Mac
    types 'ß' for Option+S, which is easy to pick up mid-copy) surfaces as a
    UnicodeEncodeError from deep inside the transport. Catch it here where we
    can say which character and where.
    """
    if client is not None:
        return client

    key = api_key or os.environ.get("HOMEHQ_ANTHROPIC_API_KEY") or ""
    if not key.strip():
        raise ExtractionError(
            "The Anthropic API key is not configured (HOMEHQ_ANTHROPIC_API_KEY)."
        )
    for index, char in enumerate(key):
        if ord(char) > 127:
            raise ExtractionError(
                f"The Anthropic API key contains a non-ASCII character "
                f"({char!r}) at position {index}, so it cannot be a valid key. "
                "It was most likely mangled when copied -- copy it again."
            )
    return anthropic.Anthropic(api_key=key)


def _strip_code_fence(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def parse_extraction_response(response_text):
    """Parse the model's JSON reply into a list of staging-item-ready dicts."""
    try:
        data = json.loads(_strip_code_fence(response_text))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ExtractionError(f"Model response was not valid JSON: {exc}") from exc

    kind = data.get("kind")

    if kind in ("receipt", "pantry_items", "shelf"):
        rows = []
        for raw_item in data.get("items", []):
            name = raw_item.get("name")
            if not name:
                raise ExtractionError("Item is missing a name")
            storage = raw_item.get("storage") or "pantry"
            rows.append(
                {
                    "target_type": "inventory",
                    "name": name,
                    "quantity": raw_item.get("quantity"),
                    "unit": raw_item.get("unit"),
                    "storage": storage,
                    # normalize() is validated against the item's own storage,
                    # so a pantry section on a freezer item collapses to
                    # "other" rather than silently sticking.
                    "section": sections.normalize(storage, raw_item.get("section")),
                }
            )
        return rows

    if kind == "catalog_item":
        name = data.get("name")
        if not name:
            raise ExtractionError("Catalog item is missing a name")
        category = data.get("category")
        if category not in _VALID_CATEGORIES:
            category = None
        return [
            {
                "target_type": "catalog",
                "name": name,
                "category": category,
                "brand": data.get("brand"),
                "model": data.get("model"),
                "serial_number": data.get("serial_number"),
                "notes": data.get("notes"),
                "estimated_value": _coerce_value(data.get("estimated_value")),
            }
        ]

    raise ExtractionError(f"Unrecognized response kind: {kind!r}")


def _coerce_value(raw):
    """Best-effort coercion to a plain float; None if it isn't parseable
    (e.g. the model ignored instructions and returned a range/string)."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_ingredients_response(response_text):
    """Parse an ingredients reply into normalized ingredient dicts.

    A single unusable entry is dropped rather than failing the whole
    extraction -- losing 11 good ingredients over one bad one is worse than
    the human deleting a stray line on the review screen.
    """
    try:
        data = json.loads(_strip_code_fence(response_text))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ExtractionError(f"Model response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict) or "items" not in data:
        raise ExtractionError("Response did not contain an 'items' list")

    ingredients = []
    for raw in data.get("items") or []:
        try:
            ingredient = recipe_loader.normalize_ingredient(raw)
        except (ValueError, TypeError):
            continue
        if ingredient["fresh"] and ingredient["staple"]:
            # Mutually exclusive by definition; fresh is the safer call
            # because it keeps the item on the shopping list.
            ingredient["staple"] = False
        ingredients.append(ingredient)
    return ingredients


def extract_ingredients(name, text, api_key=None, model=None, client=None):
    """Propose structured ingredients from a recipe's freeform notes."""
    client = _build_client(api_key, client)

    message = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": _INGREDIENTS_PROMPT.format(name=name, text=text),
                    }
                ],
            }
        ],
    )
    return parse_ingredients_response(message.content[0].text)


def parse_steps_response(response_text):
    """Parse a steps reply into normalized step dicts."""
    try:
        data = json.loads(_strip_code_fence(response_text))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ExtractionError(f"Model response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict) or "items" not in data:
        raise ExtractionError("Response did not contain an 'items' list")
    return recipe_loader.normalize_steps(data.get("items"))


def extract_steps(name, ingredients, notes, api_key=None, model=None, client=None):
    """Propose a cooking-step graph for a recipe."""
    client = _build_client(api_key, client)

    listed = "\n".join(f"- {i.get('name')}" for i in ingredients or []) or "- (none recorded)"
    message = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": _STEPS_PROMPT.format(
                            name=name, ingredients=listed, notes=notes or "(none)"
                        ),
                    }
                ],
            }
        ],
    )
    return parse_steps_response(message.content[0].text)


def extract_from_text(text, api_key=None, model=None, client=None):
    """Turn a free-text description of groceries into staging-item rows.

    This is also the voice path: iOS keyboard dictation types into the same
    textarea, so no speech-to-text service is involved.
    """
    client = _build_client(api_key, client)

    message = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": _TEXT_PROMPT.format(
                            sections=_section_prompt_lines(), text=text
                        ),
                    }
                ],
            }
        ],
    )
    return parse_extraction_response(message.content[0].text)


def extract_from_image(image_bytes, media_type, api_key=None, model=None, client=None):
    """Send an image to Claude and return parsed staging-item rows.

    `client` can be injected for testing; otherwise a real anthropic.Anthropic
    client is constructed from api_key/ANTHROPIC_API_KEY.
    """
    import base64

    client = _build_client(api_key, client)

    message = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        },
                    },
                    {
                        "type": "text",
                        # replace(), not format(): this prompt contains literal
                        # JSON braces that format() would choke on.
                        "text": _PROMPT.replace("{sections}", _section_prompt_lines()),
                    },
                ],
            }
        ],
    )
    return parse_extraction_response(message.content[0].text)
