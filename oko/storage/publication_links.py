"""Direct article dependencies; external identities come only from adapters."""
from oko.storage.publication_owners import owner_expr
from oko.storage.database import expect_row, Row
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from oko.collection.adapters import ExternalReference
from oko.domain.collection import CollectionEffects
from oko.storage.reading import reading_value, saved_content
from oko.storage import memberships
from oko.storage.reader_status import changed as status_changed

if TYPE_CHECKING:
    from oko.storage.store import Store


@reading_value
def children(store: 'Store',item: str) -> list[Row]:
    from oko.storage.material_read import current
    inputs = current(store.db)
    if inputs is not None and item in inputs.children:
        return inputs.children[item]
    return list(store.db.execute(f"SELECT i . * , {owner_expr('i')} AS feed_id , j . error AS fetch_error FROM publication_links l JOIN publications i ON i . id = l . target_publication_id LEFT JOIN jobs j ON j . kind = 'fetch' AND j . item = i . id WHERE l . source_publication_id = %s AND i . viewed = 0 ORDER BY l . rowid",(item,)).fetchall())


def sync(store: 'Store', item: str, references: tuple[ExternalReference,...], effects: CollectionEffects) -> bool:
    memberships.lock_graph(store.db)
    owner=expect_row(store.db.execute(f"SELECT i.*, {owner_expr('i')} AS feed_id FROM publications i WHERE i.id = %s",(item,)).fetchone())
    if owner['feed_id'] is None:
        return False
    previous_rows=store.db.execute("""SELECT l.target_publication_id,
        EXISTS (SELECT 1 FROM jobs j WHERE j.item=l.target_publication_id AND j.problem_code LIKE 'source-%%') OR
        EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=l.target_publication_id AND j.problem_code LIKE 'source-%%') AS source_problem
        FROM publication_links l WHERE l.source_publication_id=%s""",(item,)).fetchall()
    previous={row['target_publication_id'] for row in previous_rows}
    source_targets={row['target_publication_id'] for row in previous_rows if row['source_problem']}
    capture_deletion_problems(store,effects,previous)
    capture_fetch_problems(store,effects,previous)
    capture_media_problems(store,effects,previous)
    keys=sorted({(ref.key.platform.value,ref.key.external_id) for ref in references if (ref.key.platform.value,ref.key.external_id)!=(owner['platform'],owner['external_id'])})
    existing: set[str]=set()
    for start in range(0,len(keys),400):
        batch=keys[start:start+400]
        found=store.db.execute("""SELECT p.id,
            EXISTS (SELECT 1 FROM jobs j WHERE j.item=p.id AND j.problem_code LIKE 'source-%%') OR
            EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=p.id AND j.problem_code LIKE 'source-%%') AS source_problem
            FROM unnest(%s::text[],%s::text[]) AS v(platform,external_id) JOIN publications p ON p.platform=v.platform AND p.external_id=v.external_id""",([key[0] for key in batch],[key[1] for key in batch])).fetchall()
        existing.update(row['id'] for row in found)
        source_targets.update(row['id'] for row in found if row['source_problem'])
    if previous!=existing or len(existing)<len(keys):
        if source_targets:capture_source_groups(store,effects,source_targets)
        store.incomplete_draft.mark_materials(sorted(previous | existing | {item}))
        affected=store.problems.processor_jobs_for_materials(sorted(previous | existing))
        store.problems.capture_processor_histories(affected,store.wall_clock())
    capture_processing_problems(store,effects,previous)
    targets=[]
    for ref in references:
        if (ref.key.platform.value,ref.key.external_id)==(owner['platform'],owner['external_id']):
            continue
        row=store.db.execute(f"SELECT i.*, {owner_expr('i')} AS feed_id, (SELECT j.id FROM jobs j WHERE j.kind='delete' AND j.item=i.id AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL)) AS delete_problem_job, (SELECT j.id FROM jobs j WHERE j.kind='fetch' AND j.item=i.id AND ((j.problem_code IS NULL AND j.error IS NOT NULL) OR (j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%' AND j.problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')))) AS fetch_problem_job, EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=i.id AND ((m.resource_key='youtube:subtitles' AND j.problem_code IS NULL AND j.error IS NOT NULL) OR (j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%'))) AS media_problem_exists, EXISTS (SELECT 1 FROM jobs j WHERE j.kind IN ('filter','theses') AND j.item=i.id AND (((j.problem_code='' OR (j.problem_code IS NULL AND j.error IS NOT NULL))) OR (j.problem_code IS NOT NULL AND j.problem_code<>'' AND j.problem_code NOT LIKE 'source-%%' AND j.problem_code NOT IN ('processor-config','ai-permission','codex-choice','youtube-admission')))) AS processing_problem_exists FROM publications i WHERE i.platform = %s AND i.external_id = %s",
                             (ref.key.platform.value,ref.key.external_id)).fetchone()
        if row is None:
            identity=str(uuid4())
            store.db.execute("INSERT INTO publications ( id , source_id , url , published , members , published_known , platform , external_id , discovered_at ) VALUES ( %s , %s , %s , 0 , '[]' , 0 , %s , %s , %s )",
                (identity,'linked:'+identity,ref.url,ref.key.platform.value,ref.key.external_id,store.wall_clock()))
            status_changed(store.db)
            effects.fetch[identity]=False
        else:
            identity=row['id']
            if row['processing_problem_exists']:
                capture_processing_problems(store,effects,{identity})
            if row['media_problem_exists']:
                capture_media_problems(store,effects,{identity})
            if row['fetch_problem_job'] is not None and row['fetch_problem_job'] not in effects.fetch_problems_checked:
                from oko.storage.fetch_job_problems import managed_candidates
                effects.fetch_problems_checked.add(row['fetch_problem_job'])
                prior=managed_candidates(store,row['fetch_problem_job'],store.wall_clock())
                if prior:effects.fetch_problems_before[row['fetch_problem_job']]=prior
            if identity not in effects.delete_problems_checked:
                if row['delete_problem_job'] is not None:
                    effects.delete_problem_ids_before[identity] = store.problems.deletion_problem_ids(row['delete_problem_job'],store.wall_clock())
                effects.delete_problems_checked.add(identity)
            if row['url']!=ref.url:
                raise ValueError('Conflicting linked publication identity')
            if row['viewed'] and row['feed_id'] is None:
                store.set_processor_viewed([identity],False)
                effects.fetch[identity]=True
            elif owner['platform']=='rss':
                effects.refresh_links.add(identity)
        targets.append(identity)
    store.db.execute('DELETE FROM publication_links WHERE source_publication_id = %s',(item,))
    store.db.executemany('INSERT INTO publication_links VALUES ( %s , %s ) ON CONFLICT DO NOTHING',[(item,target) for target in targets])
    prune(store,previous-set(targets),effects=effects)
    changed=previous!=set(targets)
    if changed and store.problems.processor_jobs_for_materials(sorted(previous | set(targets))):
        status_changed(store.db)
    return changed


