"""Bounded projection of one media download Job into reader problems."""
from typing import Any, TYPE_CHECKING
from oko.storage.publication_owners import owner_expr

if TYPE_CHECKING:
    from oko.storage.store import Store


def candidates_many(store: 'Store', job_ids: list[int], now: float, *, include_subtitles: bool = False, include_completed: bool = False, include_suppressed: bool = False) -> dict[int,list[dict[str, Any]]]:
    subtitle_filter="" if include_subtitles else " AND m.resource_key IS DISTINCT FROM 'youtube:subtitles'"
    error_filter="TRUE" if include_completed else "(j.error IS NOT NULL OR j.problem_code IS NOT NULL)"
    rows=store.db.execute(f"""SELECT j.id,j.kind,j.item,j.status,j.error,j.problem_code,j.feed_id,m.publication_id,m.resource_key,
        coalesce({owner_expr('legacy_item')},{owner_expr('p')},{owner_expr('root')},j.feed_id) AS owner,
        coalesce(legacy_item.viewed,p.viewed) AS viewed,coalesce(root.id,m.publication_id,j.item) AS affected, f.kind AS feed_kind
        FROM jobs j LEFT JOIN media_files m ON m.id=j.item LEFT JOIN publications p ON p.id=m.publication_id
        LEFT JOIN publications legacy_item ON legacy_item.id=j.item AND (j.problem_code IS NULL OR j.problem_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')) AND m.resource_key IS DISTINCT FROM 'youtube:subtitles'
        LEFT JOIN publication_links link ON link.target_publication_id=coalesce(legacy_item.id,p.id)
        LEFT JOIN publications root ON root.id=link.source_publication_id AND root.viewed=0
        LEFT JOIN feeds f ON f.id=coalesce({owner_expr('p')},{owner_expr('root')},j.feed_id)
        WHERE j.id=ANY(%s) AND j.kind='download_media'{subtitle_filter} AND {error_filter} ORDER BY j.id,root.id""",(job_ids,)).fetchall()
    results: dict[tuple[int,str],dict[str,Any]]={}
    seen_objects: dict[tuple[int,str],set[str]]={}
    for row in rows:
        if not isinstance(row['item'],str) or not row['item']:
            continue  # Preserve an unaddressable legacy Job rather than inventing a material ID.
        code=row['problem_code']
        if row['status'] not in ('pending','running','failed','unavailable','blocked') and not (include_completed and row['status']=='done' and row['error'] is None and row['problem_code'] is None):continue
        if code in ('codex-choice','ai-permission','youtube-admission','processor-config') or row['viewed']:continue
        code=code or 'work-download_media'
        feed=row['owner'] or row['item'] or 'demo'
        if not include_suppressed and code in ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'):
            needle=store.db.execute("SELECT 1 FROM reader_problems WHERE identity LIKE 'profile:%%' AND finished IS NULL AND (body::jsonb->'objects') @> jsonb_build_array(jsonb_build_object('kind','feed','id',%s::text)) LIMIT 1",(feed,)).fetchone()
            if needle:continue
        item=row['item'] or ''
        shared=code.startswith('source-')
        identity='job:'+code+':'+feed+':download_media'+('' if shared else ':'+item)
        group=(row['id'],identity)
        if group not in results:
            body: dict[str,Any]=dict(id=identity,code=code,section='object',title='Не удалось загрузить вложение',
                consequence='Сохранённое содержимое остаётся доступным.',next_step='Включите обработку, чтобы повторить попытку.' if row['status']=='blocked' else 'Повторите загрузку вручную.' if row['status'] in ('done','failed') else 'Проверьте доступность источника.' if row['status']=='unavailable' else 'Око повторит попытку автоматически.',
                objects=[],action=None,operation='producer:download_media',media_owner=feed,_code_present=row['problem_code'] is not None or (row['error'] is not None and row['resource_key']!='youtube:subtitles'),_subtitle=row['resource_key']=='youtube:subtitles',media_file_id=item,media_orphan=row['publication_id'] is None,created_at=now,state='retrying' if row['status']=='running' or not row['error'] else 'current',finished_at=None,outcome=None,next_at=None)
            if code=='work-download_media':body['media_implicit_code']=row['problem_code'] in (None,'')
            if row['problem_code'] is None and row['resource_key']!='youtube:subtitles':body['media_null_event']=True
            if row['problem_code'] in ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'):body['media_profile_event']=True
            if row['resource_key']=='youtube:subtitles':body.update(subtitle_event=True,subtitle_wait_on_next=row['problem_code'] is None)
            if row['error']:body['reason']=row['error']
            if code=='source-reconnect':body.update(section='action',next_step='Проверьте привязку источника; доступ не изменяется автоматически.')
            results[group]=body
            seen_objects[group]=set()
        legacy_feed=row['feed_id'] or row['item'] or 'demo'
        legacy_identity='job:'+code+':'+legacy_feed+':download_media'+('' if shared else ':'+item)
        if legacy_identity!=identity:results[group]['_legacy_id']=legacy_identity
        obj=dict(kind='material',id=row['affected'])
        if row['affected'] not in seen_objects[group]:
            seen_objects[group].add(row['affected'])
            results[group]['objects'].append(obj)
    grouped: dict[int,list[dict[str,Any]]]={}
    for (job_id,_),body in results.items():grouped.setdefault(job_id,[]).append(body)
    return grouped


