import os
import sqlite3
import sys

import pytest

sys.path.insert(0, "scripts")

import backup


class _FakeGPG:
    """Stands in for subprocess.run calling gpg.

    Records the argv and stdin it was handed, and copies the plaintext input
    to the output path so tests can inspect what would have been encrypted.
    """

    def __init__(self):
        self.calls = []
        self.encrypted_payload = None

    def __call__(self, cmd, input=None, check=False, **kwargs):
        self.calls.append({"cmd": cmd, "input": input})
        out_path = cmd[cmd.index("--output") + 1]
        in_path = cmd[-1]
        with open(in_path, "rb") as f:
            self.encrypted_payload = f.read()
        with open(out_path, "wb") as f:
            f.write(b"GPG-ENCRYPTED:" + self.encrypted_payload)
        return None


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE pantry_items (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO pantry_items (name) VALUES ('black beans')")
    conn.commit()
    return conn


def test_backup_writes_encrypted_file_and_removes_plaintext(tmp_path):
    db_path = tmp_path / "pantry.db"
    _make_db(str(db_path))
    dest = tmp_path / "backups"
    gpg = _FakeGPG()

    result = backup.create_backup(
        str(db_path), str(dest), passphrase="hunter2", runner=gpg
    )

    assert os.path.exists(result)
    assert result.endswith(".gpg")
    with open(result, "rb") as f:
        assert f.read().startswith(b"GPG-ENCRYPTED:")
    leftovers = [p for p in os.listdir(dest) if not p.endswith(".gpg")]
    assert leftovers == [], f"plaintext snapshot left behind: {leftovers}"


def test_backup_snapshot_contains_live_rows(tmp_path):
    db_path = tmp_path / "pantry.db"
    conn = _make_db(str(db_path))
    dest = tmp_path / "backups"
    gpg = _FakeGPG()

    backup.create_backup(str(db_path), str(dest), passphrase="hunter2", runner=gpg)

    # The bytes handed to gpg must be a real SQLite file holding the row that
    # was only in the WAL, not an empty or torn copy.
    snapshot = tmp_path / "roundtrip.db"
    with open(snapshot, "wb") as f:
        f.write(gpg.encrypted_payload)
    restored = sqlite3.connect(str(snapshot))
    names = [r[0] for r in restored.execute("SELECT name FROM pantry_items")]
    assert names == ["black beans"]
    conn.close()


def test_backup_passphrase_is_never_in_argv(tmp_path):
    db_path = tmp_path / "pantry.db"
    _make_db(str(db_path))
    gpg = _FakeGPG()

    backup.create_backup(
        str(db_path), str(tmp_path / "backups"), passphrase="hunter2", runner=gpg
    )

    argv = gpg.calls[0]["cmd"]
    assert "hunter2" not in argv
    assert not any("hunter2" in str(arg) for arg in argv)
    assert gpg.calls[0]["input"] == b"hunter2"


def test_backup_refuses_destination_inside_repo(tmp_path):
    """The catalog auto-commit stages the repo dir, so a backup written there
    would be pushed to GitHub."""
    db_path = tmp_path / "pantry.db"
    _make_db(str(db_path))
    repo_dir = tmp_path / "homehq"
    os.makedirs(repo_dir)

    with pytest.raises(ValueError, match="repo"):
        backup.create_backup(
            str(db_path),
            str(repo_dir / "backups"),
            passphrase="hunter2",
            repo_dir=str(repo_dir),
            runner=_FakeGPG(),
        )


def test_backup_requires_a_passphrase(tmp_path):
    db_path = tmp_path / "pantry.db"
    _make_db(str(db_path))

    with pytest.raises(ValueError, match="passphrase"):
        backup.create_backup(
            str(db_path), str(tmp_path / "backups"), passphrase="", runner=_FakeGPG()
        )


def test_prune_keeps_newest_backups(tmp_path):
    dest = tmp_path / "backups"
    os.makedirs(dest)
    for day in range(1, 6):
        with open(dest / f"pantry-2026-09-0{day}T000000Z.db.gpg", "w") as f:
            f.write("x")

    backup.prune_backups(str(dest), keep=3)

    remaining = sorted(os.listdir(dest))
    assert remaining == [
        "pantry-2026-09-03T000000Z.db.gpg",
        "pantry-2026-09-04T000000Z.db.gpg",
        "pantry-2026-09-05T000000Z.db.gpg",
    ]
