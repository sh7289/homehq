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
