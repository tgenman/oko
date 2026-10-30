"""Conservative classification of stored publication content for status lookup."""
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any


def content_class(value: Any) -> int:
    try:
        raw=value if isinstance(value,str) else json.dumps(value,ensure_ascii=True)
        obj=json.loads(raw,parse_float=Decimal,parse_int=Decimal)
    except (ValueError,TypeError,InvalidOperation,OverflowError):
        return 2
    if not isinstance(obj,dict):return 1
    extra=obj.get('extra')
    if not isinstance(extra,dict):return 1
    header=extra.get('_reader')
    if not isinstance(header,dict):return 1
    full=header.get('full')
    if full is None:return 1
    if isinstance(full,bool):return 1 if full else 0
    if isinstance(full,Decimal):
        if not full.is_finite():return 2
        parts=full.as_tuple()
        exponent=parts.exponent
        if not isinstance(exponent,int) or exponent<0:return 2
        digits=''.join(str(digit) for digit in parts.digits).lstrip('0') or '0'
        if digits=='0':return 0
        limit='2147483648' if parts.sign else '2147483647'
        if len(digits)+exponent>len(limit):return 2
        expanded=digits+'0'*exponent
        if len(expanded)==len(limit) and expanded>limit:return 2
        return 1
    if isinstance(full,str) and re.fullmatch(r'[ \t\n\r\f\v]*[+-]?[0-9]+[ \t\n\r\f\v]*',full):
        normalized=full.strip()
        negative=normalized.startswith('-')
        digits=normalized.lstrip('+-').lstrip('0') or '0'
        limit='2147483648' if negative else '2147483647'
        if len(digits)>len(limit) or (len(digits)==len(limit) and digits>limit):return 2
        return 0 if digits=='0' else 1
    return 2


def save_class(db: Any, item: str, content: Any) -> None:
    db.execute('UPDATE publications SET incomplete_class=%s WHERE id=%s',(content_class(content),item))


def classify_newly_eligible(db: Any, ids: list[str]) -> None:
    for start in range(0,len(ids),400):
        rows=db.execute("SELECT id,content FROM publications WHERE id=ANY(%s) AND viewed=0 AND complete=1 AND NOT source_deleted AND incomplete_class IS NULL FOR UPDATE",(ids[start:start+400],)).fetchall()
        if rows:
            db.execute('UPDATE publications p SET incomplete_class=s.class FROM unnest(%s::text[],%s::smallint[]) s(id,class) WHERE p.id=s.id',([r['id'] for r in rows],[content_class(r['content']) for r in rows]))


def recover(store: Any) -> None:
    while True:
        with store.db:
            from oko.storage.transaction import begin_write
            begin_write(store.db)
            store.db.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),%s))",(746582091,))
            if store.db.execute("SELECT 1 FROM state WHERE key='incomplete-class-recovered-v1'").fetchone():return
            rows=store.db.execute("SELECT id,content FROM publications WHERE viewed=0 AND complete=1 AND NOT source_deleted AND incomplete_class IS NULL ORDER BY id LIMIT 100 FOR UPDATE").fetchall()
            if not rows:
                store.db.execute("INSERT INTO state(key,value) VALUES ('incomplete-class-recovered-v1','true') ON CONFLICT DO NOTHING")
                return
            store.db.execute('UPDATE publications p SET incomplete_class=s.class FROM unnest(%s::text[],%s::smallint[]) s(id,class) WHERE p.id=s.id',([r['id'] for r in rows],[content_class(r['content']) for r in rows]))
        store.fault('incomplete_class_recovery_batch')
