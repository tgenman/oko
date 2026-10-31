"""Targeted projection of Telegram read-Job problems.

This module does not mutate the store. The event owner must persist the result
within its own write transaction, including group suppression and recovery.
"""
from dataclasses import dataclass
from typing import Any, Literal, TYPE_CHECKING
import json

if TYPE_CHECKING:
    from oko.storage.store import Store


def classify_legacy(identity: str, body: Any) -> Literal['read','other','ambiguous']:
    """Classify old untyped Job keys without guessing when text IDs overlap."""
    if not identity.startswith('job:'):
        return 'other'
    if not isinstance(body,dict):
        return 'ambiguous'
    operation=body.get('operation')
    if operation=='producer:read':
        return 'read'
    if operation not in (None,'producer:status'):
        return 'other'
    code=body.get('code')
    if not isinstance(code,str):
        return 'ambiguous' if ':read' in identity else 'other'
    prefix='job:'+code+':'
    if not identity.startswith(prefix):
        return 'ambiguous' if ':read' in identity else 'other'
    tail=identity[len(prefix):]
    kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
    if code.startswith('source-'):
        found=[kind for kind in kinds if tail.endswith(':'+kind) and len(tail)>len(kind)+1]
    else:
        found=[kind for kind in kinds if ':'+kind+':' in tail]
    if len(found)==1:
        return 'read' if found[0]=='read' else 'other'
    return 'ambiguous' if 'read' in tail else 'other'


@dataclass(frozen=True)
class ReadJobProblem:
    body: dict[str, Any]
    legacy_identities: frozenset[str]


def candidates(store: 'Store', job_id: int, now: float) -> list[ReadJobProblem]:
    db = store.db
    rows = db.execute("""SELECT j.kind,j.item,j.status,j.error,j.problem_code,j.feed_id FROM jobs j
        WHERE j.id=%s AND j.kind='read' AND j.status IN ('pending','running','failed','unavailable','blocked')
        AND (j.problem_code IS NOT NULL OR j.error IS NOT NULL)""",(job_id,)).fetchall()
    if not rows:
        return []
    result: dict[str,ReadJobProblem] = {}
    request_error: bool | None = None
    for row in rows:
        code=row['problem_code']
        if code in ('codex-choice','ai-permission','youtube-admission','processor-config'):
            continue
        if code is None:
            if request_error is None:
                saved=db.execute("""SELECT f.id,f.transport,f.connection,f.auto_read,h.feed AS history_feed,
                    h.selection::jsonb->>'mode' AS history_mode,h.frozen,h.scan_complete
                    FROM feeds f LEFT JOIN initial_history h ON h.feed=f.id
                    WHERE f.id=%s AND f.transport='telegram'""",(row['item'],)).fetchone()
                config = None
                if saved:
                    history = (dict(mode=saved['history_mode'],frozen=bool(saved['frozen'] and saved['scan_complete']),
                                    scan_complete=bool(saved['scan_complete'])) if saved['history_feed'] is not None else None)
                    config = dict(id=saved['id'],transport=saved['transport'],connection=saved['connection'],
                                  auto_read=saved['auto_read'],initial_history=history)
                binding=store.subscriptions.telegram.binding(row['item']) if config else None
                state=store.read_sync.request_state(config,binding) if config else None
                request_error=bool(state is not None and state['error'])
            if not request_error:
                continue
        code=code or 'work-read'
        feed=row['item'] or 'demo'
        if code in ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'):
            needle=json.dumps([dict(kind='feed',id=feed)])
            if db.execute("SELECT 1 FROM reader_problems WHERE identity LIKE 'profile:%%' AND finished IS NULL AND (body::jsonb->'objects') @> %s::jsonb LIMIT 1",(needle,)).fetchone():
                continue
        shared=code.startswith('source-')
        item_id=row['item'] or ''
        identity='job:'+code+':'+feed+':read'+('' if shared else ':'+item_id)
        legacy_feeds={row['feed_id'] or row['item'] or 'demo'}
        legacy_ids={'job:'+code+':'+value+':read'+('' if shared else ':'+item_id) for value in legacy_feeds} - {identity}
        next_step=('Включите обработку, чтобы повторить попытку.' if row['status']=='blocked' else
                   'Повторите загрузку вручную.' if row['status']=='failed' else
                   'Проверьте доступность источника.' if row['status']=='unavailable' else
                   'Око повторит попытку автоматически.')
        body=dict(id=identity,code=code,section='object',title='Не удалось подтвердить чтение в Telegram',
            consequence='Сохранённое содержимое остаётся доступным.',next_step=next_step,
            objects=[dict(kind='feed',id=feed)],action=None,operation='producer:read',created_at=now,
            state='retrying' if row['status']=='running' or not row['error'] else 'current',
            finished_at=None,outcome=None,next_at=None)
        if row['error']:
            body['reason']=row['error']
        if code=='source-reconnect':
            body.update(section='action',next_step='Проверьте привязку источника; доступ не изменяется автоматически.',action=None)
        prior = result.get(identity)
        result[identity]=ReadJobProblem(body,frozenset(legacy_ids | (set(prior.legacy_identities) if prior else set())))
    return list(result.values())