def feedless(store: 'Store', item: str) -> bool:
    return store.db.execute(f"SELECT 1 FROM publications i WHERE i.id = %s AND i.platform IS NOT NULL AND {owner_expr('i')} IS NULL",(item,)).fetchone() is not None


def text(store: 'Store', item: str) -> str:
    sections=[]
    for row in children(store,item):
        if not row['complete']:
            continue
        content=saved_content(row['content'])
        body='\n\n'.join(block['text'] for block in content.get('blocks',[]) if block['type'] in ('text','quote') and block.get('text'))
        if body:
            sections.append('---\n\n# '+row['title']+'\n\nИсходник: '+row['url']+'\n\n'+body)
    return '\n\n'.join(sections)


def notify_parents(store: 'Store', item: str, *, preserve_cancelled: bool = False) -> None:
    notify_parents_many(store, {item}, preserve_cancelled=preserve_cancelled)


def notify_parents_many(store: 'Store', items: set[str], *, preserve_cancelled: bool = False) -> None:
    if not items:
        return
    memberships.lock_graph(store.db)
    rows = store.db.execute('SELECT DISTINCT source_publication_id FROM publication_links WHERE target_publication_id = ANY(%s)', (sorted(items),)).fetchall()
    for row in rows:
        store.read_sync.mark_item(row[0])
        store.read_sync.saved_publication(row[0])
        store.processing.reconcile_accepted_item(row[0],preserve_cancelled=preserve_cancelled)


