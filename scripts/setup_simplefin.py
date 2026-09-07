"""Claim a SimpleFIN setup token into a private environment file."""

import argparse
import getpass
import os
import json
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import simplefin


SAFE_FAILURE = "Setup failed safely; the output file was not changed."


def _inside(path, directory):
    try:
        return os.path.commonpath((path, directory)) == directory
    except ValueError:
        return False


def _reserve(path, repo_dir):
    if not os.path.isabs(path):
        raise ValueError
    path = os.path.abspath(path)
    if os.path.realpath(path) != path or _inside(path, os.path.realpath(repo_dir)) or any((parent / ".git").exists() for parent in Path(path).parents):
        raise ValueError
    parent = os.path.dirname(path)
    if not os.path.exists(parent):
        if not os.path.isdir(os.path.dirname(parent)):
            raise ValueError
        os.mkdir(parent, 0o700)
    info = os.lstat(parent)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise ValueError
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(path, flags, 0o600)


def main(
    argv=None,
    *,
    token_reader=None,
    claim=None,
    printer=print,
    repo_dir=None,
):
    parser = argparse.ArgumentParser(
        description="Claim a SimpleFIN token into a private sync environment file."
    )
    parser.add_argument("--output", required=True, help="Absolute path outside the repository")
    args = parser.parse_args(argv)
    output = os.path.abspath(args.output)
    descriptor = None
    try:
        descriptor = _reserve(args.output, repo_dir or str(ROOT))
    except (OSError, ValueError):
        printer(SAFE_FAILURE)
        return 2

    reader = token_reader or getpass.getpass
    claim_function = claim or simplefin.claim_token
    try:
        token = reader("SimpleFIN setup token (input hidden): ")
        access_url = claim_function(token)
        content = "HOMEHQ_SIMPLEFIN_ACCESS_URL={}\n".format(json.dumps(access_url))
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            descriptor = None
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(output, 0o600)
    except (EOFError, KeyboardInterrupt, OSError, simplefin.SimpleFINError, ValueError):
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(output)
        except OSError:
            pass
        printer(SAFE_FAILURE)
        return 1

    printer("SimpleFIN access was saved to the private output file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
