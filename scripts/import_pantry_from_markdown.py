#!/usr/bin/env python3
"""One-time/reusable bootstrap: import a bullet-list markdown pantry
inventory (## section headers, "-   item" bullets) into the pantry DB.

No quantities are assumed to exist in the source doc -- every item is
added with quantity 1 (adjust from the web UI afterwards) and its section
header as the location, so the original grouping isn't lost.

Usage:
    HOMEHQ_DB_PATH=... python3 scripts/import_pantry_from_markdown.py path/to/pantry_inventory.md
"""
import os
import re
import sys

import db

_SECTION_RE = re.compile(r"^##\s+(.*)")
_BULLET_RE = re.compile(r"^-\s+(.*)")

# Sections that hold commentary rather than pantry items.
_NON_ITEM_SECTIONS = {"notes"}


def parse_items(markdown_text):
    """Yield (name, location) for each bullet under a "## Section" header."""
    section = None
    for line in markdown_text.splitlines():
        section_match = _SECTION_RE.match(line)
        if section_match:
            section = section_match.group(1).strip()
            continue
        bullet_match = _BULLET_RE.match(line.strip())
        if bullet_match and section and section.lower() not in _NON_ITEM_SECTIONS:
            name = bullet_match.group(1).strip()
            yield name, section


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} path/to/pantry_inventory.md", file=sys.stderr)
        return 1

    markdown_path = sys.argv[1]
    with open(markdown_path, encoding="utf-8") as f:
        text = f.read()

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)

    count = 0
    for name, location in parse_items(text):
        db.add_item(conn, name=name, quantity=1, unit="", location=location)
        count += 1
    conn.close()

    print(f"Imported {count} pantry items from {markdown_path}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
