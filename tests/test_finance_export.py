"""CSV exports: splits as rows, no private metadata, spreadsheet-formula safe (spec §8.3)."""
import csv
import io
from datetime import date

import finance_export as E
import finance_ledger as L
from test_finance_budget import ledger_db, cat, CARD_H, CARD_S


def rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_transactions_export_rows_per_allocation(tmp_path):
    conn = ledger_db(tmp_path)
    t = L.create_txn(conn, account_id=CARD_H, txn_date='2026-10-04', amount='-200', description='TARGET', actor='h')
    L.classify(conn, t, kind='expense', allocations=[
        dict(category_id=cat(conn, 'Groceries and household essentials'), person='shared', amount='-150'),
        dict(category_id=cat(conn, 'Heather personal'), person='Heather', amount='-50')], actor='h')
    L.create_txn(conn, account_id=CARD_H, txn_date='2026-10-05', amount='-9', description='MYSTERY', actor='h')
    L.create_txn(conn, account_id=CARD_H, txn_date='2026-11-05', amount='-9', description='NOVEMBER', actor='h')
    out = rows(E.transactions_csv(conn, '2026-10'))
    assert [(r['merchant'], r['category'], r['person'], r['allocation_amount']) for r in out] == [
        ('TARGET', 'Groceries and household essentials', 'shared', '-150.00'),
        ('TARGET', 'Heather personal', 'Heather', '-50.00'), ('MYSTERY', '', '', '')]
    assert out[0]['account'] == 'Heather card' and out[0]['amount'] == '-200.00'
    assert not {'source', 'fingerprint', 'account_id', 'source_txn_hash'} & set(out[0])


def test_links_are_listed(tmp_path):
    conn = ledger_db(tmp_path)
    hotel = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-02', amount='-500', description='HOTEL', actor='s')
    L.classify(conn, hotel, kind='expense', allocations=[dict(category_id=cat(conn, 'Travel'), person='shared', amount='-500')], actor='s')
    refund = L.create_txn(conn, account_id=CARD_S, txn_date='2026-10-09', amount='500', description='HOTEL', actor='s')
    L.link(conn, kind='refund', from_id=refund, to_id=hotel, actor='s')
    out = {r['id']: r for r in rows(E.transactions_csv(conn, '2026-10'))}
    assert out[str(refund)]['linked_to'] == f'refund:{hotel}'


def test_formula_cells_are_neutralized(tmp_path):
    conn = ledger_db(tmp_path)
    L.create_txn(conn, account_id=CARD_H, txn_date='2026-10-04', amount='-5', description='=HYPERLINK("x")', actor='h')
    out = rows(E.transactions_csv(conn, '2026-10'))
    assert out[0]['merchant'].startswith("'=") and out[0]['amount'] == '-5.00'


def test_categories_export_includes_remaining(tmp_path):
    conn = ledger_db(tmp_path)
    out = {r['category']: r for r in rows(E.categories_csv(conn, '2026-10', date(2026, 10, 15)))}
    assert out['Shared dining and entertainment']['target'] == '650.00'
    assert out['Shared dining and entertainment']['remaining'] == '650.00'
    assert out['Groceries and household essentials']['target'] == ''
    assert out['Mortgage']['parent'] == 'Housing and fixed obligations'
