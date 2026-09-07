"""Encrypted off-box backups of the Home HQ SQLite database.

Takes a consistent snapshot with SQLite's own backup API (safe against a live
WAL database, unlike `cp`), encrypts it with gpg symmetric AES-256, and drops
the plaintext copy. Intended to run from a systemd timer; see
deploy/homehq-backup.service.

Restore:
    gpg --decrypt pantry-2026-09-05T030000Z.db.gpg > pantry.db
    sqlite3 pantry.db "SELECT COUNT(*) FROM pantry_items;"

The passphrase must be stored somewhere OTHER than this server -- a password
manager. A backup you cannot decrypt after losing the box is not a backup.
"""

import argparse
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone

GPG_BINARY = "gpg"


def _timestamp(now=None):
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H%M%SZ")


def _snapshot(db_path, snapshot_path):
    """Copy a live SQLite database consistently, WAL contents included."""
    source = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        target = sqlite3.connect(snapshot_path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def create_backup(
    db_path,
    dest_dir,
    passphrase,
    repo_dir=None,
    runner=subprocess.run,
    now=None,
):
    """Snapshot db_path and write an encrypted copy into dest_dir.

    Returns the path of the encrypted file.
    """
    if not passphrase:
        raise ValueError(
            "A backup passphrase is required (set HOMEHQ_BACKUP_PASSPHRASE)."
        )

    if not os.path.isfile(db_path) or os.path.islink(db_path):
        raise ValueError("Backup source must be an existing regular database file.")

    dest = os.path.abspath(dest_dir)
    if os.path.realpath(dest) != dest:
        raise ValueError("Backup destination must not use symbolic links.")
    if os.path.lexists(dest):
        info = os.lstat(dest)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("Backup destination must be a private directory (0700).")
    if repo_dir:
        repo = os.path.realpath(repo_dir)
        if dest == repo or dest.startswith(repo + os.sep):
            raise ValueError(
                "Refusing to write backups into the git repo directory. "
                "Pick a destination outside the repo."
            )

    os.makedirs(dest, mode=0o700, exist_ok=True)
    stem = os.path.splitext(os.path.basename(db_path))[0]
    encrypted_path = os.path.join(dest, f"{stem}-{_timestamp(now)}.db.gpg")

    # Private workspace covers partial SQLite snapshots and gpg failures too.
    # Ciphertext is published atomically only once encryption succeeds.
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=dest) as temporary:
        snapshot_path = os.path.join(temporary, "snapshot.db")
        staged_ciphertext = os.path.join(temporary, "encrypted.gpg")
        fd = os.open(snapshot_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        _snapshot(db_path, snapshot_path)
        runner(
            [
                GPG_BINARY,
                "--batch",
                "--yes",
                "--symmetric",
                "--cipher-algo",
                "AES256",
                "--passphrase-fd",
                "0",
                "--output",
                staged_ciphertext,
                snapshot_path,
            ],
            # Passphrase goes over stdin, never argv -- argv is world-readable
            # in /proc on a shared box.
            input=passphrase.encode("utf-8"),
            check=True,
        )
        os.chmod(staged_ciphertext, 0o600)
        os.replace(staged_ciphertext, encrypted_path)

    return encrypted_path


def prune_backups(dest_dir, keep=14):
    """Retain `keep` timestamped backups per database; ignore other files."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    groups = {}
    for name in os.listdir(dest_dir):
        match = re.fullmatch(r"(.+)-\d{4}-\d{2}-\d{2}T\d{6}Z\.db\.gpg", name)
        path = os.path.join(dest_dir, name)
        if match and os.path.isfile(path) and not os.path.islink(path):
            groups.setdefault(match[1], []).append(name)
    for names in groups.values():
        for name in sorted(names)[:-keep]:
            os.remove(os.path.join(dest_dir, name))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Back up the Home HQ database.")
    parser.add_argument("--db", default=os.environ.get("HOMEHQ_DB_PATH"))
    parser.add_argument("--finance-db", default=os.environ.get("HOMEHQ_FINANCE_DB_PATH"))
    parser.add_argument("--dest", default=os.environ.get("HOMEHQ_BACKUP_DIR"))
    parser.add_argument("--keep", type=int, default=int(os.environ.get("HOMEHQ_BACKUP_KEEP", "14")))
    args = parser.parse_args(argv)

    if not args.db or not args.dest:
        parser.error("HOMEHQ_DB_PATH and HOMEHQ_BACKUP_DIR must be set (or use --db/--dest).")

    content_dir = os.environ.get("HOMEHQ_CONTENT_DIR")
    repo_dir = os.path.dirname(os.path.normpath(content_dir)) if content_dir else None

    sources = [args.db] + ([args.finance_db] if args.finance_db else [])
    stems = [Path(source).stem for source in sources]
    if len(stems) != len(set(stems)):
        parser.error("Database filenames must have distinct stems for backup retention.")
    for source in sources:
        create_backup(
            source, args.dest,
            passphrase=os.environ.get("HOMEHQ_BACKUP_PASSPHRASE", ""),
            repo_dir=repo_dir,
        )
    prune_backups(args.dest, keep=args.keep)
    print(f"Encrypted backups written: {len(sources)} database(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
