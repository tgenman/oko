"""Bounded projection of one publication fetch Job into reader problems."""
from typing import Any, TYPE_CHECKING
from oko.storage.publication_owners import owner_expr

if TYPE_CHECKING:
    from oko.storage.store import Store


def candidates_many(store: 'Store', job_ids: list[int], now: float, *, include_suppressed: bool = False, include_youtube: bool = True) -> dict[int,list[dict[str, Any]]]:
    rows=store.db.execute(f"""SELECT j.id,j.kind,j.item,j.status,j.error,j.problem_code,j.feed_id,
        coalesce({owner_expr('p')},{owner_expr('root')},j.feed_id) AS owner,
        p.viewed,coalesce(root.id,j.item) AS affected, f.kind AS feed_kind
        FROM jobs j LEFT JOIN publications p ON p.id=j.item
        LEFT JOIN publication_links link ON link.target_publication_id=p.id
        LEFT JOIN publications root ON root.id=link.source_publication_id AND root.viewed=0
        LEFT JOIN feeds f ON f.id=coalesce({owner_expr('p')},{owner_expr('root')},j.feed_id)
        WHERE j.id=ANY(%s) AND j.kind='fetch' AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL) ORDER BY j.id,root.id""",(job_ids,)).fetchall()
    youtube_jobs={row['id'] for row in rows if row['feed_kind']=='youtube'}
    profile_codes=('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')
    feeds=sorted({row['owner'] or row['item'] or 'demo' for row in rows if row['problem_code'] in profile_codes and isinstance(row['owner'] or row['item'] or 'demo',str)})
    suppressed: set[str]=set()
    if feeds and not include_suppressed:
        for start in range(0,len(feeds),100):
            found=store.db.execute("""SELECT f.feed FROM unnest(%s::text[]) f(feed) WHERE EXISTS (
                SELECT 1 FROM reader_problems WHERE identity LIKE 'profile:%%' AND finished IS NULL
                AND (body::jsonb->'objects') @> jsonb_build_array(jsonb_build_object('kind','feed','id',f.feed)) LIMIT 1)""",(feeds[start:start+100],))
            suppressed.update(row['feed'] for row in found)
    results: dict[tuple[int,str],dict[str,Any]]={}
    for row in rows:
        if not isinstance(row['item'],str) or not row['item']:
            continue  # Preserve an unaddressable legacy Job rather than inventing a material ID.
        code=row['problem_code']
        if row['status'] not in ('pending','running','failed','unavailable','blocked') and not (row['status']=='done' and code and row['error']):continue
        if code in ('codex-choice','ai-permission','youtube-admission','processor-config') or row['viewed']:continue
        if not include_youtube and code is None and row['feed_kind']=='youtube' and store.youtube.ready_at()>now:continue
        code=code or 'work-fetch'
        feed=row['owner'] or row['item'] or 'demo'
        if code in profile_codes and feed in suppressed:continue
        item=row['item'] or ''
        shared=code.startswith('source-')
        identity='job:'+code+':'+feed+':fetch'+('' if shared else ':'+item)
        group=(row['id'],identity)
        if group not in results:
            body: dict[str,Any]=dict(id=identity,code=code,section='object',title='Не удалось загрузить материал',
                consequence='Сохранённое содержимое остаётся доступным.',next_step='Включите обработку, чтобы повторить попытку.' if row['status']=='blocked' else 'Повторите загрузку вручную.' if row['status'] in ('done','failed') else 'Проверьте доступность источника.' if row['status']=='unavailable' else 'Око повторит попытку автоматически.',
                objects=[],action=None,operation='producer:fetch',fetch_owner=feed,_code_present=row['problem_code'] is not None or (row['error'] is not None and (include_youtube or row['id'] not in youtube_jobs)),created_at=now,state='retrying' if row['status']=='running' or not row['error'] else 'current',finished_at=None,outcome=None,next_at=None)
            if code=='work-fetch':body['fetch_implicit_code']=row['problem_code'] in (None,'')
            if row['problem_code'] is None and row['feed_kind']=='youtube':body['fetch_youtube_event']=True
            if row['error']:body['reason']=row['error']
            if code=='source-reconnect':body.update(section='action',next_step='Проверьте привязку источника; доступ не изменяется автоматически.')
            results[group]=body
        legacy_feed=row['feed_id'] or row['item'] or 'demo'
        legacy_identity='job:'+code+':'+legacy_feed+':fetch'+('' if shared else ':'+item)
        if legacy_identity!=identity:results[group]['_legacy_id']=legacy_identity
        obj=dict(kind='material',id=row['affected'])
        if obj not in results[group]['objects']:results[group]['objects'].append(obj)
    grouped: dict[int,list[dict[str,Any]]]={}
    for (job_id,_),body in results.items():grouped.setdefault(job_id,[]).append(body)
    return grouped


def candidates(store: 'Store', job_id: int, now: float) -> list[dict[str, Any]]:
    return candidates_many(store,[job_id],now).get(job_id,[])


