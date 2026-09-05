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
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

GPG_BINARY = "gpg"


def _timestamp(now=None):
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H%M%SZ")


def _snapshot(db_path, snapshot_path):
    """Copy a live SQLite database consistently, WAL contents included."""
    source = sqlite3.connect(db_path)
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

    dest = os.path.realpath(dest_dir)
    if repo_dir:
        repo = os.path.realpath(repo_dir)
        if dest == repo or dest.startswith(repo + os.sep):
            raise ValueError(
                f"Refusing to write backups into the git repo dir ({repo}): approved "
                "catalog imports stage that directory and would push the backup to "
                "GitHub. Pick a destination outside the repo."
            )

    os.makedirs(dest, exist_ok=True)
    stem = os.path.splitext(os.path.basename(db_path))[0]
    snapshot_path = os.path.join(dest, f"{stem}-{_timestamp(now)}.db")
    encrypted_path = snapshot_path + ".gpg"

    _snapshot(db_path, snapshot_path)
    try:
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
                encrypted_path,
                snapshot_path,
            ],
            # Passphrase goes over stdin, never argv -- argv is world-readable
            # in /proc on a shared box.
            input=passphrase.encode("utf-8"),
            check=True,
        )
    finally:
        if os.path.exists(snapshot_path):
            os.remove(snapshot_path)

    return encrypted_path


def prune_backups(dest_dir, keep=14):
    """Delete all but the `keep` newest encrypted backups."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    names = sorted(n for n in os.listdir(dest_dir) if n.endswith(".gpg"))
    for name in names[:-keep]:
        os.remove(os.path.join(dest_dir, name))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Back up the Home HQ database.")
    parser.add_argument("--db", default=os.environ.get("HOMEHQ_DB_PATH"))
    parser.add_argument("--dest", default=os.environ.get("HOMEHQ_BACKUP_DIR"))
    parser.add_argument("--keep", type=int, default=int(os.environ.get("HOMEHQ_BACKUP_KEEP", "14")))
    args = parser.parse_args(argv)

    if not args.db or not args.dest:
        parser.error("HOMEHQ_DB_PATH and HOMEHQ_BACKUP_DIR must be set (or use --db/--dest).")

    content_dir = os.environ.get("HOMEHQ_CONTENT_DIR")
    repo_dir = os.path.dirname(os.path.normpath(content_dir)) if content_dir else None

    path = create_backup(
        args.db,
        args.dest,
        passphrase=os.environ.get("HOMEHQ_BACKUP_PASSPHRASE", ""),
        repo_dir=repo_dir,
    )
    prune_backups(args.dest, keep=args.keep)
    print(f"✅ Backup written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
