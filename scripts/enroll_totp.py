"""Offline administrator enrollment; never print authenticator material."""
import argparse
import os
from pathlib import Path
import secrets
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pyotp
from mfa_security import SCHEMA, enroll


def _private_output(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or path.exists():
        raise ValueError('Choose a new absolute output directory without symlinks.')
    if any((parent / '.git').exists() for parent in (path,) + tuple(path.parents)):
        raise ValueError('Enrollment material must remain outside Git working trees.')
    if not path.parent.is_dir():
        raise ValueError('Output parent directory must exist.')
    return path


def main(argv=None, *, confirm=input, printer=print):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slot', required=True, choices=('1','2'))
    parser.add_argument('--username', required=True)
    parser.add_argument('--db', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args(argv)
    conn = None
    output = None
    written = []
    try:
        if not args.username.strip() or any(ord(c) < 32 for c in args.username):
            raise ValueError('Invalid username.')
        configured = os.environ.get(f'HOMEHQ_USER{args.slot}_NAME')
        if configured and configured != args.username:
            raise ValueError('Username does not match the configured account slot.')
        output = _private_output(args.output_dir)
        database = Path(args.db)
        if not database.is_absolute() or not database.is_file() or database.resolve() != database:
            raise ValueError('An existing absolute pantry database path is required.')
        conn = sqlite3.connect(database.as_uri() + '?mode=rw', uri=True)
        conn.executescript(SCHEMA)
        existing = bool(conn.execute('SELECT 1 FROM mfa_enrollments WHERE username=?', (args.username,)).fetchone())
        if existing and confirm('Replace this user’s MFA enrollment and recovery codes? Type REPLACE: ') != 'REPLACE':
            raise ValueError('Enrollment replacement cancelled.')
        secret = pyotp.random_base32()
        codes = [secrets.token_urlsafe(24) for _ in range(10)]
        output.mkdir(mode=0o700)
        written.append(output)
        contents = {
            'totp.env': f'HOMEHQ_USER{args.slot}_TOTP_SECRET={secret}\n',
            'provisioning.txt': f'Manual setup secret: {secret}\nType: time-based, 6 digits, 30 seconds, SHA1\n' + pyotp.TOTP(secret).provisioning_uri(args.username, issuer_name='Home HQ') + '\n',
            'recovery-codes.txt': '\n'.join(codes) + '\n',
        }
        for name, content in contents.items():
            path = output / name
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            written.append(path)
            with os.fdopen(fd, 'w') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        enroll(conn, args.username, secret, codes, replace=existing)
        printer('Enrollment saved. Install the private environment file and configure your authenticator from the output directory.')
        return 0
    except (ValueError, OSError, sqlite3.Error, EOFError):
        for path in reversed(written):
            try:
                path.rmdir() if path.is_dir() else path.unlink()
            except OSError:
                pass
        printer('Enrollment failed safely. Check account, database, and new private output directory; existing enrollment was preserved.')
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