def capture_processing_problems(store: 'Store', effects: CollectionEffects, items: set[str], *, kinds: tuple[str,...] = ('filter','theses')) -> None:
    from oko.storage.processing_job_problems import managed_candidates_many
    ordered=sorted(items)
    for start in range(0,len(ordered),400):
        rows=store.db.execute("""SELECT id FROM jobs j WHERE kind=ANY(%s) AND item=ANY(%s)
            AND (((j.problem_code='' OR (j.problem_code IS NULL AND j.error IS NOT NULL))) OR (j.problem_code IS NOT NULL AND j.problem_code<>'' AND j.problem_code NOT LIKE 'source-%%'
            AND j.problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')))""",(list(kinds),ordered[start:start+400])).fetchall()
        ids=[row['id'] for row in rows if row['id'] not in effects.processing_problems_checked]
        effects.processing_problems_checked.update(ids)
        if ids:
            groups=managed_candidates_many(store,ids,store.wall_clock())
            effects.processing_problems_before.update({job_id:previous for job_id,previous in groups.items() if previous})


def capture_media_problems(store: 'Store', effects: CollectionEffects, items: set[str]) -> None:
    from oko.storage.media_job_problems import previous_candidates
    ordered=sorted(items)
    for start in range(0,len(ordered),400):
        for row in store.db.execute("""SELECT j.id FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id
            WHERE m.publication_id=ANY(%s) AND (
            (m.resource_key='youtube:subtitles' AND ((j.problem_code IS NULL AND j.error IS NOT NULL) OR (j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%' AND j.problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'))))
            OR (((m.resource_key='youtube:subtitles' AND j.problem_code IS NULL AND j.error IS NOT NULL) OR (j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%'))
            AND j.problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch','work-download_media')))""",(ordered[start:start+400],)):
            if row['id'] in effects.media_problems_checked:continue
            effects.media_problems_checked.add(row['id'])
            previous=previous_candidates(store,row['id'],store.wall_clock())
            if previous:effects.media_problems_before[row['id']]=previous


def capture_fetch_problems(store: 'Store', effects: CollectionEffects, items: set[str]) -> None:
    from oko.storage.fetch_job_problems import managed_candidates
    for start in range(0,len(items),400):
        batch=sorted(items)[start:start+400]
        for row in store.db.execute("SELECT id,j.problem_code,j.error FROM jobs j WHERE kind='fetch' AND item=ANY(%s) AND ((j.problem_code IS NULL AND j.error IS NOT NULL) OR (j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%' AND j.problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')))",(batch,)):
            if row['id'] in effects.fetch_problems_checked:continue
            effects.fetch_problems_checked.add(row['id'])
            candidates=managed_candidates(store,row['id'],store.wall_clock())
            if candidates or (row['problem_code'] is None and row['error'] is not None):effects.fetch_problems_before[row['id']]=candidates


def capture_deletion_problems(store: 'Store', effects: CollectionEffects, items: set[str]) -> None:
    missing = sorted(items - effects.delete_problems_checked)
    effects.delete_problems_checked.update(missing)
    for start in range(0,len(missing),400):
        for row in store.db.execute("SELECT id,item FROM jobs WHERE kind='delete' AND item=ANY(%s) AND (error IS NOT NULL OR problem_code IS NOT NULL)", (missing[start:start+400],)):
            effects.delete_problem_ids_before[row['item']] = store.problems.deletion_problem_ids(row['id'],store.wall_clock())


