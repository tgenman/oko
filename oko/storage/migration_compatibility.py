"""Audited repair of legacy metadata in a copy; never removes source rows."""
import hashlib
import json
import re
import sqlite3
from typing import Any

TABLE='migration_retired_rows'


def encoded(value: Any) -> str:
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))


def opaque(value: str) -> bool:
    return bool(re.fullmatch(r'(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})',value or ''))


def rows(db: sqlite3.Connection, sql: str, values: tuple[Any,...]=()) -> list[dict[str,Any]]:
    cursor=db.execute(sql,values)
    names=[c[0] for c in cursor.description]
    return [dict(zip(names,row)) for row in cursor]


def terminal(db: sqlite3.Connection, table: str, row: dict[str,Any]) -> bool:
    if table=='jobs':
        owner={'fetch':'items','delete':'items','download_media':'media_files'}.get(row.get('kind',''))
        return bool(owner and row.get('status') in ('done','cancelled') and opaque(row.get('item',''))
                    and db.execute('SELECT 1 FROM '+owner+' WHERE id=?',(row['item'],)).fetchone() is None)
    if table=='history_members':
        feed=db.execute('SELECT kind,config FROM feeds WHERE id=?',(row.get('feed'),)).fetchone()
        return bool(feed and (feed[0] in ('rss','youtube') or feed[0]=='telegram' and json.loads(feed[1]).get('source_kind')=='channel')
                    and row.get('outcome') in ('loaded','viewed','incomplete','unavailable') and opaque(row.get('item',''))
                    and db.execute('SELECT 1 FROM items WHERE id=?',(row['item'],)).fetchone() is None)
    return False


def key(table: str, row: dict[str,Any]) -> str:
    return encoded([row['id']] if table=='jobs' else [row['feed'],row['item']])


def certificates(db: sqlite3.Connection) -> set[tuple[str,str]]:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(TABLE,)).fetchone():return set()
    result=set()
    for table,identity,body,digest in db.execute('SELECT source_table,source_key,row_json,sha256 FROM '+TABLE):
        if table not in ('jobs','history_members') or hashlib.sha256(body.encode()).hexdigest()!=digest:
            raise ValueError('Invalid retained migration certificate')
        original=json.loads(body)
        if not isinstance(original,dict) or key(table,original)!=identity:
            raise ValueError('Invalid retained row identity')
        current=rows(db,'SELECT * FROM '+table+(' WHERE id=?' if table=='jobs' else ' WHERE feed=? AND item=?'),tuple(json.loads(identity)))
        # New technical columns (e.g. jobs.payload) may be added by Store startup.
        if not current:continue  # Runtime may remove active history; immutable audit remains.
        if len(current)!=1 or any(name not in original and not (name=='payload' and value=='{}') for name,value in current[0].items()) or any(name not in current[0] or current[0][name]!=value for name,value in original.items()) or not terminal(db,table,current[0]):
            continue  # Audit is history, not a constraint on later valid runtime rows.
        result.add((table,identity))
    return result


def normalize(db: sqlite3.Connection) -> dict[str,Any]:
    """Only call on an isolated SQLite backup, before Store recovery."""
    import time
    from oko.storage.reader_versions import ReaderVersions
    ReaderVersions.functions(db,time.monotonic,time.time)
    restored=[]
    for row in rows(db,"SELECT f.*,b.binding FROM feeds f LEFT JOIN telegram_bindings b ON b.feed=f.id WHERE f.kind='telegram'"):
        config=json.loads(row['config'])
        if not isinstance(config,dict) or config.get('source_kind') is not None:continue
        binding=json.loads(row['binding']) if row['binding'] else None
        if not isinstance(binding,dict):continue
        name=binding.get('username','')
        if not isinstance(name,str) or not re.fullmatch('[a-zA-Z][a-zA-Z0-9_]{3,31}',name):continue
        url='https://t.me/s/'+name
        proven=(set(binding)=={'kind','username','preview_url'} and binding.get('kind')=='web' and config==binding and binding.get('preview_url')==url
                and row.get('adapter')=='telegram_web_adapter' and row.get('platform')=='telegram'
                and row.get('url')==url and row.get('telegram_username')==name
                and db.execute("SELECT 1 FROM telegram_claim_owners o JOIN telegram_source_claims c ON c.kind=o.kind AND c.value=o.value WHERE o.feed=? AND o.kind='username' AND o.value=? AND o.evidence='public preview'",(row['id'],name.lower())).fetchone() is not None
                and db.execute("SELECT count(DISTINCT feed) FROM telegram_claim_owners WHERE kind='username' AND value=?",(name.lower(),)).fetchone()[0]==1)
        if proven:
            db.execute('UPDATE feeds SET config=? WHERE id=?',(encoded({**config,'source_kind':'channel'}),row['id']))
            restored.append(dict(feed=row['id'],evidence='verified_public_preview_claim',username=name,original_config=row['config']))
    db.execute('CREATE TABLE IF NOT EXISTS '+TABLE+' (source_table TEXT,source_key TEXT,row_json TEXT,sha256 TEXT,PRIMARY KEY(source_table,source_key))')
    retained={}
    for table in ('jobs','history_members'):
        count=0
        for row in rows(db,'SELECT * FROM '+table):
            if terminal(db,table,row):
                body=encoded(row)
                db.execute('INSERT OR IGNORE INTO '+TABLE+' VALUES (?,?,?,?)',(table,key(table,row),body,hashlib.sha256(body.encode()).hexdigest()))
                count+=1
        retained[table]=count
    certificates(db)
    db.commit()
    return dict(restored_types=restored,retained=retained,policy='All rows stay in original tables; terminal orphan rows also have immutable audit copies')