def candidates(store: 'Store', job_id: int, now: float) -> list[dict[str, Any]]:
    return candidates_many(store,[job_id],now).get(job_id,[])


def managed_candidates(store: 'Store', job_id: int, now: float) -> list[dict[str, Any]]:
    """Only per-publication failures with explicit, non-shared codes."""
    rows=candidates_many(store,[job_id],now,include_subtitles=True).get(job_id,[])
    excluded={'codex-choice','ai-permission','youtube-admission','processor-config'}
    profiles={'identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'}
    return [dict({k:v for k,v in row.items() if k not in ('_code_present','_subtitle')},media_job_id=str(job_id)) for row in rows
            if (row['_code_present'] or row['_subtitle']) and row['code'] and row['code'] not in excluded and (row['code'] not in profiles or row.get('media_profile_event') or row.get('subtitle_event')) and not row['code'].startswith('source-')]


def managed_candidates_many(store: 'Store', job_ids: list[int], now: float, *, include_subtitles: bool = False, include_suppressed: bool = False) -> dict[int,list[dict[str, Any]]]:
    excluded={'codex-choice','ai-permission','youtube-admission','processor-config'}
    profiles={'identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'}
    groups=candidates_many(store,job_ids,now,include_subtitles=include_subtitles,include_suppressed=include_suppressed)
    return {job_id:[dict({k:v for k,v in row.items() if k not in ('_code_present','_subtitle')},media_job_id=str(job_id)) for row in rows if (row['_code_present'] or row['_subtitle']) and row['code'] and row['code'] not in excluded and (row['code'] not in profiles or row.get('media_profile_event') or row.get('subtitle_event')) and not row['code'].startswith('source-')] for job_id,rows in groups.items()}


def previous_candidates(store: 'Store', job_id: int, now: float) -> list[dict[str, Any]]:
    """Capture a prior GET-owned error before an event changes its owner."""
    excluded={'codex-choice','ai-permission','youtube-admission','processor-config'}
    profiles={'identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'}
    rows=candidates_many(store,[job_id],now,include_subtitles=True).get(job_id,[])
    if rows and rows[0]['_subtitle']:
        owned=store.db.execute('SELECT subtitle_event_owned FROM jobs WHERE id=%s',(job_id,)).fetchone()
        if owned and owned['subtitle_event_owned']:
            # A past virtual deadline must be materialized before any writer
            # changes the Job, otherwise the occurrence observed by GET is lost.
            from oko.storage import memberships
            from oko.storage.subtitle_problems import activate_due
            memberships.lock_graph(store.db)
            cursor: tuple[float,str] | None=None
            while True:
                cursor=activate_due(store,now,job_id,cursor)
                if cursor is None:break
        elif store.youtube.status(now)['paused'] or (not rows[0]['_code_present'] and store.youtube.status(now)['next_at']>now):return []
    return [dict({k:v for k,v in row.items() if k not in ('_code_present','_subtitle')},media_job_id=str(job_id)) for row in rows
            if row['code'] not in excluded and not row['code'].startswith('source-')]
