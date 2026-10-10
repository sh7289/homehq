"""Privacy-preserving SimpleFIN balance and transaction client.

``fetch_balances`` returns this normalized contract::

    {
        "accounts": [{
            "id": str,          # SHA-256 hex digest; no provider identifier
            "label": str,       # opaque default or local alias
            "currency": str,    # ISO 4217 code or "NONFINANCIAL"
            "balance": Decimal,
            "balance_at": str,  # UTC ISO-8601, ending in Z
        }],
        "warnings": [str],       # fixed safe category names
        "complete": bool,
    }

Only sanitized provider display names cross the boundary; extras, response
errors, and raw identifiers never do. ``fetch_accounts`` additionally returns
posted transactions with hashed ids and masked descriptions (see its docstring).
"""

import base64
import binascii
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote, urlsplit, urlunsplit

import requests


ALLOWED_HOSTS = frozenset({"bridge.simplefin.org", "beta-bridge.simplefin.org"})
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TOKEN_BYTES = 8192
MAX_URL_LENGTH = 4096
MAX_ABS_BALANCE = Decimal("1e24")
NONFINANCIAL = "NONFINANCIAL"

# ISO 4217 active alphabetic codes. Unknown three-letter codes and protocol
# custom-currency URLs deliberately fall into a display-only bucket.
ISO_CURRENCIES = frozenset(
    "AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND "
    "BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP "
    "COU CRC CUC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP "
    "GEL GHS GIP GMD GNF GTQ GYD HKD HNL HRK HTG HUF IDR ILS INR IQD IRR "
    "ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD "
    "LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN "
    "NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD "
    "RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP "
    "SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW "
    "UZS VED VES VND VUV WST XAF XAG XAU XBA XBB XBC XBD XCD XDR XOF XPD "
    "XPF XPT XSU XTS XUA XXX YER ZAR ZMW ZWL".split()
)


class SimpleFINError(ValueError):
    """A provider error whose string is always safe to show or log."""


_ERRORS = {
    "config": "Finance provider has invalid configuration.",
    "token": "Finance provider received an invalid setup token.",
    "claim": "Finance provider setup token could not be claimed.",
    "auth": "Finance provider authentication failed.",
    "payment": "Finance provider subscription requires attention.",
    "network": "Finance provider is temporarily unavailable.",
    "data": "Finance provider returned invalid data.",
}


def _error(category):
    return SimpleFINError(_ERRORS[category])


def _has_control(value):
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _parse_url(value, *, credentials, claim=False):
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        raise _error("config" if credentials else "token")
    if _has_control(value) or re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise _error("config" if credentials else "token")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise _error("config" if credentials else "token")
    invalid = (
        parts.scheme != "https"
        or parts.hostname not in ALLOWED_HOSTS
        or port not in (None, 443)
        or bool(parts.query)
        or bool(parts.fragment)
        or not parts.path.startswith("/")
        or _has_control(unquote(parts.path))
        or "\\" in unquote(parts.path)
        or any(segment == ".." for segment in unquote(parts.path).split("/"))
    )
    if credentials:
        invalid = invalid or not parts.username or not parts.password or claim
        invalid = invalid or _has_control(unquote(parts.username or ""))
        invalid = invalid or _has_control(unquote(parts.password or ""))
    else:
        invalid = invalid or parts.username is not None or parts.password is not None
        invalid = invalid or not re.fullmatch(r"/simplefin/claim/[^/]+", parts.path)
    if invalid:
        raise _error("config" if credentials else "token")
    return parts


def _read_response(response, limit=MAX_RESPONSE_BYTES):
    body = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > limit:
            raise _error("data") from None
    return bytes(body)


def _request(method, url, *, session=None, claiming=False, **kwargs):
    own_session = session is None
    client = session or requests.Session()
    if own_session:
        client.trust_env = False
    response = None
    try:
        response = getattr(client, method)(url, **kwargs)
        if response.status_code != 200:
            raise _status_error(response.status_code, claiming=claiming)
        return response, _read_response(response)
    except SimpleFINError:
        raise
    except (requests.RequestException, UnicodeError):
        raise _error("network") from None
    finally:
        if response is not None:
            response.close()
        if own_session:
            client.close()


def _status_error(status, *, claiming=False):
    if status == 402 and not claiming:
        return _error("payment")
    if status == 403 and not claiming:
        return _error("auth")
    if claiming:
        return _error("claim")
    return _error("network")


def _decode_json(body):
    try:
        decoded = body.decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _error("data") from None
    if not isinstance(value, dict):
        raise _error("data") from None
    return value


def _safe_string(value, *, allow_url=False):
    if not isinstance(value, str) or not value or len(value) > 1024 or _has_control(value):
        raise _error("data") from None
    if not allow_url and len(value) > 256:
        raise _error("data") from None
    return value


