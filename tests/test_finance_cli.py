import base64
import os
import stat
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import finance_store


ROOT = Path(__file__).resolve().parents[1]


def test_setup_reserves_private_file_before_claim_and_never_prints_secret(tmp_path):
    output = tmp_path / "private" / "simplefin.env"
    observed = {}
    access_url = "https://alice:topsecret@bridge.simplefin.org/simplefin"

    def claim(token):
        observed["exists_during_claim"] = output.exists()
        observed["mode_during_claim"] = stat.S_IMODE(output.stat().st_mode)
        observed["token"] = token
        return access_url

    from scripts import setup_simplefin

    messages = []
    code = setup_simplefin.main(
        ["--output", str(output)],
        token_reader=lambda prompt: "one-time-token",
        claim=claim,
        printer=messages.append,
        repo_dir=str(ROOT),
    )

    assert code == 0
    assert observed == {
        "exists_during_claim": True,
        "mode_during_claim": 0o600,
        "token": "one-time-token",
    }
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert output.read_text() == f'HOMEHQ_SIMPLEFIN_ACCESS_URL="{access_url}"\n'
    assert "topsecret" not in " ".join(messages)


def test_setup_refuses_overwrite_and_does_not_claim(tmp_path):
    output = tmp_path / "simplefin.env"
    output.write_text("existing")
    from scripts import setup_simplefin

    claimed = []
    messages = []
    code = setup_simplefin.main(
        ["--output", str(output)],
        token_reader=lambda prompt: "token",
        claim=lambda token: claimed.append(token),
        printer=messages.append,
        repo_dir=str(ROOT),
    )

    assert code == 2
    assert claimed == []
    assert output.read_text() == "existing"
    assert messages == ["Setup failed safely; the output file was not changed."]


def test_setup_rejects_output_inside_repo_without_prompting(tmp_path):
    from scripts import setup_simplefin

    prompted = []
    code = setup_simplefin.main(
        ["--output", str(ROOT / "secret.env")],
        token_reader=lambda prompt: prompted.append(prompt),
        printer=lambda message: None,
        repo_dir=str(ROOT),
    )

    assert code == 2
    assert prompted == []


def run_sync(tmp_path, *args, env_extra=None):
    env = os.environ.copy()
    env["HOMEHQ_FINANCE_DB_PATH"] = str(tmp_path / "private" / "finance.db")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_finance.py"), *args],
        cwd=str(tmp_path),
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_demo_sync_is_deterministic_and_needs_no_credential(tmp_path):
    result = run_sync(tmp_path, "--demo")

    assert result.returncode == 0
    assert result.stdout == "Finance sync completed.\n"
    assert result.stderr == ""
    conn = finance_store.connect(str(tmp_path / "private" / "finance.db"))
    data = finance_store.dashboard(conn)
    assert data["totals"]["by_currency"] == {"USD": Decimal("3284.56")}
    assert data["last_attempt"] == {
        "at": "2026-09-07T12:00:00Z",
        "status": "success",
    }
    assert [a["label"] for a in data["accounts"]] == [
        "Demo checking",
        "Demo savings",
    ]


def test_live_sync_requires_credential_without_loading_dotenv(tmp_path):
    (tmp_path / ".env").write_text(
        "HOMEHQ_SIMPLEFIN_ACCESS_URL=https://u:p@bridge.simplefin.org/simplefin\n"
    )
    result = run_sync(tmp_path, env_extra={"HOMEHQ_SIMPLEFIN_ACCESS_URL": ""})

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "Finance sync configuration is incomplete.\n"


def test_demo_refuses_to_overwrite_store_marked_live(tmp_path):
    db = tmp_path / "private" / "finance.db"
    conn = finance_store.connect(str(db))
    finance_store.set_store_mode(conn, "live")
    conn.close()

    result = run_sync(tmp_path, "--demo")

    assert result.returncode == 2
    assert result.stderr == "Demo sync refused because this is a live finance store.\n"


def test_sync_lock_contention_exits_safely(tmp_path):
    import fcntl

    lock_path = tmp_path / "private" / "finance.db.lock"
    lock_path.parent.mkdir(mode=0o700)
    lock = open(lock_path, "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = run_sync(tmp_path, "--demo")
    finally:
        lock.close()

    assert result.returncode == 2
    assert result.stderr == "Finance sync is already running.\n"


def _live_db(tmp_path):
    from test_finance_budget import ledger_db
    conn = ledger_db(tmp_path)
    finance_store.set_store_mode(conn, "live")
    conn.close()
    return {"HOMEHQ_FINANCE_DB_PATH": str(tmp_path / "private" / "finance.db"),
            "HOMEHQ_SIMPLEFIN_ACCESS_URL": "https://u:p@bridge.simplefin.org/simplefin"}


def _balances():
    from test_finance_budget import CHECKING, CARD_S, CARD_H
    return {"accounts": [{"id": i, "label": "x", "currency": "USD", "balance": Decimal("5"),
                          "balance_at": "2026-11-02T00:00:00Z"} for i in (CHECKING, CARD_S, CARD_H)],
            "warnings": [], "complete": True}


def test_live_sync_stores_posted_transactions_for_budget_accounts(tmp_path):
    from datetime import date
    from scripts import sync_finance
    from test_finance_budget import CARD_S

    asked = []

    def fetch_feed(credential, start):
        asked.append(start)
        return _balances(), {CARD_S: {"complete": True, "transactions": [dict(
            id_hash="1" * 64, txn_date="2026-10-05", posted_date="2026-10-05", amount=Decimal("-42.10"),
            description="TRADER JOES")]}}

    out = []
    code = sync_finance.main([], environ=_live_db(tmp_path), fetch_feed=fetch_feed, today=date(2026, 10, 9),
                             stdout=type("W", (), {"write": lambda self, t: out.append(t)})())
    assert code == 0 and asked == ["2026-10-01"]
    conn = finance_store.connect(str(tmp_path / "private" / "finance.db"))
    assert conn.execute("SELECT amount FROM finance_txns").fetchone()["amount"] == "-42.10"
    assert conn.execute("SELECT balance FROM finance_accounts WHERE id=?", (CARD_S,)).fetchone()["balance"] == "5"


def test_transaction_failure_keeps_balances_and_exits_nonzero(tmp_path, monkeypatch):
    from datetime import date
    import finance_bank_feed
    from scripts import sync_finance
    from test_finance_budget import CARD_S

    def broken(*args, **kwargs):
        raise finance_store.FinanceStoreError("boom")

    monkeypatch.setattr(finance_bank_feed, "record", broken)
    errors = []
    code = sync_finance.main([], environ=_live_db(tmp_path), fetch_feed=lambda c, s: (_balances(), {}),
                             today=date(2026, 10, 9), stderr=type("W", (), {"write": lambda self, t: errors.append(t)})())
    assert code == 1
    assert "transactions could not be recorded" in "".join(errors)
    conn = finance_store.connect(str(tmp_path / "private" / "finance.db"))
    assert conn.execute("SELECT balance FROM finance_accounts WHERE id=?", (CARD_S,)).fetchone()["balance"] == "5"
