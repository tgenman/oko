"""Bounded projection of one publication fetch Job into reader problems."""
from typing import Any, TYPE_CHECKING
from oko.storage.publication_owners import owner_expr

if TYPE_CHECKING:
    from oko.storage.store import Store


def candidates_many(store: 'Store', job_ids: list[int], now: float) -> dict[int,list[dict[str, Any]]]:
    rows=store.db.execute(f"""SELECT j.id,j.kind,j.item,j.status,j.error,j.problem_code,j.feed_id,
        coalesce({owner_expr('p')},{owner_expr('root')},j.feed_id) AS owner,
        p.viewed,coalesce(root.id,j.item) AS affected, f.kind AS feed_kind
        FROM jobs j LEFT JOIN publications p ON p.id=j.item
        LEFT JOIN publication_links link ON link.target_publication_id=p.id
        LEFT JOIN publications root ON root.id=link.source_publication_id AND root.viewed=0
        LEFT JOIN feeds f ON f.id=coalesce({owner_expr('p')},{owner_expr('root')},j.feed_id)
        WHERE j.id=ANY(%s) AND j.kind='fetch' AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL) ORDER BY j.id,root.id""",(job_ids,)).fetchall()
    results: dict[tuple[int,str],dict[str,Any]]={}
    for row in rows:
        if not isinstance(row['item'],str) or not row['item']:
            continue  # Preserve an unaddressable legacy Job rather than inventing a material ID.
        code=row['problem_code']
        if row['status'] not in ('pending','running','failed','unavailable','blocked') and not (row['status']=='done' and code and row['error']):continue
        if code in ('codex-choice','ai-permission','youtube-admission','processor-config') or row['viewed']:continue
        if code is None and row['feed_kind']=='youtube' and store.youtube.ready_at()>now:continue
        code=code or 'work-fetch'
        feed=row['owner'] or row['item'] or 'demo'
        if code in ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'):
            needle=store.db.execute("SELECT 1 FROM reader_problems WHERE identity LIKE 'profile:%%' AND finished IS NULL AND (body::jsonb->'objects') @> jsonb_build_array(jsonb_build_object('kind','feed','id',%s::text)) LIMIT 1",(feed,)).fetchone()
            if needle:continue
        item=row['item'] or ''
        shared=code.startswith('source-')
        identity='job:'+code+':'+feed+':fetch'+('' if shared else ':'+item)
        group=(row['id'],identity)
        if group not in results:
            body: dict[str,Any]=dict(id=identity,code=code,section='object',title='Не удалось загрузить материал',
                consequence='Сохранённое содержимое остаётся доступным.',next_step='Включите обработку, чтобы повторить попытку.' if row['status']=='blocked' else 'Повторите загрузку вручную.' if row['status'] in ('done','failed') else 'Проверьте доступность источника.' if row['status']=='unavailable' else 'Око повторит попытку автоматически.',
                objects=[],action=None,operation='producer:fetch',fetch_owner=feed,created_at=now,state='retrying' if row['status']=='running' or not row['error'] else 'current',finished_at=None,outcome=None,next_at=None)
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
    excluded={'codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'}
    return [dict(row,fetch_job_id=str(job_id)) for row in rows
            if row['code'] and row['code']!='work-fetch' and row['code'] not in excluded and not row['code'].startswith('source-')]


def managed_candidates_many(store: 'Store', job_ids: list[int], now: float) -> dict[int,list[dict[str, Any]]]:
    excluded={'codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'}
    groups=candidates_many(store,job_ids,now)
    return {job_id:[dict(row,fetch_job_id=str(job_id)) for row in rows if row['code'] and row['code']!='work-fetch' and row['code'] not in excluded and not row['code'].startswith('source-')] for job_id,rows in groups.items()}
