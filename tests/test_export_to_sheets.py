import sys

sys.path.insert(0, "scripts")

import gspread


class _FakeWorksheet:
    def __init__(self, title):
        self.title = title
        self.cleared = False
        self.updated_values = None

    def clear(self):
        self.cleared = True

    def update(self, values, value_input_option=None):
        self.updated_values = values


class _FakeSpreadsheet:
    def __init__(self, existing_titles=()):
        self._worksheets = {title: _FakeWorksheet(title) for title in existing_titles}
        self.added_titles = []

    def worksheet(self, title):
        if title not in self._worksheets:
            raise gspread.WorksheetNotFound(title)
        return self._worksheets[title]

    def add_worksheet(self, title, rows, cols):
        ws = _FakeWorksheet(title)
        self._worksheets[title] = ws
        self.added_titles.append(title)
        return ws


def test_write_table_creates_worksheet_when_missing():
    from export_to_sheets import _write_table

    spreadsheet = _FakeSpreadsheet()

    _write_table(spreadsheet, "pantry", ["name"], [["Rice"]])

    assert "pantry" in spreadsheet.added_titles
    ws = spreadsheet.worksheet("pantry")
    assert ws.cleared
    assert ws.updated_values == [["name"], ["Rice"]]


def test_write_table_reuses_and_replaces_existing_worksheet():
    from export_to_sheets import _write_table

    spreadsheet = _FakeSpreadsheet(existing_titles=["pantry"])

    _write_table(spreadsheet, "pantry", ["name"], [["Rice"]])

    assert spreadsheet.added_titles == []
    ws = spreadsheet.worksheet("pantry")
    assert ws.cleared
    assert ws.updated_values == [["name"], ["Rice"]]
