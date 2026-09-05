import json
import os

import anthropic

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

_PROMPT = """You are looking at a photo that is either (a) a store receipt, or
(b) a single physical household item (a tool, kitchen item, appliance manual,
or a valuable/collectible like a record, instrument, or electronics).

Decide which one it is, then respond with ONLY a JSON object (no prose, no
markdown fences) in one of these two exact shapes:

Receipt:
{"kind": "receipt", "items": [
  {"name": "...", "quantity": 1, "unit": "...", "storage": "pantry"}
]}
- "storage" must be "pantry" or "freezer" (guess based on the item -- frozen
  foods go to "freezer", everything else to "pantry").
- Skip non-food line items (bags, tax, discounts).

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


class ExtractionError(Exception):
    pass


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

    if kind in ("receipt", "pantry_items"):
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


def extract_from_text(text, api_key=None, model=None, client=None):
    """Turn a free-text description of groceries into staging-item rows.

    This is also the voice path: iOS keyboard dictation types into the same
    textarea, so no speech-to-text service is involved.
    """
    if client is None:
        client = anthropic.Anthropic(api_key=api_key or os.environ["HOMEHQ_ANTHROPIC_API_KEY"])

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

    if client is None:
        client = anthropic.Anthropic(api_key=api_key or os.environ["HOMEHQ_ANTHROPIC_API_KEY"])

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
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
    )
    return parse_extraction_response(message.content[0].text)