def capture_source_groups(store: 'Store', effects: CollectionEffects, roots: set[str], *, kinds: tuple[str,...]=('fetch','download_media','filter','theses','delete','feed-delete')) -> None:
    from oko.storage import source_groups
    ordered=sorted(roots)
    for start in range(0,len(ordered),400):
        batch=ordered[start:start+400]
        rows=store.db.execute("""WITH affected(id) AS MATERIALIZED (
            SELECT id FROM unnest(%s::text[]) x(id)
            UNION SELECT l.target_publication_id FROM unnest(%s::text[]) x(id)
                CROSS JOIN LATERAL (SELECT target_publication_id FROM publication_links WHERE source_publication_id=x.id OFFSET 0) l
        ) SELECT DISTINCT j.id FROM affected a CROSS JOIN LATERAL (
            SELECT id FROM jobs WHERE item=a.id AND kind=ANY(%s) AND problem_code LIKE 'source-%%'
            UNION ALL SELECT j.id FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id
                WHERE 'download_media'=ANY(%s) AND m.publication_id=a.id AND j.problem_code LIKE 'source-%%'
        ) j""",(batch,batch,list(kinds),list(kinds))).fetchall()
        ids=[row['id'] for row in rows if row['id'] not in effects.source_groups_before]
        for offset in range(0,len(ids),400):
            group=ids[offset:offset+400]
            effects.source_groups_before.update(source_groups.job_keys_many(store,group))
            evidence=source_groups.job_evidence_many(store,[(job_id,None) for job_id in group])
            for key,objects in evidence.items():effects.source_evidence_before.setdefault(key,set()).update(objects)


def capture_root_deletion_problems(store: 'Store', effects: CollectionEffects, roots: set[str]) -> None:
    ordered = sorted(roots)
    for start in range(0,len(ordered),400):
        batch = ordered[start:start+400]
        capture_deletion_problems(store,effects,set(batch))
        capture_fetch_problems(store,effects,set(batch))
        capture_media_problems(store,effects,set(batch))
        capture_processing_problems(store,effects,set(batch))
        capture_source_groups(store,effects,set(batch))
        processing_children=store.db.execute("""SELECT DISTINCT j.item FROM unnest(%s::text[]) root(id)
            CROSS JOIN LATERAL (SELECT target_publication_id FROM publication_links WHERE source_publication_id=root.id OFFSET 0) link
            CROSS JOIN LATERAL (SELECT item FROM jobs j WHERE j.kind IN ('filter','theses') AND j.item=link.target_publication_id AND (((j.problem_code='' OR (j.problem_code IS NULL AND j.error IS NOT NULL))) OR (j.problem_code IS NOT NULL AND j.problem_code<>'' AND j.problem_code NOT LIKE 'source-%%')) OFFSET 0) j""",(batch,)).fetchall()
        capture_processing_problems(store,effects,{row['item'] for row in processing_children})
        media_children=store.db.execute("""SELECT DISTINCT m.publication_id FROM unnest(%s::text[]) root(id)
            CROSS JOIN LATERAL (SELECT target_publication_id FROM publication_links WHERE source_publication_id=root.id OFFSET 0) link
            CROSS JOIN LATERAL (SELECT id,publication_id,resource_key FROM media_files WHERE publication_id=link.target_publication_id OFFSET 0) m
            CROSS JOIN LATERAL (SELECT id FROM jobs j WHERE j.kind='download_media' AND j.item=m.id AND ((m.resource_key='youtube:subtitles' AND j.problem_code IS NULL AND j.error IS NOT NULL) OR (j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%')) OFFSET 0) j""",(batch,)).fetchall()
        capture_media_problems(store,effects,{row['publication_id'] for row in media_children})
        fetch_children=store.db.execute("""SELECT DISTINCT j.item FROM unnest(%s::text[]) root(id)
            CROSS JOIN LATERAL (SELECT target_publication_id FROM publication_links WHERE source_publication_id=root.id OFFSET 0) link
            CROSS JOIN LATERAL (SELECT item FROM jobs j WHERE kind='fetch' AND item=link.target_publication_id AND ((j.problem_code IS NULL AND j.error IS NOT NULL) OR (j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%' AND j.problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'))) OFFSET 0) j""",(batch,)).fetchall()
        capture_fetch_problems(store,effects,{row['item'] for row in fetch_children})
        # Keep the selected roots as the outer relation: unrelated links and
        # unrelated failed Jobs must not turn this into a library-wide join.
        rows = store.db.execute("""SELECT DISTINCT j.id,j.item FROM unnest(%s::text[]) root(id)
            CROSS JOIN LATERAL (SELECT target_publication_id FROM publication_links
                WHERE source_publication_id=root.id ORDER BY target_publication_id OFFSET 0) link
            CROSS JOIN LATERAL (SELECT id,item FROM jobs WHERE kind='delete' AND item=link.target_publication_id
                AND (error IS NOT NULL OR problem_code IS NOT NULL) OFFSET 0) j""",(batch,))
        for row in rows:
            if row['item'] not in effects.delete_problems_checked:
                effects.delete_problems_checked.add(row['item'])
                effects.delete_problem_ids_before[row['item']] = store.problems.deletion_problem_ids(row['id'],store.wall_clock())


