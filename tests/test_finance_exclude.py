"""The built-in "Exclude from budget" category: recorded, never counted."""
from datetime import date
from decimal import Decimal

import finance_budget
import finance_export
import finance_ledger as L
import finance_ledger_math as M
import finance_store
from test_finance_budget import ledger_db, cat, CHECKING, CARD_S

TODAY = date(2026, 10, 9)
EXCLUDED = finance_budget.EXCLUDED_NAME


def venmo_case(conn):
    """$71.99 personal charge; $250 Venmo cash-out of which $35 pays it back and $215 predates the budget."""
    charge = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-02', amount='-71.99', description='SHOP', actor='s')
    L.classify(conn, charge, kind='expense', actor='s',
               allocations=[dict(category_id=cat(conn, 'Steve personal'), person='Steve', amount='-71.99')])
    cashout = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-08', amount='250.00', description='VENMO', actor='s')
    L.classify(conn, cashout, kind='reimbursement', actor='s', allocations=[
        dict(category_id=cat(conn, 'Steve personal'), person='Steve', amount='35.00'),
        dict(category_id=cat(conn, EXCLUDED), person='shared', amount='215.00')])
    return cashout


def test_excluded_category_is_seeded_once_and_marked(tmp_path):
    conn = ledger_db(tmp_path)
    rows = conn.execute('SELECT type, excluded, active FROM finance_budget_categories WHERE name=?', (EXCLUDED,)).fetchall()
    assert [tuple(r) for r in rows] == [('other', 1, 1)]
    finance_budget.initialize(conn)
    assert conn.execute('SELECT COUNT(*) FROM finance_budget_categories WHERE excluded=1').fetchone()[0] == 1


def test_excluded_lines_count_nowhere_but_the_rest_of_the_split_does(tmp_path):
    conn = ledger_db(tmp_path)
    venmo_case(conn)
    summary = M.month_summary(conn, '2026-10', TODAY)
    personal = next(r for r in summary['categories'] if r['name'] == 'Steve personal')
    assert personal['spent'] == Decimal('36.99')
    assert EXCLUDED not in [r['name'] for r in summary['categories']]
    assert summary['known_spending']['total'] == Decimal('36.99')
    assert summary['funding']['total'] == Decimal('36.99')
    assert summary['income'] == Decimal('0')
    assert summary['uncategorized']['count'] == 0 and summary['unreviewed_inflows']['count'] == 0


def test_excluded_income_line_is_not_income(tmp_path):
    conn = ledger_db(tmp_path)
    pay = L.create_txn(conn, account_id=CHECKING, txn_date='2026-10-01', amount='1000.00', description='PAY', actor='s')
    L.classify(conn, pay, kind='income', actor='s', allocations=[
        dict(category_id=cat(conn, 'Income'), person='shared', amount='600.00'),
        dict(category_id=cat(conn, EXCLUDED), person='shared', amount='400.00')])
    assert M.month_summary(conn, '2026-10', TODAY)['income'] == Decimal('600.00')


def test_export_keeps_the_excluded_line(tmp_path):
    conn = ledger_db(tmp_path)
    venmo_case(conn)
    assert EXCLUDED in finance_export.transactions_csv(conn, '2026-10')


def test_upgrade_adds_column_to_an_existing_category_table(tmp_path):
    conn = ledger_db(tmp_path)
    conn.execute('DELETE FROM finance_budget_categories WHERE excluded=1')
    conn.execute('ALTER TABLE finance_budget_categories DROP COLUMN excluded')
    conn.execute("UPDATE finance_budget_settings SET value='4' WHERE key='seed_version'")
    conn.commit()
    conn.close()
    conn = finance_store.connect(str(tmp_path / 'private' / 'finance.db'))
    assert conn.execute('SELECT excluded FROM finance_budget_categories WHERE name=?', (EXCLUDED,)).fetchone()[0] == 1
    assert conn.execute("SELECT excluded FROM finance_budget_categories WHERE name='Mortgage'").fetchone()[0] == 0
