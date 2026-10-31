"""Experimental addressed YouTube admission contributor projection."""
from typing import Any
from oko.storage.publication_owners import owner_expr


def contributions(store: Any, ids: list[int]) -> dict[int,set[str | None]]:
    """Project selected jobs only; caller is responsible for event coverage."""
    if not ids:return {}
    special="'codex-choice','ai-permission','youtube-admission','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'"
    query=f"""SELECT j.id,CASE WHEN affected_publication.id IS NOT NULL AND affected_publication.viewed IS NULL THEN NULL ELSE coalesce(root.id,m.publication_id,j.item) END AS affected
        FROM jobs j
        LEFT JOIN publications item ON item.id=j.item
        LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
        LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
        LEFT JOIN publication_links link ON link.target_publication_id=coalesce(item.id,media_owner.id)
        LEFT JOIN publications root ON root.id=link.source_publication_id AND root.viewed=0
        LEFT JOIN publications affected_publication ON affected_publication.id=coalesce(root.id,m.publication_id,j.item)
        JOIN feeds f ON f.id=coalesce({owner_expr('item')},{owner_expr('media_owner')},{owner_expr('root')},j.feed_id)
        WHERE j.id=ANY(%s) AND j.status IN ('pending','running') AND f.kind='youtube'
        AND (affected_publication.id IS NULL OR affected_publication.viewed=0 OR affected_publication.viewed IS NULL)
        AND NOT (j.kind='fetch' AND item.id IS NULL AND j.youtube_admission_forgotten_item IS NOT NULL AND j.youtube_admission_forgotten_item=j.item)
        AND j.problem_code IS DISTINCT FROM 'processor-config'
        AND NOT (j.problem_code IS NOT NULL AND j.problem_code LIKE 'source-%%' AND j.source_event_owned)
        AND NOT (j.kind='fetch' AND j.problem_code IS NULL AND j.error IS NOT NULL AND j.fetch_null_owned)
        AND ((j.kind='fetch' AND (
              j.problem_code IN ('','work-fetch') OR
              ((j.problem_code IS NOT NULL OR j.error IS NOT NULL)
                AND (j.problem_code IS NULL OR j.problem_code LIKE 'source-%%' OR j.problem_code IN ({special}) OR j.fetch_null_ambiguous))))
          OR (j.kind='download_media' AND m.resource_key='youtube:subtitles'
              AND (j.problem_code IS NOT NULL OR j.error IS NOT NULL)))"""
    result: dict[int,set[str | None]]={}
    for row in store.db.execute(query,(ids,)):
        result.setdefault(row['id'],set()).add(row['affected'])
    return result


def sync_ids(store: Any, ids: list[int]) -> None:
    """Maintain per-job contributions for explicitly affected IDs."""
    import json
    if not ids:return
    if not store.db.in_transaction:raise RuntimeError('Admission flag update requires a transaction')
    for start in range(0,len(ids),100):
        batch=ids[start:start+100]
        store.db.execute("""UPDATE jobs j SET youtube_admission_forgotten_item=v.forgotten FROM (
            SELECT source.id,CASE
              WHEN source.kind='fetch' AND p.id IS NOT NULL AND p.viewed<>0 THEN source.item
              WHEN source.kind IS DISTINCT FROM 'fetch' OR (p.id IS NOT NULL AND p.viewed=0) OR source.item IS DISTINCT FROM source.youtube_admission_forgotten_item THEN NULL
              ELSE source.youtube_admission_forgotten_item END AS forgotten
            FROM jobs source LEFT JOIN publications p ON p.id=source.item WHERE source.id=ANY(%s)
        ) v WHERE j.id=v.id AND j.youtube_admission_forgotten_item IS DISTINCT FROM v.forgotten""",(batch,))
        projected=contributions(store,batch)
        values=[]
        for job_id in batch:
            found=projected.get(job_id,set())
            objects=sorted(found,key=lambda value:(value is not None,str(value)))
            unknown=any(not isinstance(value,str) or not value for value in found)
            values.append((job_id,bool(found),json.dumps(objects),unknown))
        store.db.execute('''UPDATE jobs j SET youtube_admission_candidate=v.active,youtube_admission_objects=v.objects::jsonb,youtube_admission_unknown=v.unknown
            FROM unnest(%s::bigint[],%s::boolean[],%s::text[],%s::boolean[]) v(id,active,objects,unknown)
            WHERE j.id=v.id AND (j.youtube_admission_candidate IS DISTINCT FROM v.active OR j.youtube_admission_objects IS DISTINCT FROM v.objects::jsonb OR j.youtube_admission_unknown IS DISTINCT FROM v.unknown)''',tuple([row[i] for row in values] for i in range(4)))


