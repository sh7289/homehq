"""Local household classifications and immutable, dated balance snapshots."""
import json
import re
import uuid
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext

from finance_store import FinanceStoreError, _utc, _iso, _parse_iso, _stored_balance, STALE_AFTER, MANUAL_STALE_AFTER

BUCKETS = ('liquid', 'illiquid', 'debt', 'unassigned')
ASSET_KINDS = {'': '', 'real_estate': 'Real estate', 'vehicle': 'Vehicle', 'other': 'Other'}
# (id, name, bucket, position). Vehicles shares position 4 with "Other illiquid
# assets" (tiebreak is id, so it sorts right after) rather than reassigning any
# existing group's id/position -- group_id 6 is hardcoded elsewhere as the
# "Needs grouping" ungrouped inbox and must keep that id on every install.
DEFAULT_GROUPS = [(1,'Cash & spending','liquid',1), (2,'Accessible investments','liquid',2),
                  (3,'Retirement','illiquid',3), (4,'Other illiquid assets','illiquid',4),
                  (5,'Debts','debt',5), (6,'Needs grouping','unassigned',6),
                  (7,'Vehicles','illiquid',4)]

@contextmanager
def _transaction(conn):
    nested=conn.in_transaction
    marker='book_'+uuid.uuid4().hex
    conn.execute('SAVEPOINT '+marker if nested else 'BEGIN IMMEDIATE')
    try:
        yield
        conn.execute("RELEASE "+marker) if nested else conn.commit()
    except BaseException:
        if nested:
            conn.execute("ROLLBACK TO "+marker)
            conn.execute("RELEASE "+marker)
        else:
            conn.rollback()
        raise