def managed_candidates(store: 'Store', job_id: int, now: float) -> list[dict[str, Any]]:
    """Only per-publication failures with explicit, non-shared codes."""
    rows=candidates(store,job_id,now)
    excluded={'codex-choice','ai-permission','youtube-admission','processor-config','profile_not_configured','profile_unavailable','account_identity_mismatch'}
    return [dict({k:v for k,v in row.items() if k!='_code_present'},fetch_job_id=str(job_id)) for row in rows
            if row['_code_present'] and row['code'] and row['code'] not in excluded and not row['code'].startswith('source-')]


def managed_candidates_many(store: 'Store', job_ids: list[int], now: float, *, include_suppressed: bool = False, include_youtube: bool = True) -> dict[int,list[dict[str, Any]]]:
    excluded={'codex-choice','ai-permission','youtube-admission','processor-config','profile_not_configured','profile_unavailable','account_identity_mismatch'}
    groups=candidates_many(store,job_ids,now,include_suppressed=include_suppressed,include_youtube=include_youtube)
    return {job_id:[dict({k:v for k,v in row.items() if k!='_code_present'},fetch_job_id=str(job_id)) for row in rows if row['_code_present'] and row['code'] and row['code'] not in excluded and not row['code'].startswith('source-')] for job_id,rows in groups.items()}


def previous_candidates(store: 'Store', job_id: int, now: float) -> list[dict[str, Any]]:
    """Capture the prior GET-owned NULL error when an event changes its code."""
    rows=candidates(store,job_id,now)
    flag=store.db.execute("SELECT fetch_youtube_sensitive FROM jobs WHERE id=%s AND kind='fetch'",(job_id,)).fetchone() if any(row.get('fetch_youtube_event') for row in rows) else None
    if flag and flag['fetch_youtube_sensitive']:
        from oko.storage import memberships
        from oko.storage.youtube_fetch_problems import activate_due
        memberships.lock_graph(store.db)
        # Both the graph and ownership can change while the lock is awaited.
        locked_flag=store.db.execute("SELECT fetch_youtube_sensitive FROM jobs WHERE id=%s AND kind='fetch'",(job_id,)).fetchone()
        if locked_flag and locked_flag['fetch_youtube_sensitive']:
            cursor: tuple[float,str] | None=None
            while True:
                cursor=activate_due(store,now,job_id,cursor)
                if cursor is None:break
            rows=candidates(store,job_id,now)
        else:
            rows=candidates_many(store,[job_id],now,include_youtube=False).get(job_id,[])
    elif flag:
        rows=candidates_many(store,[job_id],now,include_youtube=False).get(job_id,[])
    excluded={'codex-choice','ai-permission','youtube-admission','processor-config','profile_not_configured','profile_unavailable','account_identity_mismatch'}
    eligible=[dict({k:v for k,v in row.items() if k!='_code_present'},fetch_job_id=str(job_id)) for row in rows
              if row['code'] not in excluded and not row['code'].startswith('source-')]
    if eligible and any(not row['_code_present'] or row.get('fetch_implicit_code') is True for row in rows):
        job=store.db.execute('SELECT item FROM jobs WHERE id=%s',(job_id,)).fetchone()
        if job:
            for row in eligible:row['_fetch_item']=job['item']
    return eligible


def cleanup_null_protection(store: 'Store', cursor: str | None) -> tuple[str | None,bool]:
    """Collect only verified markers whose exact occurrence no longer remains active."""
    import hashlib,json
    from oko.storage.problems import _readable_timestamp
    query="SELECT key,value FROM state WHERE key COLLATE \"C\">='fetch-null-protected:' AND key COLLATE \"C\"<'fetch-null-protected;'"
    params: tuple[Any,...]=()
    if cursor is not None:
        query+=' AND key COLLATE "C">%s';params=(cursor,)
    rows=store.db.execute(query+' ORDER BY key COLLATE "C" LIMIT 100',params).fetchall()
    if not rows:return None,True
    verified=[]
    for row in rows:
        try:value=json.loads(row['value'])
        except (ValueError,TypeError):continue
        if (not isinstance(value,list) or len(value)!=3 or value[0]!='oko-fetch-null-v2'
            or not isinstance(value[1],str) or not isinstance(value[2],str)
            or row['key']!='fetch-null-protected:'+hashlib.sha256(value[1].encode()).hexdigest()):continue
        verified.append((row['key'],row['value'],value[1],value[2]))
    current={row['id']:row for row in store.db.execute('SELECT id,identity,finished,outcome FROM reader_problems WHERE id=ANY(%s)',([v[2] for v in verified],))} if verified else {}
    stale=[(key,value) for key,value,identity,canonical in verified if identity not in current or current[identity]['identity']!=canonical or (_readable_timestamp(current[identity]['finished']) and current[identity]['outcome'] in ('resolved','irrelevant'))]
    if stale:
        store.db.execute('DELETE FROM state s USING unnest(%s::text[],%s::text[]) v(key,value) WHERE s.key=v.key AND s.value=v.value',([v[0] for v in stale],[v[1] for v in stale]))
    return rows[-1]['key'],False
