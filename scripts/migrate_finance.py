"""Explicitly upgrade an existing private finance database without provider access."""
import argparse
import os
from pathlib import Path
import sqlite3
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import finance_store


def main(argv=None, *, stdout=None, stderr=None):
    parser=argparse.ArgumentParser(description='Migrate an existing private finance database.')
    parser.add_argument('--db',required=True)
    args=parser.parse_args(argv)
    stdout=stdout or sys.stdout; stderr=stderr or sys.stderr
    conn=None
    try:
        path=finance_store._validate_path(args.db,str(ROOT))
        info=os.lstat(path)
        parent=os.lstat(os.path.dirname(path))
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or parent.st_mode & 0o077:
            raise finance_store.FinanceStoreError('Finance database permissions are not private.')
        # Check the existing database before connect can create an empty schema.
        from urllib.parse import quote
        conn=sqlite3.connect('file:'+quote(path,safe='/')+'?mode=rw',uri=True)
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='finance_accounts'").fetchone():
            raise finance_store.FinanceStoreError('Finance database is not initialized.')
        conn.close(); conn=None
        conn=finance_store.connect(path,repo_dir=str(ROOT))
        print('Finance database migration completed.',file=stdout)
        return 0
    except (OSError,sqlite3.Error,finance_store.FinanceStoreError):
        print('Finance database migration could not be completed.',file=stderr)
        return 1
    finally:
        if conn is not None: conn.close()

if __name__=='__main__':
    raise SystemExit(main())