def recover_flags(store: Any) -> None:
    """Restartable one-time recovery over the active collection queue only."""
    from oko.storage.transaction import begin_write
    if store.db.execute('SELECT youtube_admission_recovered FROM youtube_access WHERE id=1').fetchone()[0]:return
    while True:
        stale=store.db.execute("SELECT id FROM jobs WHERE youtube_admission_candidate AND (kind NOT IN ('fetch','download_media') OR kind IS NULL OR status NOT IN ('pending','running') OR status IS NULL) ORDER BY id LIMIT 100").fetchall()
        if not stale:break
        with store.db:
            begin_write(store.db)
            store.db.execute("UPDATE jobs SET youtube_admission_candidate=false,youtube_admission_objects='[]'::jsonb,youtube_admission_unknown=false WHERE id=ANY(%s)",([r['id'] for r in stale],))
        store.fault('youtube_admission_jobs_recovery_batch')
    cursor: int | None=None
    while True:
        sql="SELECT id FROM jobs WHERE kind IN ('fetch','download_media') AND status IN ('pending','running')"
        if cursor is not None:sql+=' AND id>%s'
        sql+=' ORDER BY id LIMIT 100'
        rows=store.db.execute(sql,(cursor,) if cursor is not None else ()).fetchall()
        if not rows:break
        with store.db:
            begin_write(store.db)
            sync_ids(store,[r['id'] for r in rows])
        cursor=rows[-1]['id']
        store.fault('youtube_admission_jobs_recovery_batch')
    with store.db:
        store.db.execute('UPDATE youtube_access SET youtube_admission_recovered=true WHERE id=1')


def group_objects(store: Any) -> list[str]:
    """Read the indexed candidate group in bounded job pages."""
    result: set[str]=set()
    cursor: int | None=None
    while True:
        sql='SELECT id,youtube_admission_objects FROM jobs WHERE youtube_admission_candidate'
        if cursor is not None:sql+=' AND id>%s'
        sql+=' ORDER BY id LIMIT 100'
        rows=store.db.execute(sql,(cursor,) if cursor is not None else ()).fetchall()
        if not rows:break
        for row in rows:
            values=row['youtube_admission_objects']
            if not isinstance(values,list):raise ValueError('Unrecognized YouTube admission contribution')
            result.update(value for value in values if isinstance(value,str) and value)
        cursor=rows[-1]['id']
    return sorted(result)


def has_unknown_objects(store: Any) -> bool:
    """Keep malformed legacy targets visible without scanning known jobs."""
    return store.db.execute('SELECT 1 FROM jobs WHERE youtube_admission_unknown LIMIT 1').fetchone() is not None


def apply_delta(store: Any, existing: list[str]) -> list[str]:
    """Update only changed object IDs, probing contributor GIN for each."""
    result=set(existing)
    cursor: str | None=None
    while True:
        sql='SELECT item FROM pg_temp.oko_youtube_admission_delta'
        if cursor is not None:sql+=' WHERE item>%s COLLATE "C"'
        sql+=' ORDER BY item LIMIT 100'
        page=store.db.execute(sql,(cursor,) if cursor is not None else ()).fetchall()
        if not page:break
        items=[row['item'] for row in page]
        rows=store.db.execute('''SELECT v.item,EXISTS(SELECT 1 FROM jobs j WHERE j.youtube_admission_candidate
            AND j.youtube_admission_objects @> jsonb_build_array(v.item)) AS present
            FROM unnest(%s::text[]) v(item)''',(items,)).fetchall()
        for row in rows:
            if row['present']:result.add(row['item'])
            else:result.discard(row['item'])
        cursor=items[-1]
    return sorted(result)


def affected_pages(store: Any, items: list[str]) -> Any:
    """Stream jobs reachable from these materials, links, or media IDs."""
    for start in range(0,len(items),100):
        batch=items[start:start+100]
        sql="""SELECT DISTINCT id FROM (
            SELECT j.id FROM jobs j WHERE j.kind IN ('fetch','download_media') AND j.item=ANY(%s)
            UNION ALL SELECT j.id FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=ANY(%s)
            UNION ALL SELECT j.id FROM publication_links l JOIN jobs j ON j.kind IN ('fetch','download_media') AND j.item=l.target_publication_id WHERE l.source_publication_id=ANY(%s)
            UNION ALL SELECT j.id FROM publication_links l JOIN media_files m ON m.publication_id=l.target_publication_id JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE l.source_publication_id=ANY(%s)
        ) affected"""
        params=(batch,batch,batch,batch)
        sample=store.db.execute(sql+' LIMIT 101',params).fetchall()
        if len(sample)<=100:
            if sample:yield [r['id'] for r in sample]
        else:
            for rows in store.db.incomplete_row_pages(sql,params):
                yield [r['id'] for r in rows]


def may_have_contributors(store: Any) -> bool:
    """Skip unrelated work when no YouTube feed or previous contributor exists."""
    row=store.db.execute("""SELECT EXISTS(SELECT 1 FROM feeds WHERE kind='youtube' LIMIT 1)
        OR EXISTS(SELECT 1 FROM jobs WHERE youtube_admission_candidate LIMIT 1)""").fetchone()
    return bool(row and row[0])