def _account_identity(item):
    account_id = _safe_string(item.get("id"))
    conn_id = item.get("conn_id")
    if conn_id is not None:
        conn_id = _safe_string(conn_id)
        material = "v2\0{}\0{}".format(conn_id, account_id)
    else:
        org = item.get("org")
        if not isinstance(org, dict):
            raise _error("data") from None
        selected = {}
        for key in ("domain", "id", "name", "sfin-url"):
            if key in org:
                selected[key] = _safe_string(org[key], allow_url=True)
        if "sfin-url" not in selected or not ({"domain", "name"} & set(selected)):
            raise _error("data") from None
        material = "v1\0{}\0{}".format(
            json.dumps(selected, sort_keys=True, separators=(",", ":")), account_id
        )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _connection_ids(data):
    if "errlist" not in data and "connections" not in data:
        return None
    connections = data.get("connections")
    if not isinstance(connections, list):
        raise _error("data") from None
    result = set()
    for connection in connections:
        if not isinstance(connection, dict):
            raise _error("data") from None
        for key in ("conn_id", "name", "org_id", "sfin_url"):
            _safe_string(connection.get(key), allow_url=key == "sfin_url")
        if connection["conn_id"] in result:
            raise _error("data") from None
        result.add(connection["conn_id"])
    return result


def _balance(value):
    if not isinstance(value, str) or not value or len(value) > 100:
        raise _error("data") from None
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise _error("data") from None
    if (
        not number.is_finite()
        or number.copy_abs() > MAX_ABS_BALANCE
        or number.as_tuple().exponent < -18
        or len(number.as_tuple().digits) > 40
    ):
        raise _error("data") from None
    return number


def _balance_at(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 32503680000:
        raise _error("data") from None
    try:
        moment = datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise _error("data") from None
    return moment.isoformat().replace("+00:00", "Z")


def _currency(value):
    value = _safe_string(value, allow_url=True)
    if re.fullmatch(r"[A-Z]{3}", value):
        return value if value in ISO_CURRENCIES else NONFINANCIAL
    if value.startswith("https://"):
        return NONFINANCIAL
    raise _error("data") from None


def _aliases():
    path = os.environ.get("HOMEHQ_FINANCE_ALIASES_FILE")
    if not path:
        return {}
    try:
        if os.path.islink(path) or os.path.getsize(path) > 65536:
            raise ValueError
        with open(path, "r", encoding="utf-8") as source:
            values = json.load(source)
    except (OSError, ValueError, json.JSONDecodeError):
        raise _error("config") from None
    if not isinstance(values, dict):
        raise _error("config") from None
    clean = {}
    for account_id, label in values.items():
        if not re.fullmatch(r"[0-9a-f]{64}", account_id or ""):
            raise _error("config") from None
        if not isinstance(label, str) or not label.strip() or len(label) > 80 or _has_control(label):
            raise _error("config") from None
        clean[account_id] = label.strip()
    return clean


def _warnings(data):
    result = []
    errlist = data.get("errlist", [])
    legacy = data.get("errors", [])
    if not isinstance(errlist, list) or not isinstance(legacy, list):
        raise _error("data") from None
    for item in errlist:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str):
            category = "provider_warning"
        else:
            code = item["code"]
            if code == "con.auth":
                category = "connection_authentication"
            elif code.startswith("con."):
                category = "connection_unavailable"
            elif code == "act.missingdata":
                category = "account_incomplete"
            elif code.startswith("act."):
                category = "account_unavailable"
            elif code == "gen.auth":
                category = "provider_authentication"
            else:
                category = "provider_warning"
        if category not in result:
            result.append(category)
    for _item in legacy:
        if "provider_warning" not in result:
            result.append("provider_warning")
    return result


def _display_name(value):
    """Keep a short hint, masking account-number-like digit sequences."""
    if not isinstance(value, str):
        return ""
    value = " ".join("".join(c if ord(c) >= 32 and ord(c) != 127 else " " for c in value).split())
    value = re.sub(r"\d(?:[ -]?\d){3,}", "••••", value)
    return value[:80]


def _normalize(data):
    source_accounts = data.get("accounts")
    if not isinstance(source_accounts, list):
        raise _error("data") from None
    aliases = _aliases()
    connection_ids = _connection_ids(data)
    institutions = {c["conn_id"]: _display_name(c.get("name")) for c in data.get("connections", [])}
    accounts = []
    seen = set()
    for item in source_accounts:
        if not isinstance(item, dict):
            raise _error("data") from None
        if connection_ids is not None and item.get("conn_id") not in connection_ids:
            raise _error("data") from None
        account_id = _account_identity(item)
        if account_id in seen:
            raise _error("data") from None
        seen.add(account_id)
        accounts.append(
            {
                "id": account_id,
                "label": aliases.get(account_id, "Account {}".format(account_id[:8].upper())),
                "provider_name": _display_name(item.get("name")),
                "institution": (_display_name(item["org"].get("name")) if isinstance(item.get("org"), dict) else "") or institutions.get(item.get("conn_id"), ""),
                "currency": _currency(item.get("currency")),
                "balance": _balance(item.get("balance")),
                "balance_at": _balance_at(item.get("balance-date")),
            }
        )
    warnings = _warnings(data)
    return {"accounts": accounts, "warnings": warnings, "complete": not warnings}