def prune(store: 'Store', candidates: set[str], *, effects: CollectionEffects | None = None) -> None:
    if not candidates:
        return
    memberships.lock_graph(store.db)
    rows=store.db.execute('''WITH RECURSIVE component(id) AS (
        SELECT id FROM publications WHERE id=ANY(%s)
        UNION
        SELECT edge.id FROM component c CROSS JOIN LATERAL (
            SELECT target_publication_id AS id FROM publication_links WHERE source_publication_id=c.id
            UNION ALL
            SELECT source_publication_id AS id FROM publication_links WHERE target_publication_id=c.id
        ) edge
    ), needed(id) AS (
        SELECT root.id FROM component c CROSS JOIN LATERAL (
            SELECT i.id FROM publications i WHERE i.id=c.id AND i.viewed=0
                AND EXISTS (SELECT 1 FROM feed_publications f WHERE f.publication_id=i.id) OFFSET 0
        ) root
        UNION
        SELECT edge.id FROM needed n CROSS JOIN LATERAL (
            SELECT target_publication_id AS id FROM publication_links WHERE source_publication_id=n.id OFFSET 0
        ) edge
    )
    SELECT candidate.id FROM component c CROSS JOIN LATERAL (
        SELECT i.id FROM publications i WHERE i.id=c.id AND i.platform IS NOT NULL AND i.viewed=0 OFFSET 0
    ) candidate WHERE NOT EXISTS (SELECT 1 FROM needed n WHERE n.id=candidate.id)''', (sorted(candidates),)).fetchall()
    items = [row['id'] for row in rows]
    if effects is not None:
        capture_root_deletion_problems(store,effects,set(items))
    store.publications.forget_many(items,effects=effects)
    for start in range(0, len(items), 400):
        batch = items[start:start+400]
        marks = ','.join('%s' for _ in batch)
        store.publications.set_source_deleted(batch, False)
        store.jobs.forget_input('filter', batch)
        store.jobs.forget_input('theses', batch)
        store.set_processor_viewed(batch,True)


def project_header(store: 'Store', item: str, header: dict[str,Any]) -> dict[str,Any]:
    from copy import deepcopy
    result=deepcopy(header)
    reasons=[result['reason']] if result.get('reason') else []
    for row in children(store,item):
        if not row['complete']:
            reasons.append('Связанная статья недоступна: '+row['fetch_error'] if row['fetch_error'] else 'Связанная статья ещё не загружена')
            continue
        article=deepcopy(saved_content(row['content']).get('extra',{}).get('_reader',{}))
        result['changed']=bool(result.get('changed') or article.get('changed'))
        if article.get('reason'):
            reasons.append(article['reason'])
        result.setdefault('attachments',[]).extend(article.get('attachments',[]))
        result.setdefault('_media_urls',{}).update(article.get('_media_urls',{}))
    result['reason']='; '.join(dict.fromkeys(reasons))
    result['full']=result.get('full',True) and not reasons
    return result


def reading(store: 'Store', item: str, post: str) -> dict[str,Any] | None:
    articles=[]
    for row in children(store,item):
        if row['platform']!='telegraph':
            continue
        content=saved_content(row['content'])
        header=content.get('extra',{}).get('_reader',{})
        body='\n\n'.join(block.get('text','') for block in content.get('blocks',[]) if block['type'] in ('text','quote'))
        articles.append(dict(source_url=row['url'],title=row['title'] or 'Telegraph',text=body,
            error=row['fetch_error'] or header.get('reason') or None,
            media_urls=list(header.get('_media_urls',{}).values())))
    return dict(post_text=post,articles=articles) if articles else None
