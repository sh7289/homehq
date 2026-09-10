from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3
import pytest
import finance_book as book
import finance_store as store

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)

def conn(tmp_path):
    return store.connect(str(tmp_path / 'private' / 'finance.db'))

def sync(db, balance='100', **extra):
    account = dict(id='a'*64, label='Account AAAAAAAA', currency='USD', balance=Decimal(balance), balance_at='2026-09-07T12:00:00Z')
    account.update(extra)
    store.record_sync(db, dict(accounts=[account], warnings=[], complete=True), now=NOW)

def edit(db, **changes):
    args = dict(nickname='Our checking', owner='Household', group_id=1, position=0, included=True, debt_sign='unconfirmed')
    args.update(changes)
    book.update_account(db, 'a'*64, **args)

def test_metadata_survives_sync_and_migration(tmp_path):
    db = conn(tmp_path)
    sync(db, provider_name='Checking', institution='Bank')
    edit(db)
    sync(db, '200', provider_name='New checking', institution='New bank')
    book.initialize(db)
    row = book.view(db, now=NOW)['accounts'][0]
    assert (row['label'], row['provider_name'], row['owner'], row['balance']) == ('Our checking', 'New checking', 'Household', Decimal('200'))
    assert len(book.view(db)['groups']) == 7

def test_manual_precision_currency_and_coverage(tmp_path):
    db = conn(tmp_path)
    for currency, balance in [('USD','999999999999999999999999.1234567890123456'),('USD','0.000000000000000001'),('EUR','3')]:
        book.add_manual(db,nickname='Asset',currency=currency,balance=balance,balance_at='2026-09-07',owner='',group_id=1)
    result = book.view(db,now=NOW)
    assert result['summary']['liquid'] == {'USD':Decimal('999999999999999999999999.123456789012345601'),'EUR':Decimal('3')}
    assert result['complete']
    assert all(a['source']=='manual' for a in result['accounts'])
    # Manual balances get a much longer staleness window than daily-synced ones.
    assert book.view(db,now=NOW+timedelta(days=3))['coverage']['stale']==0
    assert book.view(db,now=NOW+timedelta(days=181))['coverage']['stale']==3

def test_manual_staleness_window_differs_from_synced_accounts(tmp_path):
    db=conn(tmp_path); sync(db)
    book.add_manual(db,nickname='Home',currency='USD',balance='500000',balance_at='2026-09-07',owner='',group_id=4)
    just_under=book.view(db,now=NOW+timedelta(hours=47))['accounts']
    assert not next(a for a in just_under if a['source']=='provider')['stale']
    assert not next(a for a in just_under if a['source']=='manual')['stale']
    past_sync_window=book.view(db,now=NOW+timedelta(hours=49))['accounts']
    assert next(a for a in past_sync_window if a['source']=='provider')['stale']
    assert not next(a for a in past_sync_window if a['source']=='manual')['stale']
    past_manual_window=book.view(db,now=NOW+timedelta(days=181))['accounts']
    assert next(a for a in past_manual_window if a['source']=='manual')['stale']

def test_default_groups_include_vehicles_without_disturbing_inbox_id(tmp_path):
    db=conn(tmp_path)
    groups={g['id']:g for g in book.view(db)['groups']}
    assert groups[7]==dict(id=7,name='Vehicles',bucket='illiquid',position=4)
    assert groups[6]['name']=='Needs grouping' and groups[6]['bucket']=='unassigned'
    with pytest.raises(store.FinanceStoreError):
        book.save_group(db,group_id=6,name='Cash',bucket='liquid',position=1)

def test_asset_kind_is_cosmetic_and_validated(tmp_path):
    db=conn(tmp_path)
    account_id=book.add_manual(db,nickname='Home',currency='USD',balance='500000',balance_at='2026-09-07',owner='',group_id=4,asset_kind='real_estate')
    assert book.view(db)['accounts'][0]['asset_kind']=='real_estate'
    book.update_manual(db,account_id,balance='510000',balance_at='2026-09-07',asset_kind='vehicle')
    assert book.view(db)['accounts'][0]['asset_kind']=='vehicle'
    with pytest.raises(store.FinanceStoreError):
        book.add_manual(db,nickname='Bad',currency='USD',balance='1',balance_at='2026-09-07',owner='',group_id=4,asset_kind='boat')

def test_missing_excluded_unassigned_and_debt(tmp_path):
    db=conn(tmp_path); sync(db,'-10'); edit(db,group_id=5)
    assert book.view(db,now=NOW)['coverage']['unconfirmed_debt']==1
    assert not book.view(db,now=NOW)['summary']['debt']
    edit(db,group_id=5,debt_sign='negative')
    assert book.view(db,now=NOW)['summary']['debt']=={'USD':Decimal('10')}
    store.record_sync(db,dict(accounts=[],warnings=[],complete=True),now=NOW)
    result=book.view(db,now=NOW)
    assert result['coverage']['missing']==1 and not result['summary']['debt']
    edit(db,group_id=6,included=False)
    assert book.view(db,now=NOW)['coverage']['excluded']==1

