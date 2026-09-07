import json


class _FakeContentBlock:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeContentBlock(text)]


class _FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeMessage(self._response_text)


class _FakeClient:
    def __init__(self, response_text):
        self.messages = _FakeMessages(response_text)


def test_extract_from_image_sends_image_and_returns_parsed_rows():
    from ai_extract import extract_from_image

    fake_client = _FakeClient(json.dumps({"kind": "receipt", "items": []}))

    rows = extract_from_image(b"fake-jpeg-bytes", "image/jpeg", client=fake_client)

    assert rows == []
    call = fake_client.messages.calls[0]
    content = call["messages"][0]["content"]
    image_block = next(c for c in content if c["type"] == "image")
    assert image_block["source"]["media_type"] == "image/jpeg"
    assert image_block["source"]["data"]  # base64 payload present


def test_a_malformed_api_key_is_rejected_with_a_clear_message():
    """A key with a stray non-ASCII character (Option+S types 'ß' on a Mac)
    otherwise surfaces as a UnicodeEncodeError inside an HTTP header."""
    import pytest

    import ai_extract

    with pytest.raises(ai_extract.ExtractionError, match="position 7"):
        ai_extract.extract_from_text("two cans of beans", api_key="sk-ant-ßxyz")


def test_a_blank_api_key_is_rejected_clearly():
    import pytest

    import ai_extract

    with pytest.raises(ai_extract.ExtractionError, match="not configured"):
        ai_extract.extract_ingredients("Chili", "notes", api_key="")
