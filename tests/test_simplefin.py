import base64
import json
from decimal import Decimal

import pytest
import requests

import simplefin


class FakeResponse:
    def __init__(self, status=200, payload=None, body=None, chunks=None):
        self.status_code = status
        if body is None and payload is not None:
            body = json.dumps(payload).encode("utf-8")
        self._body = body or b""
        self._chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size=65536):
        if self._chunks is not None:
            yield from self._chunks
        else:
            yield self._body

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        if self.error:
            raise self.error
        return self.response

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        if self.error:
            raise self.error
        return self.response


def account(account_id="acct-1", conn_id="conn-1", **overrides):
    value = {
        "id": account_id,
        "name": "Provider Checking 4321",
        "conn_id": conn_id,
        "currency": "USD",
        "balance": "100.10",
        "balance-date": 1_788_739_200,
        "transactions": [{"id": "must-not-survive", "amount": "9.00"}],
        "extra": {"sensitive": "must-not-survive"},
    }
    value.update(overrides)
    return value


def payload(accounts=None, errlist=None):
    return {
        "errlist": [] if errlist is None else errlist,
        "connections": [
            {
                "conn_id": "conn-1",
                "name": "Provider login name",
                "org_id": "org-1",
                "sfin_url": "https://bank.example/simplefin",
            }
        ],
        "accounts": [account()] if accounts is None else accounts,
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pass@bridge.simplefin.org/simplefin",
        "https://user:pass@evil.example/simplefin",
        "https://user:pass@bridge.simplefin.org:444/simplefin",
        "https://user@bridge.simplefin.org/simplefin",
        "https://user:pass@bridge.simplefin.org",
        "https://user:pass@bridge.simplefin.org/simplefin?leak=yes",
        "https://user:pass@bridge.simplefin.org/simplefin#fragment",
        "https://user:pass@bridge.simplefin.org/simplefin\n/accounts",
        "https://user:pa%0Ass@bridge.simplefin.org/simplefin",
    ],
)
def test_fetch_rejects_unsafe_access_urls_before_network(url):
    session = FakeSession(FakeResponse(payload=payload()))

    with pytest.raises(simplefin.SimpleFINError, match="invalid configuration"):
        simplefin.fetch_balances(url, session=session)

    assert session.calls == []


def test_fetch_uses_v2_balances_only_without_credentials_in_request_url():
    response = FakeResponse(payload=payload())
    session = FakeSession(response)

    result = simplefin.fetch_balances(
        "https://alice:s3cr%40t@beta-bridge.simplefin.org/simplefin", session=session
    )

    method, url, kwargs = session.calls[0]
    assert method == "get"
    assert url == "https://beta-bridge.simplefin.org/simplefin/accounts"
    assert kwargs == {
        "auth": ("alice", "s3cr@t"),
        "params": {"version": "2", "balances-only": "1"},
        "allow_redirects": False,
        "timeout": (5, 30),
        "stream": True,
        "verify": True,
    }
    assert result["accounts"][0]["balance"] == Decimal("100.10")
    assert response.closed


def test_default_session_disables_ambient_auth_and_is_closed(monkeypatch):
    created = []

    class OwnedSession(FakeSession):
        def __init__(self):
            super().__init__(FakeResponse(payload=payload()))
            self.trust_env = True
            self.closed = False
            created.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(simplefin.requests, "Session", OwnedSession)

    simplefin.fetch_balances("https://u:p@bridge.simplefin.org/simplefin")

    assert created[0].trust_env is False
    assert created[0].closed is True


def test_normalization_scopes_ids_to_v2_connection_and_discards_provider_text():
    source = payload(accounts=[account(conn_id="login-a"), account(conn_id="login-b")])
    source["connections"] = [
        {
            "conn_id": conn_id,
            "name": "Provider login name",
            "org_id": "org-1",
            "sfin_url": "https://bank.example/simplefin",
        }
        for conn_id in ("login-a", "login-b")
    ]

    result = simplefin.fetch_balances(
        "https://u:p@bridge.simplefin.org/simplefin",
        session=FakeSession(FakeResponse(payload=source)),
    )

    first, second = result["accounts"]
    assert first["id"] != second["id"]
    assert len(first["id"]) == 64
    assert set(first) == {"id", "label", "provider_name", "institution", "currency", "balance", "balance_at"}
    assert first["provider_name"] == "Provider Checking ••••"
    assert first["institution"] == "Provider login name"
    assert "4321" not in first["label"]
    assert "transactions" not in repr(result)
    assert "sensitive" not in repr(result)


