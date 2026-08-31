import json
import os

import anthropic

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

_VALID_CATEGORIES = ("kitchen", "tools", "manuals", "valuables")

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
 "model": null, "serial_number": null, "notes": "..."}
- "category" must be one of: kitchen, tools, manuals, valuables.
- Fill in whatever fields you can confidently read; use null for the rest.
- "notes" is free text: anything else useful you can see (condition,
  distinguishing marks, edition, etc).
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

    if kind == "receipt":
        rows = []
        for raw_item in data.get("items", []):
            name = raw_item.get("name")
            if not name:
                raise ExtractionError("Receipt item is missing a name")
            rows.append(
                {
                    "target_type": "inventory",
                    "name": name,
                    "quantity": raw_item.get("quantity"),
                    "unit": raw_item.get("unit"),
                    "storage": raw_item.get("storage") or "pantry",
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
            }
        ]

    raise ExtractionError(f"Unrecognized response kind: {kind!r}")


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
