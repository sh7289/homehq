import json

import pytest


def test_parses_receipt_response_into_inventory_staging_rows():
    from ai_extract import parse_extraction_response

    response = json.dumps(
        {
            "kind": "receipt",
            "items": [
                {"name": "Rice", "quantity": 2, "unit": "bags", "storage": "pantry"},
                {"name": "Frozen Peas", "quantity": 1, "unit": "bag", "storage": "freezer"},
            ],
        }
    )

    rows = parse_extraction_response(response)

    assert rows == [
        {
            "target_type": "inventory",
            "name": "Rice",
            "quantity": 2,
            "unit": "bags",
            "storage": "pantry",
        },
        {
            "target_type": "inventory",
            "name": "Frozen Peas",
            "quantity": 1,
            "unit": "bag",
            "storage": "freezer",
        },
    ]


def test_parses_catalog_item_response():
    from ai_extract import parse_extraction_response

    response = json.dumps(
        {
            "kind": "catalog_item",
            "name": "Kind of Blue",
            "category": "valuables",
            "brand": "Columbia Records",
            "model": None,
            "serial_number": None,
            "notes": "Miles Davis, 1959 pressing",
        }
    )

    rows = parse_extraction_response(response)

    assert rows == [
        {
            "target_type": "catalog",
            "name": "Kind of Blue",
            "category": "valuables",
            "brand": "Columbia Records",
            "model": None,
            "serial_number": None,
            "notes": "Miles Davis, 1959 pressing",
        }
    ]


def test_strips_markdown_code_fence_around_json():
    from ai_extract import parse_extraction_response

    response = "```json\n" + json.dumps({"kind": "receipt", "items": []}) + "\n```"

    rows = parse_extraction_response(response)

    assert rows == []


def test_raises_on_invalid_json():
    from ai_extract import ExtractionError, parse_extraction_response

    with pytest.raises(ExtractionError):
        parse_extraction_response("not json at all")


def test_raises_on_unknown_kind():
    from ai_extract import ExtractionError, parse_extraction_response

    with pytest.raises(ExtractionError):
        parse_extraction_response(json.dumps({"kind": "mystery"}))


def test_raises_when_receipt_item_missing_name():
    from ai_extract import ExtractionError, parse_extraction_response

    response = json.dumps({"kind": "receipt", "items": [{"quantity": 1}]})

    with pytest.raises(ExtractionError):
        parse_extraction_response(response)