def test_legacy_identity_includes_organization_identifiers():
    legacy_accounts = [
        account(conn_id=None, org={"domain": "one.example", "sfin-url": "https://one"}),
        account(conn_id=None, org={"domain": "two.example", "sfin-url": "https://two"}),
    ]
    for item in legacy_accounts:
        item.pop("conn_id")
    legacy = {"errors": [], "accounts": legacy_accounts}

    result = simplefin.fetch_balances(
        "https://u:p@bridge.simplefin.org/simplefin",
        session=FakeSession(FakeResponse(payload=legacy)),
    )

    assert result["accounts"][0]["id"] != result["accounts"][1]["id"]


def test_ambiguous_duplicate_account_identity_is_rejected():
    source = payload(accounts=[account(), account(balance="999")])

    with pytest.raises(simplefin.SimpleFINError, match="invalid data"):
        simplefin.fetch_balances(
            "https://u:p@bridge.simplefin.org/simplefin",
            session=FakeSession(FakeResponse(payload=source)),
        )


def test_v2_accounts_require_a_matching_valid_connection():
    source = payload()
    source["connections"] = []

    with pytest.raises(simplefin.SimpleFINError, match="invalid data"):
        simplefin.fetch_balances(
            "https://u:p@bridge.simplefin.org/simplefin",
            session=FakeSession(FakeResponse(payload=source)),
        )


@pytest.mark.parametrize(
    "balance", ["NaN", "Infinity", "-Infinity", "1e100", "1e-1000000", "", 12.0]
)
def test_nonfinite_huge_or_nonstring_balances_are_rejected(balance):
    source = payload(accounts=[account(balance=balance)])

    with pytest.raises(simplefin.SimpleFINError, match="invalid data"):
        simplefin.fetch_balances(
            "https://u:p@bridge.simplefin.org/simplefin",
            session=FakeSession(FakeResponse(payload=source)),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"balance-date": -1},
        {"balance-date": "yesterday"},
        {"currency": "US"},
        {"currency": "usd"},
        {"id": ""},
    ],
)
def test_malformed_account_fields_are_rejected(overrides):
    source = payload(accounts=[account(**overrides)])

    with pytest.raises(simplefin.SimpleFINError, match="invalid data"):
        simplefin.fetch_balances(
            "https://u:p@bridge.simplefin.org/simplefin",
            session=FakeSession(FakeResponse(payload=source)),
        )


def test_custom_currency_is_a_nonfinancial_bucket_without_fetching_its_url():
    source = payload(accounts=[account(currency="https://points.example/units")])
    session = FakeSession(FakeResponse(payload=source))

    result = simplefin.fetch_balances(
        "https://u:p@bridge.simplefin.org/simplefin", session=session
    )

    assert result["accounts"][0]["currency"] == "NONFINANCIAL"
    assert len(session.calls) == 1


def test_alias_file_maps_hash_to_safe_local_label(tmp_path, monkeypatch):
    first = simplefin.fetch_balances(
        "https://u:p@bridge.simplefin.org/simplefin",
        session=FakeSession(FakeResponse(payload=payload())),
    )
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({first["accounts"][0]["id"]: "Household checking"}))
    monkeypatch.setenv("HOMEHQ_FINANCE_ALIASES_FILE", str(aliases))

    second = simplefin.fetch_balances(
        "https://u:p@bridge.simplefin.org/simplefin",
        session=FakeSession(FakeResponse(payload=payload())),
    )

    assert second["accounts"][0]["label"] == "Household checking"


def test_provider_errors_are_sanitized_to_fixed_categories():
    raw_secret = "Bank login for Jane Doe broke: hunter2"
    source = payload(
        errlist=[
            {"code": "con.auth", "msg": raw_secret, "conn_id": "conn-1"},
            {"code": "act.failed", "msg": raw_secret, "account_id": "acct-1"},
            {"code": "new.secret", "msg": raw_secret},
        ]
    )

    result = simplefin.fetch_balances(
        "https://u:p@bridge.simplefin.org/simplefin",
        session=FakeSession(FakeResponse(payload=source)),
    )

    assert result["complete"] is False
    assert result["warnings"] == [
        "connection_authentication",
        "account_unavailable",
        "provider_warning",
    ]
    assert raw_secret not in repr(result)