def is_initialized(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {'finance_saved_snapshots', 'finance_payments', 'finance_groups'} <= tables


def initialize(conn):
    with _transaction(conn):
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(finance_accounts)')}
        additions = {'provider_name':"TEXT NOT NULL DEFAULT ''", 'institution':"TEXT NOT NULL DEFAULT ''",
                     'nickname':"TEXT NOT NULL DEFAULT ''", 'owner':"TEXT NOT NULL DEFAULT ''",
                     'group_id':'INTEGER NOT NULL DEFAULT 6', 'position':'INTEGER NOT NULL DEFAULT 0',
                     'included':'INTEGER NOT NULL DEFAULT 1', 'source':"TEXT NOT NULL DEFAULT 'provider'",
                     'debt_sign':"TEXT NOT NULL DEFAULT 'unconfirmed'", 'asset_kind':"TEXT NOT NULL DEFAULT ''"}
        for name, definition in additions.items():
            if name not in columns:
                conn.execute('ALTER TABLE finance_accounts ADD COLUMN '+name+' '+definition)
        if 'nickname' not in columns:
            for row in conn.execute('SELECT id,label FROM finance_accounts').fetchall():
                if not re.fullmatch(r'Account [0-9A-F]{8}',row['label']):
                    conn.execute('UPDATE finance_accounts SET nickname=? WHERE id=?',(row['label'],row['id']))
        conn.execute('CREATE TABLE IF NOT EXISTS finance_groups (id INTEGER PRIMARY KEY, name TEXT NOT NULL, bucket TEXT NOT NULL, position INTEGER NOT NULL)')
        for group_id,name,bucket,position in DEFAULT_GROUPS:
            conn.execute('INSERT OR IGNORE INTO finance_groups VALUES (?,?,?,?)',(group_id,name,bucket,position))
        conn.execute('CREATE TABLE IF NOT EXISTS finance_saved_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL, actor TEXT NOT NULL, complete INTEGER NOT NULL, payload TEXT NOT NULL)')
        import finance_payments
        finance_payments.initialize(conn)


def _text(value, required=False):
    if not isinstance(value,str) or len(value)>80 or any(ord(c)<32 or ord(c)==127 for c in value) or (required and not value.strip()):
        raise FinanceStoreError('Enter a valid name of at most 80 characters.')
    return value.strip()


def _integer(value):
    if isinstance(value,bool) or not re.fullmatch(r'-?\d{1,9}',str(value)):
        raise FinanceStoreError('Finance ordering or selection is invalid.')
    return int(value)


def _group(conn,group_id):
    group_id=_integer(group_id)
    if not conn.execute('SELECT 1 FROM finance_groups WHERE id=?',(group_id,)).fetchone():
        raise FinanceStoreError('Select an existing finance group.')
    return group_id


def _account(conn,account_id):
    row=conn.execute('SELECT * FROM finance_accounts WHERE id=?',(account_id,)).fetchone()
    if row is None:
        raise FinanceStoreError('Finance account was not found.')
    return row


def update_account(conn,account_id,*,nickname,owner,group_id,position,included,debt_sign):
    nickname,owner=_text(nickname),_text(owner)
    position=_integer(position)
    if type(included) is not bool or debt_sign not in ('unconfirmed','positive','negative'):
        raise FinanceStoreError('Finance account settings are invalid.')
    with _transaction(conn):
        row=_account(conn,account_id)
        if row['source']=='manual' and not nickname:
            raise FinanceStoreError('Manual accounts require a name.')
        group_id=_group(conn,group_id)
        conn.execute('UPDATE finance_accounts SET nickname=?,owner=?,group_id=?,position=?,included=?,debt_sign=? WHERE id=?',(nickname,owner,group_id,position,int(included),debt_sign,account_id))


def save_group(conn,*,group_id=None,name,bucket,position):
    name=_text(name,True); position=_integer(position)
    if bucket not in BUCKETS:
        raise FinanceStoreError('Select a valid finance bucket.')
    with _transaction(conn):
        if group_id is None:
            return conn.execute('INSERT INTO finance_groups(name,bucket,position) VALUES (?,?,?)',(name,bucket,position)).lastrowid
        group_id=_group(conn,group_id)
        if group_id == 6 and bucket != 'unassigned':
            raise FinanceStoreError('The ungrouped inbox must remain unassigned. Move accounts to another section instead.')
        conn.execute('UPDATE finance_groups SET name=?,bucket=?,position=? WHERE id=?',(name,bucket,position,group_id))
        return group_id


def _asset_kind(value):
    if value not in ASSET_KINDS:
        raise FinanceStoreError('Select a valid asset type.')
    return value


def _manual_values(balance,balance_at):
    if not isinstance(balance,str) or len(balance)>100 or not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)',balance):
        raise FinanceStoreError('Enter a finite decimal balance.')
    try:
        value=Decimal(balance)
        if not value.is_finite() or value.copy_abs()>Decimal('1e24') or value.as_tuple().exponent < -18 or len(value.as_tuple().digits)>42:
            raise ValueError
        if not isinstance(balance_at,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',balance_at):
            raise ValueError
        day=date.fromisoformat(balance_at)
        if day>_utc().date():
            raise ValueError
    except (ValueError,InvalidOperation):
        raise FinanceStoreError('Enter a valid balance and a date that is not in the future.') from None
    return str(value), day.isoformat()+'T00:00:00Z'


def add_manual(conn,*,nickname,currency,balance,balance_at,owner,group_id,position=0,asset_kind=''):
    from simplefin import ISO_CURRENCIES
    nickname=_text(nickname,True); owner=_text(owner); position=_integer(position)
    asset_kind=_asset_kind(asset_kind)
    if currency not in ISO_CURRENCIES:
        raise FinanceStoreError('Select a supported currency.')
    balance,balance_at=_manual_values(balance,balance_at)
    account_id='manual-'+uuid.uuid4().hex
    with _transaction(conn):
        group_id=_group(conn,group_id)
        conn.execute("INSERT INTO finance_accounts(id,label,currency,balance,balance_at,observed_at,missing,nickname,owner,group_id,position,source,asset_kind) VALUES (?,?,?,?,?,?,0,?,?,?,?,'manual',?)",(account_id,nickname,currency,balance,balance_at,_iso(_utc()),nickname,owner,group_id,position,asset_kind))
    return account_id


def update_manual(conn,account_id,*,balance,balance_at,asset_kind):
    balance,balance_at=_manual_values(balance,balance_at)
    asset_kind=_asset_kind(asset_kind)
    with _transaction(conn):
        if _account(conn,account_id)['source']!='manual':
            raise FinanceStoreError('Connected balances can only be updated by sync.')
        conn.execute('UPDATE finance_accounts SET balance=?,balance_at=?,observed_at=?,asset_kind=? WHERE id=?',(balance,balance_at,_iso(_utc()),asset_kind,account_id))


def view(conn,now=None):
    current=_utc(now)
    # A deferred read transaction provides one consistent worksheet across queries.
    own=not conn.in_transaction
    if own: conn.execute('BEGIN')
    try:
        return _view(conn,current)
    finally:
        if own: conn.rollback()


def _view(conn,current):
    groups=[dict(r) for r in conn.execute('SELECT * FROM finance_groups ORDER BY position,id')]
    sections=[dict(group=g,accounts=[],totals={}) for g in groups]
    by_group={s['group']['id']:s for s in sections}
    summary={bucket:{} for bucket in BUCKETS}
    coverage=dict(missing=0,stale=0,unassigned=0,excluded=0,unconfirmed_debt=0)
    accounts=[]; blocked_debt=set(); blocked_sections=set()
    with localcontext() as ctx:
        ctx.prec=80
        for row in conn.execute('SELECT * FROM finance_accounts ORDER BY position,nickname,label,id'):
            a=dict(row)
            a['balance']=_stored_balance(a['balance'])
            for key in ('missing','included'): a[key]=bool(a[key])
            a['stale']=current-_parse_iso(a['balance_at'])>(MANUAL_STALE_AFTER if a['source']=='manual' else STALE_AFTER)
            a['nonfinancial']=a['currency']=='NONFINANCIAL'
            a['label']=a['nickname'] or a['provider_name'] or a['label']
            section=by_group[a['group_id']]; bucket=section['group']['bucket']
            section['accounts'].append(a); accounts.append(a)
            coverage['excluded']+=not a['included']
            coverage['missing']+=a['missing']
            coverage['stale']+=a['stale']
            coverage['unassigned']+=a['included'] and bucket=='unassigned'
            unconfirmed=a['included'] and bucket=='debt' and a['debt_sign']=='unconfirmed'
            coverage['unconfirmed_debt']+=unconfirmed
            if unconfirmed and not a['missing'] and not a['nonfinancial']:
                blocked_debt.add(a['currency'])
                blocked_sections.add((a['group_id'],a['currency']))
            if not a['included'] or a['missing'] or a['nonfinancial']: continue
            currency=a['currency']; value=a['balance']
            if unconfirmed: continue
            if bucket=='debt' and a['debt_sign']=='negative': value=-value
            section['totals'][currency]=section['totals'].get(currency,Decimal(0))+value
            summary[bucket][currency]=summary[bucket].get(currency,Decimal(0))+value
        for currency in blocked_debt: summary['debt'].pop(currency,None)
        for group_id,currency in blocked_sections: by_group[group_id]['totals'].pop(currency,None)
    status=conn.execute('SELECT * FROM finance_sync_status WHERE singleton=1').fetchone()
    complete=bool(accounts) and not any(a['included'] and (a['missing'] or a['stale'] or a['nonfinancial'] or by_group[a['group_id']]['group']['bucket']=='unassigned' or (by_group[a['group_id']]['group']['bucket']=='debt' and a['debt_sign']=='unconfirmed')) for a in accounts)
    if any(a['source']=='provider' and a['included'] for a in accounts):
        complete=complete and bool(status and status['status']=='success')
    import finance_payments
    payments=finance_payments.view(conn,now=current)
    return dict(payments=payments,accounts=accounts,groups=groups,sections=sections,summary=summary,coverage=coverage,complete=complete,last_attempt=dict(at=status['last_attempt_at'],status=status['status']) if status else None,last_success=status['last_success_at'] if status else None,warnings=json.loads(status['warnings']) if status else [])


def _encode(value):
    if isinstance(value,Decimal): return {'__decimal__':str(value)}
    raise TypeError('Unsupported snapshot value')


def _decode(value):
    if set(value)=={'__decimal__'}: return _stored_balance(value['__decimal__'])
    return value


def save_snapshot(conn,*,actor,now=None):
    actor=_text(actor,True); captured=_iso(_utc(now))
    with _transaction(conn):
        payload=view(conn,now=_parse_iso(captured))
        return conn.execute('INSERT INTO finance_saved_snapshots(captured_at,actor,complete,payload) VALUES (?,?,?,?)',(captured,actor,int(payload['complete']),json.dumps(payload,default=_encode))).lastrowid


def list_snapshots(conn):
    return [dict(id=r['id'],captured_at=r['captured_at'],actor=r['actor'],complete=bool(r['complete'])) for r in conn.execute('SELECT id,captured_at,actor,complete FROM finance_saved_snapshots ORDER BY id DESC')]


def _scope(view):
    return {(a['id'],a['currency'],a['group_id'],a['included'],a['missing'],a['debt_sign'],next(g['bucket'] for g in view['groups'] if g['id']==a['group_id'])) for a in view['accounts']}


def snapshot(conn,snapshot_id):
    snapshot_id=_integer(snapshot_id)
    row=conn.execute('SELECT * FROM finance_saved_snapshots WHERE id=?',(snapshot_id,)).fetchone()
    if not row: raise FinanceStoreError('Finance snapshot was not found.')
    result=json.loads(row['payload'],object_hook=_decode)
    previous=conn.execute('SELECT id,payload FROM finance_saved_snapshots WHERE id<? ORDER BY id DESC LIMIT 1',(snapshot_id,)).fetchone()
    result.update(id=row['id'],captured_at=row['captured_at'],actor=row['actor'],previous_id=previous['id'] if previous else None,previous_complete=None,changes=[],scope_changed=False)
    if previous:
        old=json.loads(previous['payload'],object_hook=_decode)
        result['scope_changed']=_scope(result)!=_scope(old)
        result['previous_complete']=old['complete']
        def totals(v):
            return {(s['group']['id'],c):(s['group']['name'],n) for s in v['sections'] for c,n in s['totals'].items()}
        before,after=totals(old),totals(result)
        def available(v, key, values):
            if key not in values:
                return None
            for a in v['accounts']:
                if a['included'] and (a['group_id'], a['currency']) == key:
                    bucket = next(g['bucket'] for g in v['groups'] if g['id'] == a['group_id'])
                    if a['missing'] or a['nonfinancial'] or (bucket == 'debt' and a['debt_sign'] == 'unconfirmed'):
                        return None
            return values[key][1]
        with localcontext() as ctx:
            ctx.prec=80
            for key in sorted(set(before)|set(after)):
                name=(after[key] if key in after else before[key])[0]
                current=available(result,key,after)
                prior=available(old,key,before)
                delta=current-prior if current is not None and prior is not None else None
                result['changes'].append(dict(group_id=key[0],group_name=name,currency=key[1],current=current,previous=prior,delta=delta))
    return result
