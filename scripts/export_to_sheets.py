#!/usr/bin/env python3
"""Push pantry + catalog data into a shared Google Sheet.

One-way, manually-triggered (or cron-driven) export -- not a live sync.
Each category (plus "pantry") gets its own worksheet tab, fully replaced
on every run.

Required env vars (in addition to the app's usual HOMEHQ_* config):
    HOMEHQ_GOOGLE_SERVICE_ACCOUNT_FILE - path to a Google service account JSON key
    HOMEHQ_GOOGLE_SHEET_ID             - target spreadsheet ID (from its URL)

The service account must be shared as an editor on the target spreadsheet.
"""
import os
import sys

import gspread

import db
import export_csv
from catalog_store import CatalogStore


def _write_table(spreadsheet, worksheet_title, header, rows):
    try:
        worksheet = spreadsheet.worksheet(worksheet_title)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=worksheet_title, rows=1, cols=1)
    worksheet.clear()
    worksheet.update([header] + rows, value_input_option="RAW")


def main():
    service_account_file = os.environ["HOMEHQ_GOOGLE_SERVICE_ACCOUNT_FILE"]
    sheet_id = os.environ["HOMEHQ_GOOGLE_SHEET_ID"]

    client = gspread.service_account(filename=service_account_file)
    spreadsheet = client.open_by_key(sheet_id)

    conn = db.get_connection(os.environ["HOMEHQ_DB_PATH"])
    db.init_db(conn)
    try:
        header, rows = export_csv.pantry_table(db.list_items(conn))
        _write_table(spreadsheet, "pantry", header, rows)
    finally:
        conn.close()

    catalog = CatalogStore(os.environ["HOMEHQ_CONTENT_DIR"])
    catalog.reload()
    for category in catalog.categories():
        header, rows = export_csv.catalog_table(catalog.items(category))
        _write_table(spreadsheet, category, header, rows)

    print("Export complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