@pytest.mark.parametrize(
    "status,message",
    [
        (402, "subscription requires attention"),
        (403, "authentication failed"),
        (500, "temporarily unavailable"),
    ],
)
def test_http_errors_have_safe_fixed_messages(status, message):
    response = FakeResponse(status=status, body=b"secret bank response")

    with pytest.raises(simplefin.SimpleFINError, match=message) as caught:
        simplefin.fetch_balances(
            "https://u:p@bridge.simplefin.org/simplefin",
            session=FakeSession(response),
        )

    assert "secret" not in str(caught.value)
    assert response.closed


def test_http_error_is_classified_without_consuming_provider_error_body():
    class ExplodingErrorResponse(FakeResponse):
        def iter_content(self, chunk_size=65536):
            raise AssertionError("provider error body must not be consumed")
            yield b"unreachable"

    response = ExplodingErrorResponse(status=403)

    with pytest.raises(simplefin.SimpleFINError, match="authentication failed"):
        simplefin.fetch_balances(
            "https://u:p@bridge.simplefin.org/simplefin",
            session=FakeSession(response),
        )

    assert response.closed


def test_network_exception_is_not_exposed():
    raw = "DNS failed for secret-host.internal"

    with pytest.raises(simplefin.SimpleFINError, match="temporarily unavailable") as caught:
        simplefin.fetch_balances(
            "https://u:p@bridge.simplefin.org/simplefin",
            session=FakeSession(error=requests.RequestException(raw)),
        )

    assert raw not in str(caught.value)


def test_response_larger_than_two_mib_is_rejected_and_closed():
    response = FakeResponse(chunks=[b"x" * (1024 * 1024), b"y" * (1024 * 1024 + 1)])

    with pytest.raises(simplefin.SimpleFINError, match="invalid data"):
        simplefin.fetch_balances(
            "https://u:p@bridge.simplefin.org/simplefin",
            session=FakeSession(response),
        )

    assert response.closed


@pytest.mark.parametrize(
    "claim_url",
    [
        "http://bridge.simplefin.org/simplefin/claim/token",
        "https://evil.example/simplefin/claim/token",
        "https://bridge.simplefin.org/not-claim/token",
        "https://bridge.simplefin.org/simplefin/claim/",
        "https://bridge.simplefin.org/simplefin/claim/token?query=yes",
    ],
)
def test_claim_rejects_unsafe_decoded_urls(claim_url):
    token = base64.b64encode(claim_url.encode()).decode()
    session = FakeSession(FakeResponse(body=b"https://u:p@bridge.simplefin.org/simplefin"))

    with pytest.raises(simplefin.SimpleFINError, match="invalid setup token"):
        simplefin.claim_token(token, session=session)

    assert session.calls == []


def test_claim_decodes_strict_base64_and_validates_returned_access_url():
    claim_url = "https://bridge.simplefin.org/simplefin/claim/one-time"
    token = base64.b64encode(claim_url.encode()).decode()
    response = FakeResponse(body=b"https://alice:secret@bridge.simplefin.org/simplefin")
    session = FakeSession(response)

    result = simplefin.claim_token(token, session=session)

    assert result == "https://alice:secret@bridge.simplefin.org/simplefin"
    assert session.calls == [
        (
            "post",
            claim_url,
            {
                "allow_redirects": False,
                "timeout": (5, 30),
                "stream": True,
                "verify": True,
            },
        )
    ]
    assert response.closed


def test_claim_rejects_redirect_and_malformed_base64_safely():
    with pytest.raises(simplefin.SimpleFINError, match="invalid setup token"):
        simplefin.claim_token("%%%not-base64%%%", session=FakeSession())

    token = base64.b64encode(
        b"https://bridge.simplefin.org/simplefin/claim/one-time"
    ).decode()
    with pytest.raises(simplefin.SimpleFINError, match="setup token could not be claimed"):
        simplefin.claim_token(token, session=FakeSession(FakeResponse(status=302)))


def test_transport_encoding_error_cannot_expose_credentials():
    import traceback
    secret = 'SECRET-credential'
    transport = FakeSession(error=UnicodeEncodeError('latin-1', secret + '😀', 17, 18, secret))
    with pytest.raises(simplefin.SimpleFINError) as caught:
        simplefin.fetch_balances('https://u:p@bridge.simplefin.org/simplefin', session=transport)
    assert secret not in ''.join(traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__))


def test_display_names_strip_controls_and_mask_spaced_account_numbers():
    assert simplefin._display_name("Bank\n account 1234-5678 9012") == "Bank account ••••"
    assert len(simplefin._display_name("A" * 1000)) == 80
    assert simplefin._display_name(None) == ""