def capture_ids(store: Any, ids: list[int], *, relevant_checked: bool = False, workset_ready: bool = False) -> None:
    """Capture the exact job IDs, including NULL-target legacy jobs."""
    if not ids:return
    if not store.db.in_transaction:raise RuntimeError('Admission capture requires a transaction')
    if not relevant_checked and not may_have_contributors(store):return
    if not workset_ready:store.db.ensure_incomplete_workset()
    store.db.ensure_youtube_admission_workset()
    for start in range(0,len(ids),100):
        store.db.execute('''INSERT INTO pg_temp.oko_youtube_admission_work(job_id,had_candidate,old_objects) SELECT id,youtube_admission_candidate,youtube_admission_objects FROM jobs WHERE id=ANY(%s) ON CONFLICT(job_id) DO UPDATE SET had_candidate=pg_temp.oko_youtube_admission_work.had_candidate OR excluded.had_candidate''',(ids[start:start+100],))


def capture_items(store: Any, items: list[str], *, relevant_checked: bool = False, workset_ready: bool = False) -> None:
    """Capture old and new affected IDs in transaction-local scratch only."""
    if not items:return
    if not store.db.in_transaction:raise RuntimeError('Admission capture requires a transaction')
    if not relevant_checked and not may_have_contributors(store):return
    if not workset_ready:store.db.ensure_incomplete_workset()
    for ids in affected_pages(store,items):capture_ids(store,ids,relevant_checked=True,workset_ready=True)


def sync_captured(store: Any) -> bool:
    """Reproject captured IDs once after all mutations in this transaction."""
    touched=False
    cursor: int | None=None
    while True:
        sql='SELECT job_id,had_candidate,old_objects FROM pg_temp.oko_youtube_admission_work'
        if cursor is not None:sql+=' WHERE job_id>%s'
        sql+=' ORDER BY job_id LIMIT 100'
        rows=store.db.execute(sql,(cursor,) if cursor is not None else ()).fetchall()
        if not rows:break
        ids=[r['job_id'] for r in rows]
        before=any(r['had_candidate'] for r in rows) or store.db.execute('SELECT 1 FROM jobs WHERE id=ANY(%s) AND youtube_admission_candidate LIMIT 1',(ids,)).fetchone() is not None
        store.db.execute('''INSERT INTO pg_temp.oko_youtube_admission_delta(item)
            SELECT DISTINCT obj.value FROM pg_temp.oko_youtube_admission_work w
            CROSS JOIN LATERAL jsonb_array_elements_text(w.old_objects) obj(value)
            WHERE w.job_id=ANY(%s) AND w.had_candidate AND obj.value IS NOT NULL AND obj.value<>'' ON CONFLICT DO NOTHING''',(ids,))
        sync_ids(store,ids)
        store.db.execute('''INSERT INTO pg_temp.oko_youtube_admission_delta(item)
            SELECT DISTINCT obj.value FROM jobs j CROSS JOIN LATERAL jsonb_array_elements_text(j.youtube_admission_objects) obj(value)
            WHERE j.id=ANY(%s) AND j.youtube_admission_candidate AND obj.value IS NOT NULL AND obj.value<>'' ON CONFLICT DO NOTHING''',(ids,))
        after=store.db.execute('SELECT 1 FROM jobs WHERE id=ANY(%s) AND youtube_admission_candidate LIMIT 1',(ids,)).fetchone() is not None
        touched=touched or before or after
        cursor=ids[-1]
    return touched


def sync_items(store: Any,items: list[str]) -> bool:
    """One-shot proof for stable graph; event integration must capture before/after."""
    capture_items(store,items)
    return sync_captured(store)


def capture_fetch_flag_changes(store: Any, ids: list[int]) -> None:
    """Only flag changes that can affect the YouTube group need tracking."""
    for start in range(0,len(ids),100):
        batch=ids[start:start+100]
        rows=store.db.execute(f'''SELECT DISTINCT j.id FROM jobs j
            LEFT JOIN publications item ON item.id=j.item
            LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
            LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
            LEFT JOIN publication_links link ON link.target_publication_id=coalesce(item.id,media_owner.id)
            LEFT JOIN publications root ON root.id=link.source_publication_id AND root.viewed=0
            LEFT JOIN feeds f ON f.id=coalesce({owner_expr('item')},{owner_expr('media_owner')},{owner_expr('root')},j.feed_id)
            WHERE j.id=ANY(%s) AND (j.youtube_admission_candidate OR f.kind='youtube')''',(batch,)).fetchall()
        capture_ids(store,[row['id'] for row in rows])


def capture_feed(store: Any, feed: str) -> None:
    """Capture jobs whose fallback feed changes meaning on feed add/delete."""
    if not store.db.in_transaction:raise RuntimeError('Admission capture requires a transaction')
    query="""SELECT id FROM jobs WHERE feed_id IS NOT NULL AND md5(feed_id)=md5(%s) AND feed_id=%s
        AND kind IN ('fetch','download_media') AND (status IN ('pending','running') OR youtube_admission_candidate)"""
    for rows in store.db.incomplete_row_pages(query,(feed,feed)):
        capture_ids(store,[row['id'] for row in rows])