def _fetch_accounts_json(access_url, params, session):
    parts = _parse_url(access_url, credentials=True)
    username = unquote(parts.username)
    password = unquote(parts.password)
    host = parts.hostname + (":443" if parts.port == 443 else "")
    root = urlunsplit(("https", host, parts.path.rstrip("/"), "", ""))
    response, body = _request(
        "get",
        root + "/accounts",
        session=session,
        auth=(username, password),
        params=params,
        allow_redirects=False,
        timeout=(5, 30),
        stream=True,
        verify=True,
    )
    return _decode_json(body)


def fetch_balances(access_url, session=None):
    """Fetch and normalize a balance-only account set."""
    return _normalize(_fetch_accounts_json(access_url, {"version": "2", "balances-only": "1"}, session))


def transaction_hash(account_id, provider_txn_id):
    """Opaque per-account identity for one provider transaction."""
    return hashlib.sha256("sfin-txn\0{}\0{}".format(account_id, provider_txn_id).encode("utf-8")).hexdigest()


def _mask_description(value):
    value = " ".join("".join(c if ord(c) >= 32 and ord(c) != 127 else " " for c in value).split())
    return re.sub(r"\d(?:[ -]?\d){3,}", "••••", value)[:200]


def _txn_day(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 32503680000:
        raise ValueError
    return datetime.fromtimestamp(value, timezone.utc).date().isoformat()


def _transaction(account_id, item):
    """One posted transaction as masked, hashed fields, or None when still pending."""
    if not isinstance(item, dict):
        raise ValueError
    if item.get("pending") is True or item.get("posted") == 0:
        return None
    provider_id = item.get("id")
    if not isinstance(provider_id, str) or not provider_id or len(provider_id) > 200:
        raise ValueError
    posted = _txn_day(item.get("posted"))
    transacted = item.get("transacted_at")
    amount = item.get("amount")
    if not isinstance(amount, str) or len(amount) > 40:
        raise ValueError
    try:
        number = Decimal(amount)
    except InvalidOperation:
        raise ValueError from None
    if not number.is_finite() or number.copy_abs() >= Decimal("1e12") or number != number.quantize(Decimal("0.01")):
        raise ValueError
    texts = [item.get(key) for key in ("description", "payee")]
    if not any(isinstance(text, str) and text.strip() for text in texts):
        raise ValueError
    description = next(_mask_description(text) for text in texts if isinstance(text, str) and text.strip())
    return {
        "id_hash": transaction_hash(account_id, provider_id),
        "txn_date": _txn_day(transacted) if transacted else posted,
        "posted_date": posted,
        "amount": number.quantize(Decimal("0.01")),
        "description": description,
    }


def _transactions(data, accounts):
    """Posted transactions keyed by hashed account id; one bad row marks its account incomplete."""
    feed = {}
    for item, normalized in zip(data["accounts"], accounts):
        rows, complete = [], isinstance(item.get("transactions"), list)
        for raw in item.get("transactions") if complete else []:
            try:
                row = _transaction(normalized["id"], raw)
            except ValueError:
                complete = False
                continue
            if row is not None:
                rows.append(row)
        feed[normalized["id"]] = {"complete": complete, "transactions": rows}
    return feed


def fetch_accounts(access_url, start_date, session=None):
    """Fetch balances plus posted transactions since ``start_date`` (YYYY-MM-DD, UTC).

    Returns ``(payload, feed)``: ``payload`` is exactly the ``fetch_balances``
    contract, and ``feed`` maps each hashed account id to
    ``{"complete": bool, "transactions": [...]}``. Transaction ids are hashed,
    descriptions are masked, and memos and pending rows are dropped.
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise _error("config") from None
    data = _fetch_accounts_json(access_url, {"version": "2", "start-date": str(int(start.timestamp()))}, session)
    payload = _normalize(data)
    return payload, _transactions(data, payload["accounts"])


def claim_token(token, session=None):
    """Claim one setup token and return a validated credential-bearing URL."""
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_BYTES:
        raise _error("token") from None
    try:
        raw = token.encode("ascii")
        raw += b"=" * (-len(raw) % 4)
        claim_url = base64.b64decode(raw, validate=True).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error, ValueError):
        raise _error("token") from None
    _parse_url(claim_url, credentials=False, claim=True)
    response, body = _request(
        "post",
        claim_url,
        session=session,
        claiming=True,
        allow_redirects=False,
        timeout=(5, 30),
        stream=True,
        verify=True,
    )
    try:
        access_url = body.decode("utf-8")
    except UnicodeDecodeError:
        raise _error("claim") from None
    _parse_url(access_url, credentials=True)
    return access_url