@pytest.mark.parametrize('balance,date',[('NaN','2026-09-07'),('1e25','2026-09-07'),('1','2999-01-01'),('1','2026-02-30'),('1.2345678901234567890','2026-09-07')])
def test_invalid_manual_does_not_mutate(tmp_path,balance,date):
    db=conn(tmp_path)
    with pytest.raises(store.FinanceStoreError):
        book.add_manual(db,nickname='Asset',currency='USD',balance=balance,balance_at=date,owner='',group_id=1)
    assert book.view(db)['accounts']==[]

def test_frozen_snapshots_scope_and_exact_changes(tmp_path):
    db=conn(tmp_path); sync(db); edit(db)
    first=book.save_snapshot(db,actor='Tester',now=NOW)
    sync(db,'150'); edit(db,nickname='Renamed')
    second=book.save_snapshot(db,actor='Tester',now=NOW)
    frozen=book.snapshot(db,first)
    assert frozen['accounts'][0]['label']=='Our checking'
    assert frozen['accounts'][0]['balance']==Decimal('100')
    current=book.snapshot(db,second)
    assert current['previous_id']==first and not current['scope_changed']
    assert current['changes'][0]['delta']==Decimal('50')
    edit(db,group_id=2)
    third=book.save_snapshot(db,actor='Tester',now=NOW)
    assert book.snapshot(db,third)['scope_changed']
    assert len(book.list_snapshots(db))==3

def test_legacy_migration_preserves_alias(tmp_path):
    db=sqlite3.connect(':memory:'); db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE finance_accounts(id TEXT PRIMARY KEY,label TEXT,currency TEXT,balance TEXT,balance_at TEXT,observed_at TEXT,missing INTEGER)')
    db.execute("INSERT INTO finance_accounts VALUES (?,?,?,?,?,?,?)",('a'*64,'Household alias','USD','4','2026-09-07T12:00:00Z','2026-09-07T12:00:00Z',0)); db.commit()
    book.initialize(db); book.initialize(db)
    assert db.execute('SELECT nickname FROM finance_accounts').fetchone()[0]=='Household alias'

def test_nested_mutations_can_be_rolled_back_together(tmp_path):
    db=conn(tmp_path)
    account_id=book.add_manual(db,nickname='Home',currency='USD',balance='12',balance_at='2026-09-07',owner='',group_id=4)
    db.execute('BEGIN IMMEDIATE')
    book.update_account(db,account_id,nickname='Changed',owner='',group_id=4,position=1,included=True,debt_sign='unconfirmed')
    with pytest.raises(store.FinanceStoreError):
        book.update_manual(db,account_id,balance='NaN',balance_at='2026-09-07',asset_kind='')
    db.rollback()
    assert book.view(db,now=NOW)['accounts'][0]['nickname']=='Home'

def test_sync_never_marks_manual_missing(tmp_path):
    db=conn(tmp_path)
    book.add_manual(db,nickname='Home',currency='USD',balance='12',balance_at='2026-09-07',owner='',group_id=4)
    sync(db)
    assert not next(a for a in book.view(db)['accounts'] if a['source']=='manual')['missing']

def test_invalid_group_mutation_is_atomic(tmp_path):
    db=conn(tmp_path); sync(db)
    with pytest.raises(store.FinanceStoreError): edit(db,group_id=999)
    assert book.view(db)['accounts'][0]['nickname']==''

def test_migration_cli_requires_existing_private_database(tmp_path):
    from scripts.migrate_finance import main
    import io
    output=io.StringIO(); errors=io.StringIO()
    path=tmp_path/'private'/'finance.db'
    assert main(['--db',str(path)],stdout=output,stderr=errors)==1
    assert not path.exists()
    db=conn(tmp_path); sync(db); db.close()
    assert main(['--db',str(path)],stdout=output,stderr=errors)==0
    assert '100' not in output.getvalue()

def test_debt_section_totals_use_confirmed_owed_convention(tmp_path):
    db=conn(tmp_path); sync(db,'-10'); edit(db,group_id=5,debt_sign='negative')
    def debt():
        return next(s for s in book.view(db,now=NOW)['sections'] if s['group']['id']==5)['totals']
    assert debt()=={'USD':Decimal('10')}
    unknown=book.add_manual(db,nickname='Other debt',currency='USD',balance='3',balance_at='2026-09-07',owner='',group_id=5)
    assert debt()=={}
    book.update_account(db,unknown,nickname='Other debt',owner='',group_id=5,position=0,included=True,debt_sign='positive')
    assert debt()=={'USD':Decimal('13')}

def test_local_blank_nickname_survives_sync_alias(tmp_path):
    db=conn(tmp_path); sync(db,label='Imported alias'); edit(db,nickname='')
    sync(db,label='Imported alias')
    assert book.view(db)['accounts'][0]['nickname']==''


def test_ungrouped_inbox_cannot_silently_classify_new_connections(tmp_path):
    db = conn(tmp_path)
    with pytest.raises(store.FinanceStoreError):
        book.save_group(db, group_id=6, name='Cash', bucket='liquid', position=1)
    sync(db)
    assert book.view(db, now=NOW)['coverage']['unassigned'] == 1
