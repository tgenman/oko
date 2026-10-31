"""Current reader obstacles; identity and recovery are independent of message text."""
from oko.storage.publication_owners import owner_expr
from oko.storage.database import expect_row, Row
import json
import hashlib
import math
import re
from datetime import datetime, timezone
from oko.collection.contracts import SourceManifest
from oko.collection.external_media import expected_incomplete
from oko.storage.reading import reading_snapshot
from oko.storage import problem_links
from oko.storage.reader_status import changed as status_changed
from dataclasses import dataclass
import uuid
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from oko.storage.store import Store


_PROCESSOR_VALID_SQL = 'created >= -62135596800 AND created < 253402300800 AND finished IS NULL AND body::jsonb->>\'code\'=\'processor-config\' AND jsonb_typeof(body::jsonb->\'title\')=\'string\' AND jsonb_typeof(body::jsonb->\'consequence\')=\'string\' AND jsonb_typeof(body::jsonb->\'next_step\')=\'string\' AND jsonb_typeof(body::jsonb->\'operation\')=\'string\' AND body::jsonb->>\'state\' IN (\'current\',\'retrying\') AND (body::jsonb->\'next_at\' IS NULL OR jsonb_typeof(body::jsonb->\'next_at\')=\'null\') AND (NOT (body::jsonb ? \'reason\') OR jsonb_typeof(body::jsonb->\'reason\')=\'string\') AND (NOT (body::jsonb ? \'request_id\') OR (jsonb_typeof(body::jsonb->\'request_id\')=\'string\' AND (body::jsonb->>\'request_id\') COLLATE "C" ~ \'^[A-Za-z0-9_-]{1,100}$\')) AND jsonb_typeof(body::jsonb->\'objects\')=\'array\' AND NOT (body::jsonb @? \'$.objects[*] ? (@.type() != "object" || !exists(@.kind) || @.kind.type() != "string" || !exists(@.id) || @.id.type() != "string" || @.id == "" || (@.kind != "settings" && @.kind != "feed" && @.kind != "folder" && @.kind != "material"))\') AND NOT (body::jsonb @? \'$.objects[*].keyvalue() ? (@.key != "kind" && @.key != "id" && @.key != "label")\')'

def _action_context(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value,dict) or not isinstance(value.get('title'),str):
        return None
    action = value.get('action')
    if 'action' not in value or (action is not None and (not isinstance(action,dict) or not isinstance(action.get('kind'),str))):
        return None
    objects = value.get('objects')
    if not isinstance(objects,list) or any(not isinstance(obj,dict) or obj.get('kind') not in ('settings','feed','folder','material') or not isinstance(obj.get('id'),str) or not obj['id'] or set(obj)-{'kind','id','label'} for obj in objects):
        return None
    attempt = value.get('request_attempt')
    if attempt is not None and not isinstance(attempt,str):
        return None
    error = value.get('command_error')
    if error is not None:
        if not isinstance(error,str):
            return None
        try:
            if not isinstance(json.loads(error),dict):
                return None
        except (TypeError,ValueError):
            return None
    return value


def _action_receipt_context(raw: str, identity: str) -> dict[str, Any] | None:
    context = _action_context(raw)
    if context is None:
        return None
    if 'command_context' not in context:
        return context
    saved = context['command_context']
    if not isinstance(saved,dict):
        return None
    try:
        expected = json.loads(identity)
    except (TypeError,ValueError):
        return None
    if not isinstance(expected,dict):
        return None
    def canonical(value: dict[str,Any]) -> dict[str,Any]:
        result=dict(value)
        field = 'keys' if result.get('operation')=='settings_patch' else 'fields' if result.get('operation')=='feed_patch' else None
        if field and isinstance(result.get(field),list) and all(isinstance(item,str) for item in result[field]):
            result[field]=sorted(result[field])
        return result
    return context if canonical(expected)==canonical(saved) else None


def _action_problem_body(raw: str) -> dict[str, Any] | None:
    try:
        body = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(body,dict) or body.get('code') not in ('action-failed','action-unknown'):
        return None
    if any(not isinstance(body.get(field),str) for field in ('title','consequence','next_step')):
        return None
    if body.get('state') not in ('current','retrying','finished'):
        return None
    if body.get('next_at') is not None and (not isinstance(body['next_at'],(int,float)) or isinstance(body['next_at'],bool) or not math.isfinite(body['next_at'])):
        return None
    if not isinstance(body.get('operation'),str):
        return None
    if 'reason' in body and not isinstance(body['reason'],str):
        return None
    if 'request_id' in body and (not isinstance(body['request_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',body['request_id'])):
        return None
    objects = body.get('objects')
    if not isinstance(objects,list) or any(not isinstance(obj,dict) or obj.get('kind') not in ('settings','feed','folder','material') or not isinstance(obj.get('id'),str) or not obj['id'] or set(obj)-{'kind','id','label'} for obj in objects):
        return None
    return body


def _generic_action(*, unknown: bool, request_id: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = dict(code='action-unknown' if unknown else 'action-failed',section='wait' if unknown else 'action',
        title='Результат старого действия требует проверки',consequence='Исходные сведения сохранены.',
        next_step='Проверьте результат действия вручную.',objects=[],action=None,operation='legacy-action',
        state='current',next_at=None)
    if isinstance(request_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}',request_id):
        value['request_id']=request_id
    return value


def _readable_timestamp(value: Any) -> bool:
    if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):
        return False
    try:
        datetime.fromtimestamp(value,timezone.utc)
    except (ValueError,OverflowError,OSError):
        return False
    return True


def _legacy_problem_id(identity: str) -> str:
    return 'legacy-unaddressable-\x00' + hashlib.sha256(identity.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class CommandRecord:
    identity: str
    context_json: str | None
    error_json: str | None
    result: str
    updated: float


class CommandContextMismatch(ValueError):
    pass


def new_read_problem_token() -> str:
    return uuid.uuid4().hex


def legacy_discover_classification(identity: str, body: Any) -> str:
    if not identity.startswith('job:') or not isinstance(body,dict):
        return 'other'
    operation=body.get('operation')
    if operation=='producer:discover':
        return 'discover'
    if operation not in (None,'producer:status'):
        return 'other'
    code=body.get('code')
    if not isinstance(code,str) or not identity.startswith('job:'+code+':'):
        return 'ambiguous' if ':discover' in identity else 'other'
    tail=identity[len('job:'+code+':'):]
    kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
    found=[kind for kind in kinds if tail.endswith(':'+kind) and len(tail)>len(kind)+1] if code.startswith('source-') else [kind for kind in kinds if ':'+kind+':' in tail]
    if len(found)==1:
        return 'discover' if found[0]=='discover' else 'other'
    return 'ambiguous' if ':discover' in tail else 'other'


class Problems:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        self.db = store.db
        self._receipt_cleanup_cursor: tuple[float, int] | None = None
        self._processor_transaction_captures: dict[int,dict[str,Any]] = {}
        self._recovering_actions = True
        # Recovery projection only: preserve the original service records for rollback.
        self._startup_pending = {row[0] for row in self.db.execute("SELECT id FROM reader_action_results WHERE result = 'pending'")}
        pending = sorted(self._startup_pending)
        for start in range(0,len(pending),100):
            with self.db:
                for identity in pending[start:start+100]:
                    receipt = self.db.execute('SELECT identity FROM reader_action_results WHERE id=%s', (identity,)).fetchone()
                    if receipt:
                        self.sync_action(receipt['identity'], self.store.wall_clock())
        self.recover_action_problems()
        self._recovering_actions = False

    def recover_action_problems(self) -> None:
        if self.db.execute("SELECT 1 FROM state WHERE key='action-problems-recovered-v2'").fetchone():
            return
        legacy_done = self.db.execute("SELECT 1 FROM state WHERE key='action-problems-recovered-v1'").fetchone() is not None
        # Version 1 had already reconciled current problems and positive receipts.
        if not legacy_done:
            cursor = ''
            while True:
                rows = self.db.execute("SELECT identity FROM reader_problems WHERE finished IS NULL AND identity LIKE 'action:%%' AND identity>%s ORDER BY identity LIMIT 100", (cursor,)).fetchall()
                if not rows:
                    break
                with self.db:
                    for row in rows:
                        self.sync_action(row['identity'][7:], self.store.wall_clock())
                cursor = rows[-1]['identity']
        sequence: int | None = None
        while True:
            boundary = ("AND r.sequence<=0" if legacy_done else '') + ('' if sequence is None else ' AND r.sequence>%s')
            parameters = () if sequence is None else (sequence,)
            rows = self.db.execute(f"""SELECT r.sequence, r.identity FROM reader_action_results r
                WHERE r.result IN ('failed','unknown') {boundary}
                AND NOT EXISTS (SELECT 1 FROM reader_action_results newer WHERE newer.identity=r.identity AND newer.sequence>r.sequence OFFSET 0)
                AND NOT EXISTS (SELECT 1 FROM reader_problems p WHERE p.identity='action:'||r.identity
                    AND (p.finished IS NULL OR (p.outcome='irrelevant' AND p.finished>=r.started
                        AND p.body::jsonb->>'request_id'=r.id
                        AND p.body::jsonb->>'request_attempt' IS NOT DISTINCT FROM r.body::jsonb->>'request_attempt')) OFFSET 0)
                ORDER BY r.sequence LIMIT 100""", parameters).fetchall()
            if not rows:
                break
            with self.db:
                for row in rows:
                    self.sync_action(row['identity'], self.store.wall_clock())
            sequence = rows[-1]['sequence']
            self.store.fault('action_problem_recovery_batch')
        with self.db:
            self.db.execute("INSERT INTO state(key,value) VALUES ('action-problems-recovered-v2','true') ON CONFLICT DO NOTHING")
            self.db.execute("DELETE FROM state WHERE key='action-problems-recovered-v1'")

    def cleanup_receipts(self, cutoff: float) -> None:
        previous = self._receipt_cleanup_cursor
        current = problem_links.cleanup(self.db, cutoff, previous)
        if current != previous:
            if self.db.in_transaction:
                self.db.after_rollback(lambda: setattr(self, '_receipt_cleanup_cursor', previous))
            self._receipt_cleanup_cursor = current

    def snapshot(self, now: float) -> dict[str, Any]:
        with reading_snapshot():
            candidates = self.candidates(now)
            feeds = {f['id']: f for f in self.store.subscriptions.feeds()}
        return self.reconcile(candidates, now, feeds)

    def codex_choice(self, now: float, *, feed: str | None = None) -> dict[str, Any] | None:
        configurations = ([self.store.subscriptions.effective('feed', feed, details=False)] if feed is not None else
                          [self.store.subscriptions.effective('oko','global'),*self.store.subscriptions.effective_feeds().values()])
        needs_codex = any((values := configuration['effective'])['processor']['value'] == 'codex' and
                         any(values[key]['value'] for key in ('theses', 'prompt_filters')) for configuration in configurations)
        if needs_codex and self.store.codex_config() is None:
            return dict(id='processor:codex-choice', code='codex-choice', section='action',
                        title='Не выбрана модель Codex', consequence='Тезисы пока не создаются.',
                        next_step='Выберите и сохраните модель для каждого действия.',
                        objects=[dict(kind='settings', id='global', label='Настройки Око')],
                        action=None, created_at=now,
                        state='current', finished_at=None, outcome=None, next_at=None)
        return None

    def sync_candidate(self, identity: str, candidate: dict[str, Any] | None, now: float, *, removed_outcome: str = 'resolved') -> None:
        """Persist one producer's problem in the same transaction as its cause."""
        existing = self.db.execute('SELECT id,body FROM reader_problems WHERE identity=%s AND finished IS NULL', (identity,)).fetchone()
        if existing and identity.startswith('action:') and _action_problem_body(existing['body']) is None:
            return
        if candidate is None:
            if existing:
                outcome = 'irrelevant' if identity=='processor:codex-choice' and self.store.codex_config() is None else removed_outcome
                self.db.execute('UPDATE reader_problems SET finished=%s,outcome=%s WHERE id=%s', (now,outcome,existing['id']))
                status_changed(self.db)
            return
        if not identity.startswith('action:'):
            candidate.setdefault('operation','producer:status')
        body = {key: value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome')}
        body['objects'] = [{key:value for key,value in obj.items() if key!='label'} for obj in candidate['objects']]
        encoded = json.dumps(body)
        if existing:
            if existing['body'] != encoded:
                self.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(encoded,existing['id']))
                status_changed(self.db)
        else:
            origin = 'action' if identity.startswith('action:') else 'background'
            self.db.execute('INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES (%s,%s,%s,%s,%s,NULL,NULL)',
                            (uuid.uuid4().hex,identity,origin,encoded,now))
            status_changed(self.db)

    def sync_codex_feed_added(self, feed: str, now: float) -> None:
        candidate = self.codex_choice(now, feed=feed)
        if candidate is not None:
            self.sync_candidate('processor:codex-choice', candidate, now)

    def sync_codex_choice(self, now: float) -> None:
        self.sync_candidate('processor:codex-choice', self.codex_choice(now), now)

    def action_candidate(self, receipt: Any) -> dict[str, Any] | None:
        if receipt['result'] not in ('failed','unknown','pending'):
            return None
        context = _action_receipt_context(receipt['body'],receipt['identity'])
        if context is None:
            unknown = receipt['result']=='unknown' or receipt['result']=='pending' and receipt['id'] in self._startup_pending
            candidate = _generic_action(unknown=unknown,request_id=receipt['id'])
            candidate.update(id='action:'+receipt['identity'],created_at=receipt['started'],finished_at=None,outcome=None)
            return candidate
        if receipt['id'] in self._startup_pending and self.db.execute(
            "SELECT 1 FROM reader_problems WHERE identity=%s AND outcome='irrelevant' AND finished >= %s AND body::jsonb->>'request_id'=%s AND body::jsonb->>'request_attempt' IS NOT DISTINCT FROM %s LIMIT 1",
            ('action:'+receipt['identity'], receipt['started'], receipt['id'], context.get('request_attempt'))).fetchone():
            return None
        if receipt['result'] == 'pending' and receipt['id'] not in self._startup_pending and not self.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL', ('action:'+receipt['identity'],)).fetchone():
            return None
        unknown = receipt['result'] == 'unknown' or receipt['result'] == 'pending' and receipt['id'] in self._startup_pending
        receipt_action: dict[str, Any] | None = context['action']
        if unknown and receipt_action and receipt_action['kind'] not in ('settings', 'feed-settings', 'material', 'add'):
            receipt_action = None
        operation = receipt['identity']
        if context.get('command_context'):
            try:
                error = json.loads(context['command_error']) if context.get('command_error') else None
            except (ValueError,TypeError):
                error = None
            operation = json.dumps(dict(context=context['command_context'],error=error))
        return dict(id='action:' + receipt['identity'],code='action-unknown' if unknown else 'action-failed',section='wait' if unknown else 'action',
            title='Результат действия пока неизвестен' if unknown else context['title'],
            consequence='Око сверяет результат; действие не повторяется автоматически.' if unknown else 'Не удалось подтвердить выполнение действия.',
            next_step='Проверьте состояние после восстановления связи.' if unknown else 'Вернитесь к действию, чтобы исправить проблему.',
            objects=context['objects'],action=receipt_action,operation=operation,request_id=receipt['id'],request_attempt=context.get('request_attempt'),
            state='retrying' if receipt['result'] == 'pending' and not unknown else 'current',created_at=receipt['started'],finished_at=None,outcome=None,next_at=None)

    def sync_action(self, identity: str, now: float) -> None:
        receipt = self.db.execute('SELECT * FROM reader_action_results WHERE identity=%s ORDER BY sequence DESC LIMIT 1', (identity,)).fetchone()
        candidate = self.action_candidate(receipt) if receipt else None
        existing_action = self.db.execute('SELECT body FROM reader_problems WHERE identity=%s AND finished IS NULL',('action:'+identity,)).fetchone()
        if existing_action and (receipt is None or _action_problem_body(existing_action['body']) is None or _action_receipt_context(receipt['body'],receipt['identity']) is None):
            return
        if self._recovering_actions and existing_action and receipt and receipt['result'] in ('failed','unknown','pending'):
            recovered_context = _action_receipt_context(receipt['body'],receipt['identity'])
            if recovered_context and recovered_context.get('command_error') is not None:
                return
        outcome = 'resolved'
        if candidate is None:
            existing = self.db.execute('SELECT body FROM reader_problems WHERE identity=%s AND finished IS NULL', ('action:'+identity,)).fetchone()
            if existing:
                for obj in json.loads(existing['body'])['objects']:
                    if obj['kind'] == 'material' and self.store.publications.source_deleted(obj['id']):
                        outcome = 'irrelevant'
                    elif obj['kind'] == 'feed':
                        feed = self.db.execute('SELECT connection FROM feeds WHERE id=%s', (obj['id'],)).fetchone()
                        if not feed or feed['connection'] == 'deleting':
                            outcome = 'irrelevant'
        self.sync_candidate('action:'+identity, candidate, now, removed_outcome=outcome)

    def fetch_linked_pairs(self, targets: set[str], roots: set[str]) -> set[tuple[str,str]]:
        if not roots:return set()
        pairs: set[tuple[str,str]]=set()
        ordered=sorted(targets)
        for start in range(0,len(ordered),400):
            pairs.update((row['source_publication_id'],row['target_publication_id']) for row in self.db.execute(
                'SELECT source_publication_id,target_publication_id FROM publication_links WHERE target_publication_id=ANY(%s) AND source_publication_id=ANY(%s)',
                (ordered[start:start+400],sorted(roots))))
        return pairs

    def active_fetch_roots(self, items: set[str]) -> set[str]:
        result: set[str]=set()
        ordered=sorted(items)
        for start in range(0,len(ordered),400):
            result.update(row['root'] for row in self.db.execute("""SELECT target.root FROM unnest(%s::text[]) target(root)
                WHERE EXISTS (SELECT 1 FROM jobs j WHERE j.kind='fetch' AND j.item=target.root AND ((j.status IN ('pending','running','failed','unavailable','blocked') AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL)) OR (j.status='done' AND j.error IS NOT NULL AND j.problem_code IS NOT NULL)))
                   OR EXISTS (SELECT 1 FROM publication_links link JOIN jobs j ON j.kind='fetch' AND j.item=link.target_publication_id
                       WHERE link.source_publication_id=target.root AND ((j.status IN ('pending','running','failed','unavailable','blocked') AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL)) OR (j.status='done' AND j.error IS NOT NULL AND j.problem_code IS NOT NULL)))""",(ordered[start:start+400],)))
        return result

    def recover_fetch_batch(self, rows: list[Any], now: float) -> list[Any]:
        """Persist independent first occurrences; address legacy collisions separately."""
        from oko.storage.fetch_job_problems import managed_candidates_many
        projected=managed_candidates_many(self.store,[row['id'] for row in rows],now)
        counts: dict[str,int]={};legacy_keys: set[str]=set()
        for candidates in projected.values():
            for candidate in candidates:
                identity=candidate['id'];counts[identity]=counts.get(identity,0)+1
                if candidate.get('_legacy_id'):legacy_keys.add(candidate['_legacy_id'])
        identities=list(counts)
        if not identities:return []
        markers={str(job_id):job_id for job_id in projected}
        marked: dict[int,list[Any]]={}
        for record in self.db.execute("""SELECT r.id,r.identity,r.body,r.created,target.marker
            FROM unnest(%s::text[]) target(marker) CROSS JOIN LATERAL (
                SELECT id,identity,body,created FROM reader_problems WHERE finished IS NULL
                  AND body::jsonb->>'operation'='producer:fetch' AND body::jsonb->>'code'='work-fetch'
                  AND body::jsonb->>'fetch_job_identity' IS NOT NULL AND body::jsonb->>'fetch_job_id' IS NOT NULL
                  AND (body::jsonb->>'fetch_job_id') COLLATE "C"=target.marker COLLATE "C"
                ORDER BY id COLLATE "C" LIMIT 101
            ) r""",(list(markers),)):
            if record['marker'] in markers:marked.setdefault(markers[record['marker']],[]).append(record)
        items_by_id={row['id']:row['item'] for row in self.db.execute('SELECT id,item FROM jobs WHERE id=ANY(%s)',(list(marked),))} if marked else {}
        allowed_body_fields={'code','section','title','consequence','next_step','objects','action','operation',
            'fetch_owner','fetch_implicit_code','state','next_at','reason','fetch_job_id','fetch_job_identity',
            'status_job_identity','status_job_target'}
        overflow={job_id for job_id,records_for_job in marked.items() if len(records_for_job)>100}
        # Large groups are streamed in SQL: ambiguous bodies stay untouched, and
        # byte-identical proven duplicates are closed in bounded pages.
        for job_id in list(overflow):
            item=items_by_id.get(job_id)
            allowed={obj['id'] for candidate in projected[job_id] for obj in candidate['objects'] if obj.get('kind')=='material'} | {item}
            first=marked[job_id][0]
            try:body=json.loads(first['body'])
            except (ValueError,TypeError):continue
            canonical=body.get('fetch_job_identity') if isinstance(body,dict) else None
            objects=body.get('objects') if isinstance(body,dict) else None
            if (not isinstance(body,dict) or set(body)-allowed_body_fields or not isinstance(canonical,str)
                or not canonical.startswith('job:work-fetch:') or canonical.count(':fetch:')!=1
                or canonical.split(':fetch:',1)[1]!=item or body.get('fetch_job_id')!=str(job_id)
                or not isinstance(body.get('fetch_implicit_code'),bool)
                or (body.get('status_job_target') is not None and body.get('status_job_identity') is None)
                or (body.get('status_job_identity') is not None and (body.get('status_job_identity')!=canonical or body.get('status_job_target')!=item))
                or not isinstance(objects,list) or not objects or any(not isinstance(obj,dict) or obj.get('kind')!='material' or obj.get('id') not in allowed for obj in objects)
                or not _readable_timestamp(first['created'])):continue
            marker=str(job_id)
            mismatch=self.db.execute("""SELECT 1 FROM reader_problems WHERE finished IS NULL
                AND body::jsonb->>'operation'='producer:fetch' AND body::jsonb->>'code'='work-fetch'
                AND body::jsonb->>'fetch_job_identity' IS NOT NULL AND body::jsonb->>'fetch_job_id' IS NOT NULL
                AND (body::jsonb->>'fetch_job_id') COLLATE "C"=%s
                AND (body COLLATE "C"<>%s COLLATE "C" OR created IS NULL OR NOT (created>=-62135596800 AND created<=253402300799)) LIMIT 1""",(marker,first['body'])).fetchone()
            if mismatch:continue
            winner=self.db.execute("""SELECT id,identity,body,created,body::jsonb->>'fetch_job_id' AS marker FROM reader_problems
                WHERE finished IS NULL AND body::jsonb->>'operation'='producer:fetch' AND body::jsonb->>'code'='work-fetch'
                AND body::jsonb->>'fetch_job_identity' IS NOT NULL AND body::jsonb->>'fetch_job_id' IS NOT NULL
                AND (body::jsonb->>'fetch_job_id') COLLATE "C"=%s ORDER BY created,id COLLATE "C" LIMIT 1""",(marker,)).fetchone()
            if not winner or not _readable_timestamp(winner['created']):continue
            changed=False
            while True:
                closed=self.db.execute("""UPDATE reader_problems SET finished=%s,outcome='irrelevant' WHERE id IN (
                    SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:fetch'
                    AND body::jsonb->>'code'='work-fetch' AND body::jsonb->>'fetch_job_identity' IS NOT NULL
                    AND body::jsonb->>'fetch_job_id' IS NOT NULL AND (body::jsonb->>'fetch_job_id') COLLATE "C"=%s
                    AND id<>%s ORDER BY id COLLATE "C" LIMIT 400) RETURNING id""",(now,marker,winner['id'])).fetchall()
                if not closed:break
                changed=True
            if changed:status_changed(self.db)
            marked[job_id]=[winner]
            overflow.remove(job_id)
        duplicate_ids: list[str]=[]
        for job_id,records_for_job in marked.items():
            if job_id in overflow:continue
            item=items_by_id.get(job_id)
            allowed={obj['id'] for candidate in projected[job_id] for obj in candidate['objects'] if obj.get('kind')=='material'} | {item}
            equivalent: dict[str,list[Any]]={}
            for record in records_for_job:
                try:body=json.loads(record['body'])
                except (ValueError,TypeError):continue
                canonical=body.get('fetch_job_identity') if isinstance(body,dict) else None
                objects=body.get('objects') if isinstance(body,dict) else None
                if (not isinstance(body,dict) or set(body)-allowed_body_fields or not isinstance(canonical,str) or not canonical.startswith('job:work-fetch:') or canonical.count(':fetch:')!=1
                    or canonical.split(':fetch:',1)[1]!=item or body.get('fetch_job_id')!=str(job_id)
                    or not isinstance(body.get('fetch_implicit_code'),bool)
                    or (body.get('status_job_target') is not None and body.get('status_job_identity') is None)
                    or (body.get('status_job_identity') is not None and (body.get('status_job_identity')!=canonical or body.get('status_job_target')!=item))
                    or not isinstance(objects,list) or not objects or any(not isinstance(obj,dict) or obj.get('kind')!='material' or obj.get('id') not in allowed for obj in objects)
                    or not _readable_timestamp(record['created'])):continue
                equivalent.setdefault(record['body'],[]).append(record)
            removed: set[str]=set()
            for same in equivalent.values():
                if len(same)<2:continue
                same.sort(key=lambda row:(row['created'],row['id']))
                removed.update(row['id'] for row in same[1:])
            if removed:
                duplicate_ids.extend(sorted(removed))
                marked[job_id]=[row for row in records_for_job if row['id'] not in removed]
        if duplicate_ids:
            for start in range(0,len(duplicate_ids),400):
                self.db.execute("UPDATE reader_problems SET finished=%s,outcome='irrelevant' WHERE id=ANY(%s) AND finished IS NULL",(now,duplicate_ids[start:start+400]))
            status_changed(self.db)
        # Opaque or large legacy groups are never rewritten as a side effect of
        # matching a newer error. Publish a distinct newer cause only when its
        # own candidate identities have a bounded number of existing matches.
        protected=set(overflow)
        for job_id,records_for_job in marked.items():
            item=items_by_id.get(job_id)
            allowed={obj['id'] for candidate in projected[job_id] for obj in candidate['objects'] if obj.get('kind')=='material'} | {item}
            for record in records_for_job:
                try:body=json.loads(record['body'])
                except (ValueError,TypeError):
                    protected.add(job_id);break
                canonical=body.get('fetch_job_identity') if isinstance(body,dict) else None
                objects=body.get('objects') if isinstance(body,dict) else None
                if (not isinstance(body,dict) or set(body)-allowed_body_fields or not isinstance(canonical,str)
                    or not canonical.startswith('job:work-fetch:') or canonical.count(':fetch:')!=1
                    or canonical.split(':fetch:',1)[1]!=item or body.get('fetch_job_id')!=str(job_id)
                    or not isinstance(body.get('fetch_implicit_code'),bool)
                    or (body.get('status_job_target') is not None and body.get('status_job_identity') is None)
                    or (body.get('status_job_identity') is not None and (body.get('status_job_identity')!=canonical or body.get('status_job_target')!=item))
                    or not isinstance(objects,list) or not objects or any(not isinstance(obj,dict) or obj.get('kind')!='material' or obj.get('id') not in allowed for obj in objects)
                    or not _readable_timestamp(record['created'])):
                    protected.add(job_id);break
        processed:set[int]=set()
        for job_id in protected:
            parts=projected[job_id]
            if len(parts)<=100 and all(part['code']!='work-fetch' for part in parts):
                identity_keys=sorted({value for part in parts for value in (part['id'],part.get('_legacy_id')) if value})
                own=self.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:fetch' AND body::jsonb->>'fetch_job_identity'=ANY(%s) LIMIT 101",(identity_keys,)).fetchall()
                status=self.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:fetch' AND body::jsonb->>'status_job_identity'=ANY(%s) LIMIT 101",(identity_keys,)).fetchall()
                if len(own)<=100 and len(status)<=100:self.sync_fetch(job_id,[],now)
            processed.add(job_id)
        complex_jobs=[job_id for job_id,parts in projected.items() if len(parts)>1 and job_id in marked and job_id not in protected]
        for job_id in complex_jobs:
            item=items_by_id.get(job_id)
            allowed={obj['id'] for candidate in projected[job_id] for obj in candidate['objects'] if obj.get('kind')=='material'} | {item}
            previous=[];keys=set()
            for record in marked[job_id]:
                try:body=json.loads(record['body'])
                except (ValueError,TypeError):break
                canonical=body.get('fetch_job_identity') if isinstance(body,dict) else None
                objects=body.get('objects') if isinstance(body,dict) else None
                implicit=body.get('fetch_implicit_code') if isinstance(body,dict) else None
                if (not isinstance(canonical,str) or not canonical.startswith('job:work-fetch:') or canonical.count(':fetch:')!=1
                    or canonical.split(':fetch:',1)[1]!=item or canonical in keys or not isinstance(implicit,bool)
                    or body.get('fetch_job_id')!=str(job_id)
                    or (body.get('status_job_target') is not None and body.get('status_job_identity') is None)
                    or (body.get('status_job_identity') is not None and (body.get('status_job_identity')!=canonical or body.get('status_job_target')!=item))
                    or not isinstance(objects,list) or not objects or any(not isinstance(obj,dict) or obj.get('kind')!='material' or obj.get('id') not in allowed for obj in objects)):
                    break
                keys.add(canonical)
                previous.append(dict(id=canonical,code='work-fetch',objects=objects,fetch_job_id=str(job_id),fetch_implicit_code=implicit,_fetch_item=item))
            else:
                self.sync_fetch(job_id,previous,now)
                processed.add(job_id)
        if processed:
            projected={job_id:parts for job_id,parts in projected.items() if job_id not in processed}
            counts={};legacy_keys=set()
            for parts in projected.values():
                for candidate in parts:
                    counts[candidate['id']]=counts.get(candidate['id'],0)+1
                    if candidate.get('_legacy_id'):legacy_keys.add(candidate['_legacy_id'])
            identities=list(counts)
            if not identities:return []
        item_jobs: dict[str,tuple[int,dict[str,Any]]]={}
        overloaded_ids: set[int]=set()
        for job_id,candidates in projected.items():
            if len(candidates)!=1:continue
            candidate=candidates[0]
            tail=candidate['id'].split(':fetch:')
            if len(tail)==2 and tail[1]:item_jobs[tail[1]]=(job_id,candidate)
        if item_jobs:
            records=self.db.execute("""SELECT r.*,target.item FROM unnest(%s::text[]) target(item) CROSS JOIN LATERAL (
                SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND identity LIKE 'job:%%' AND body::jsonb->>'code'='work-fetch'
                AND body::jsonb->>'operation' IN ('producer:fetch','producer:status')
                AND substring(coalesce(body::jsonb->>'status_job_identity',identity) FROM ':fetch:(.*)$')=target.item LIMIT 101) r""",(list(item_jobs),)).fetchall()
            grouped: dict[int,list[Any]]={}
            for record in records:
                if record['item'] in item_jobs:grouped.setdefault(item_jobs[record['item']][0],[]).append(record)
            seen={record['id'] for record in records}
            for job_id,_ in item_jobs.values():
                for record in marked.get(job_id,[]):
                    if record['id'] not in seen:
                        grouped.setdefault(job_id,[]).append(record)
            occupied_legacy={row['identity'] for row in self.db.execute('SELECT identity FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(identities,))}
            updates: list[tuple[str,str,str]]=[]
            fallback: dict[int,list[dict[str,Any]]]={}
            for item,(job_id,candidate) in item_jobs.items():
                prior=grouped.get(job_id,[])
                if len(prior)>=101:overloaded_ids.add(job_id)
                if len(prior)!=1 or counts[candidate['id']]!=1:continue
                old=prior[0]
                try:body=json.loads(old['body'])
                except (TypeError,ValueError):continue
                if not isinstance(body,dict) or set(body)-allowed_body_fields or body.get('fetch_job_id') not in (None,str(job_id)):continue
                old_identity=body.get('fetch_job_identity',body.get('status_job_identity',old['identity']))
                if (not isinstance(old_identity,str) or not old_identity.startswith('job:work-fetch:') or old_identity.count(':fetch:')!=1
                    or old_identity.split(':fetch:',1)[1]!=item):continue
                if body.get('fetch_job_identity') is not None and body.get('status_job_identity') is not None and body['status_job_identity']!=body['fetch_job_identity']:continue
                objects=body.get('objects')
                allowed={obj['id'] for obj in candidate['objects'] if obj.get('kind')=='material'} | {item}
                if not isinstance(objects,list) or not objects or any(not isinstance(obj,dict) or obj.get('kind')!='material' or obj.get('id') not in allowed for obj in objects):continue
                if body.get('status_job_target') is not None and body.get('status_job_identity') is None:continue
                if body.get('status_job_identity') is not None and body.get('status_job_target')!=item:continue
                if candidate['code']!='work-fetch':
                    if body.get('fetch_job_id')!=str(job_id):continue  # Unmarked legacy has no proof of its former raw code.
                    implicit=body.get('fetch_implicit_code')
                    if not isinstance(implicit,bool):continue  # Legacy work-fetch may have been an explicit cause.
                    if not implicit:
                        fallback[job_id]=[dict(id=old_identity,code='work-fetch',objects=objects,fetch_job_id=str(job_id),fetch_implicit_code=False,_fetch_item=item)]
                        continue
                if candidate['id'] in occupied_legacy and candidate['id']!=old['identity']:
                    fallback[job_id]=[dict(id=old_identity,code='work-fetch',objects=objects,fetch_job_id=str(job_id),fetch_implicit_code=body.get('fetch_implicit_code'),_fetch_item=item)]
                    continue
                desired={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
                updates.append((old['id'],candidate['id'],json.dumps(desired)))
            if updates:
                self.db.execute('UPDATE reader_problems p SET identity=source.identity,body=source.body FROM unnest(%s::text[],%s::text[],%s::text[]) AS source(id,identity,body) WHERE p.id=source.id AND p.finished IS NULL',([r[0] for r in updates],[r[1] for r in updates],[r[2] for r in updates]))
                status_changed(self.db)
            for job_id,previous in fallback.items():self.sync_fetch(job_id,previous,now)
        occupied_rows={row['identity']:row for row in self.db.execute('SELECT id,identity,body FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(identities+list(legacy_keys),))}
        occupied=set(occupied_rows)
        isolated_rows: dict[str,list[Any]]={}
        for row in self.db.execute("""SELECT r.*,target.canonical FROM unnest(%s::text[]) target(canonical) CROSS JOIN LATERAL (
            SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:fetch'
            AND body::jsonb->>'fetch_job_identity'=target.canonical LIMIT 101) r""",(identities,)):
            isolated_rows.setdefault(row['canonical'],[]).append(row)
        isolated=set(isolated_rows)
        simple=[];complex_ids=set(overloaded_ids)
        def ambiguous(candidate: dict[str,Any]) -> bool:
            tail=candidate['id'][len('job:'+candidate['code']+':'):]
            kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
            found=[kind for kind in kinds if ':'+kind+':' in tail]
            return found!=['fetch'] or tail.count(':fetch:')!=1
        def already_current(candidate: dict[str,Any]) -> bool:
            canonical=candidate['id']
            matches=([occupied_rows[canonical]] if canonical in occupied_rows else [])+isolated_rows.get(canonical,[])
            if len(matches)!=1 or not self.fetch_record_matches(matches[0],candidate):return False
            body=json.loads(matches[0]['body'])
            desired={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
            if matches[0]['identity']!=canonical:desired['fetch_job_identity']=canonical
            return bool(body==desired)
        for job_id,candidates in projected.items():
            if job_id in overloaded_ids:continue
            if candidates and all(counts[c['id']]==1 and already_current(c) for c in candidates):
                continue
            if any(counts[c['id']]!=1 or c['id'] in occupied or c['id'] in isolated or c['id'] in legacy_keys or c.get('_legacy_id') in occupied or ambiguous(c) for c in candidates):
                complex_ids.add(job_id)
            else:
                simple.extend(candidates)
        added=[]
        for candidate in simple:
            body={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
            added.append((uuid.uuid4().hex,candidate['id'],'background',json.dumps(body),now))
        if added:
            self.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome)
                SELECT id,identity,origin,body,created,NULL,NULL FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::double precision[]) AS p(id,identity,origin,body,created)""",tuple([row[i] for row in added] for i in range(5)))
            status_changed(self.db)
        return [row for row in rows if row['id'] in complex_ids]

    def recover_fetch_isolated_work(self) -> None:
        """Repair event-owned work-fetch occurrences left by older recovery."""
        from oko.storage.transaction import begin_write
        cursor: str | None=None
        while True:
            query="""SELECT id,body::jsonb->>'fetch_job_id' AS marker FROM reader_problems
                WHERE finished IS NULL AND body::jsonb->>'operation'='producer:fetch' AND body::jsonb->>'code'='work-fetch'
                  AND body::jsonb->>'fetch_job_identity' IS NOT NULL AND body::jsonb->>'fetch_job_id' IS NOT NULL"""
            if cursor is not None:query+=""" AND (body::jsonb->>'fetch_job_id') COLLATE "C">%s"""
            query+=""" ORDER BY (body::jsonb->>'fetch_job_id') COLLATE "C",id COLLATE "C" LIMIT 100"""
            with self.db:
                begin_write(self.db)
                self.db.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),%s))",(746582093,))
                if self.db.execute("SELECT 1 FROM state WHERE key='fetch-isolated-work-recovered-v1'").fetchone():return
                records=self.db.execute(query,(cursor,) if cursor is not None else ()).fetchall()
                if not records:
                    self.db.execute("INSERT INTO state(key,value) VALUES ('fetch-isolated-work-recovered-v1','true') ON CONFLICT DO NOTHING")
                    return
                ids=set()
                for row in records:
                    marker=row['marker']
                    if isinstance(marker,str) and len(marker)<=20 and re.fullmatch(r'-?(?:0|[1-9][0-9]*)',marker):
                        number=int(marker)
                        if str(number)==marker and -(2**63)<=number<2**63:ids.add(number)
                jobs=self.db.execute("SELECT id FROM jobs WHERE kind='fetch' AND id=ANY(%s) ORDER BY id",(sorted(ids),)).fetchall() if ids else []
                if jobs:self.recover_fetch_batch(list(jobs),self.store.wall_clock())
            # The batch reconciles every saved occurrence for each selected Job.
            # Advance past the last complete Job even when it has >100 rows.
            cursor=records[-1]['marker']
            self.store.fault('fetch_isolated_work_recovery_batch')

    def protect_fetch_null_legacy(self, job_ids: list[int]) -> None:
        """Preserve exact occurrences, in bounded pages within the owning transaction."""
        if not job_ids:return
        jobs=self.db.execute("SELECT id,item FROM jobs WHERE id=ANY(%s) AND kind='fetch' AND item IS NOT NULL",(job_ids,)).fetchall()
        for job in jobs:
            for mode in ('legacy','marked'):
                cursor: tuple[str,str] | None=None
                while True:
                    if mode=='legacy':
                        query="""SELECT id,identity,md5(id) AS page_hash FROM reader_problems WHERE finished IS NULL AND identity LIKE 'job:%%'
                            AND body::jsonb->>'code'='work-fetch' AND body::jsonb->>'operation' IN ('producer:fetch','producer:status')
                            AND md5(substring(coalesce(body::jsonb->>'status_job_identity',identity) FROM ':fetch:(.*)$'))=md5(%s)
                            AND substring(coalesce(body::jsonb->>'status_job_identity',identity) FROM ':fetch:(.*)$')=%s"""
                        params: tuple[Any,...]=(job['item'],job['item'])
                    else:
                        query="""SELECT id,identity,md5(id) AS page_hash FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:fetch'
                            AND body::jsonb->>'code'='work-fetch' AND body::jsonb->>'fetch_job_identity' IS NOT NULL
                            AND body::jsonb->>'fetch_job_id' IS NOT NULL AND (body::jsonb->>'fetch_job_id') COLLATE "C"=%s"""
                        params=(str(job['id']),)
                    if cursor is not None:
                        if mode=='legacy':query+=' AND (md5(id),id COLLATE "C")>(%s,%s)';params=(*params,*cursor)
                        else:query+=' AND id COLLATE "C">%s';params=(*params,cursor[1])
                    order='md5(id),id COLLATE "C"' if mode=='legacy' else 'id COLLATE "C"'
                    rows=self.db.execute(query+' ORDER BY '+order+' LIMIT 400',params).fetchall()
                    if not rows:break
                    markers=[('fetch-null-protected:'+hashlib.sha256(row['id'].encode()).hexdigest(),json.dumps(['oko-fetch-null-v2',row['id'],row['identity']])) for row in rows]
                    self.db.execute('INSERT INTO state(key,value) SELECT key,value FROM unnest(%s::text[],%s::text[]) v(key,value) ON CONFLICT(key) DO NOTHING',([key for key,_ in markers],[value for _,value in markers]))
                    saved={row['key']:row['value'] for row in self.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',([key for key,_ in markers],))}
                    if any(saved.get(key)!=value for key,value in markers):raise RuntimeError('Conflicting fetch-null protection marker')
                    cursor=(rows[-1]['page_hash'],rows[-1]['id'])
                    self.store.fault('fetch_null_protection_batch')

    def recover_fetch_null(self) -> None:
        if self.db.execute("SELECT 1 FROM state WHERE key='fetch-null-recovered-v1'").fetchone():return
        cursor: int | None=None
        while True:
            query="SELECT id FROM jobs WHERE kind='fetch' AND item IS NOT NULL AND item<>'' AND problem_code IS NULL AND error IS NOT NULL"
            if cursor is not None:query+=' AND id>%s'
            rows=self.db.execute(query+' ORDER BY id LIMIT 100',(cursor,) if cursor is not None else ()).fetchall()
            if not rows:break
            with self.db:
                from oko.storage import memberships
                memberships.lock_graph(self.db)
                if getattr(self.store,'youtube_admission_ready',False):
                    from oko.storage.youtube_admission_jobs import capture_fetch_flag_changes
                    capture_fetch_flag_changes(self.store,[row['id'] for row in rows])
                markers=[str(row['id']) for row in rows]
                overflows=self.db.execute("""SELECT target.marker FROM unnest(%s::text[]) target(marker) CROSS JOIN LATERAL (
                    SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:fetch'
                    AND body::jsonb->>'code'='work-fetch' AND body::jsonb->>'fetch_job_identity' IS NOT NULL
                    AND body::jsonb->>'fetch_job_id' IS NOT NULL AND (body::jsonb->>'fetch_job_id') COLLATE "C"=target.marker COLLATE "C"
                    LIMIT 101) r GROUP BY target.marker HAVING count(*)>100""",(markers,)).fetchall()
                overflow_ids={int(row['marker']) for row in overflows}
                if overflow_ids:
                    self.protect_fetch_null_legacy(list(overflow_ids))
                    self.db.execute('UPDATE jobs SET fetch_null_owned=false,fetch_null_ambiguous=true WHERE id=ANY(%s)',(list(overflow_ids),))
                bounded_rows=[row for row in rows if row['id'] not in overflow_ids]
                complex_rows=self.recover_fetch_batch(bounded_rows,self.store.wall_clock()) if bounded_rows else []
                for row in complex_rows:self.sync_fetch(row['id'],[],self.store.wall_clock())
                from oko.storage.fetch_job_problems import managed_candidates_many
                eligible=managed_candidates_many(self.store,[row['id'] for row in rows],self.store.wall_clock())
                complex_ids={row['id'] for row in complex_rows} | overflow_ids
                owned=[job for job,candidates in eligible.items() if candidates and job not in complex_ids]
                if owned:self.db.execute('UPDATE jobs SET fetch_null_owned=true,fetch_null_ambiguous=false WHERE id=ANY(%s) AND problem_code IS NULL AND error IS NOT NULL',(owned,))
            cursor=rows[-1]['id']
            self.store.fault('fetch_null_recovery_batch')
        with self.db:self.db.execute("INSERT INTO state(key,value) VALUES ('fetch-null-recovered-v1','true') ON CONFLICT DO NOTHING")

    def recover_fetch_identity_unconfirmed(self) -> None:
        from oko.storage.transaction import begin_write
        from oko.storage.fetch_job_problems import managed_candidates_many
        cursor: int | None=None
        while True:
            with self.db:
                begin_write(self.db)
                self.db.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),%s))',(746582095,))
                if self.db.execute("SELECT 1 FROM state WHERE key='fetch-identity-unconfirmed-recovered-v1'").fetchone():return
                from oko.storage import memberships
                memberships.lock_graph(self.db)
                sql="SELECT id FROM jobs WHERE kind='fetch' AND item IS NOT NULL AND item<>'' AND problem_code='identity-unconfirmed'"
                if cursor is not None:sql+=' AND id>%s'
                jobs=self.db.execute(sql+' ORDER BY id LIMIT 100',(cursor,) if cursor is not None else ()).fetchall()
                if not jobs:
                    self.db.execute("INSERT INTO state(key,value) VALUES ('fetch-identity-unconfirmed-recovered-v1','true') ON CONFLICT DO NOTHING")
                    return
                ids=[row['id'] for row in jobs]
                now=self.store.wall_clock()
                previous=managed_candidates_many(self.store,ids,now,include_suppressed=True)
                current=managed_candidates_many(self.store,ids,now)
                mixed={job_id:previous.get(job_id,[]) for job_id in ids if current.get(job_id)
                    and {candidate['id'] for candidate in current[job_id]}!={candidate['id'] for candidate in previous.get(job_id,[])}}
                if mixed:self.sync_identity_profile_transition(mixed,now)
                active=[row for row in jobs if current.get(row['id']) and row['id'] not in mixed]
                if active:
                    complex_rows=self.recover_fetch_batch(active,now)
                    complex_ids={row['id'] for row in complex_rows}
                    simple_ids=[row['id'] for row in active if row['id'] not in complex_ids]
                    if simple_ids:self.db.execute("UPDATE jobs SET identity_event_owned=true WHERE id=ANY(%s) AND problem_code='identity-unconfirmed' AND NOT fetch_null_ambiguous",(simple_ids,))
                    for row in complex_rows:self.sync_fetch(row['id'],previous.get(row['id'],[]),now)
                suppressed={job_id:previous.get(job_id,[]) for job_id in ids if not current.get(job_id)}
                if suppressed:self.sync_identity_profile_transition(suppressed,now)
                cursor=ids[-1]
            self.store.fault('fetch_identity_unconfirmed_recovery_batch')

    def recover_finished_foreign_fetch_deletions(self) -> None:
        """Resolve only legacy records with one provably finished fetch owner."""
        from oko.storage import memberships
        cursor: tuple[float,str] | None = None
        while True:
            with self.db:
                memberships.lock_graph(self.db)
                marker = 'fetch-delete-legacy-finished-v1'
                if self.db.execute('SELECT 1 FROM state WHERE key=%s',(marker,)).fetchone():
                    return
                sql = """SELECT id,identity,body,created FROM reader_problems
                    WHERE %s=1 AND finished IS NULL AND identity LIKE 'job:%%'
                    AND coalesce(body::jsonb->>'fetch_job_identity',identity) LIKE 'job:%%:fetch:%%'
                    AND body::jsonb->>'operation' IN ('producer:fetch','producer:status')
                    AND body::jsonb->>'code' IS NOT NULL AND body::jsonb->>'code'<>''
                    AND body::jsonb->>'code' NOT LIKE 'source-%%'
                    AND body::jsonb->>'code' NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch','work-fetch')
                    AND body::jsonb->>'operation'='producer:status'
                    AND body::jsonb->>'code' IN ('delete','feed-delete','work-delete','work-feed-delete')
                    AND identity LIKE '%%:fetch:%%' AND identity LIKE '%%:delete:%%'"""
                if cursor is not None:
                    sql += ' AND (created,id COLLATE "C")>(%s,%s)'
                rows=self.db.execute(sql+' ORDER BY created,id COLLATE "C" LIMIT 100',(1,*cursor) if cursor is not None else (1,)).fetchall()
                if not rows:
                    self.db.execute('INSERT INTO state(key,value) VALUES (%s,%s) ON CONFLICT DO NOTHING',(marker,'true'))
                    return
                records: dict[str,dict[str,Any]]={}
                saved_rows: dict[str,Any]={}
                for row in rows:
                    try: body=json.loads(row['body'])
                    except (TypeError,ValueError):continue
                    if not isinstance(body,dict) or any(body.get(key) is not None for key in ('fetch_job_identity','fetch_job_id','status_job_identity','status_job_target','media_job_identity','processing_job_identity','read_job_identity','delete_job_identity','source_group_identity')):continue
                    code=body.get('code'); identity=row['identity']
                    if not isinstance(code,str) or not identity.startswith('job:'+code+':'):continue
                    tail=identity[len('job:'+code+':'):]
                    kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
                    found=[kind for kind in kinds if ':'+kind+':' in tail]
                    if found!=['fetch','delete'] or tail.count(':fetch:')!=1 or tail.count(':delete:')!=1:continue
                    objects=body.get('objects')
                    if not isinstance(objects,list) or len(objects)!=1 or not isinstance(objects[0],dict) or objects[0].get('kind')!='material' or not isinstance(objects[0].get('id'),str) or not objects[0]['id']:continue
                    records[identity]=body;saved_rows[identity]=row
                proven=self._finished_foreign_legacy_jobs(records)
                if proven:
                    selected=[saved_rows[identity] for identity in sorted(proven)]
                    # Take row locks before evaluating markers: after waiting for a
                    # concurrent owner, the following UPDATE gets a fresh snapshot.
                    self.db.execute('SELECT id FROM reader_problems WHERE id=ANY(%s) ORDER BY id FOR UPDATE',([row['id'] for row in selected],)).fetchall()
                    changed=self.db.execute("""UPDATE reader_problems p SET finished=%s,outcome='resolved'
                        FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[]) AS expected(id,identity,body,marker)
                        WHERE p.id=expected.id AND p.identity COLLATE "C"=expected.identity COLLATE "C"
                        AND p.body COLLATE "C"=expected.body COLLATE "C" AND p.finished IS NULL
                        AND NOT EXISTS (SELECT 1 FROM state WHERE key=expected.marker)""",
                        (self.store.wall_clock(),[row['id'] for row in selected],
                         [row['identity'] for row in selected],[row['body'] for row in selected],
                         ['fetch-null-protected:'+hashlib.sha256(row['id'].encode()).hexdigest() for row in selected])).rowcount
                    if changed:status_changed(self.db)
                cursor=(rows[-1]['created'],rows[-1]['id'])
            self.store.fault('fetch_delete_finished_legacy_recovery_batch')

    def recover_fetch_problems(self) -> None:
        if self.db.execute("SELECT 1 FROM state WHERE key='fetch-problems-recovered-v2'").fetchone():
            self.recover_fetch_isolated_work()
            return
        problem_cursor: tuple[float,str] | None=None
        while True:
            query="""SELECT id,identity,body,created FROM reader_problems WHERE finished IS NULL AND identity LIKE 'job:%%' AND coalesce(body::jsonb->>'fetch_job_identity',identity) LIKE 'job:%%:fetch:%%' AND body::jsonb->>'operation' IN ('producer:fetch','producer:status')
                AND body::jsonb->>'code' IS NOT NULL AND body::jsonb->>'code'<>'' AND body::jsonb->>'code' NOT LIKE 'source-%%'
                AND body::jsonb->>'code' NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch','work-fetch')"""
            if problem_cursor is not None:query+=' AND (created,id COLLATE "C")>(%s,%s)'
            records=self.db.execute(query+' ORDER BY created,id COLLATE "C" LIMIT 100',problem_cursor if problem_cursor is not None else ()).fetchall()
            if not records:break
            parsed=[]
            for record in records:
                try:body=json.loads(record['body'])
                except (ValueError,TypeError):continue
                if not isinstance(body,dict) or not isinstance(body.get('code'),str) or not isinstance(body.get('objects'),list):continue
                if not body['objects'] or any(not isinstance(obj,dict) or obj.get('kind')!='material' or not isinstance(obj.get('id'),str) or not obj['id'] for obj in body['objects']):continue
                code=body['code'];identity=body.get('fetch_job_identity',record['identity']);prefix='job:'+code+':'
                if not isinstance(identity,str) or not identity.startswith(prefix):continue
                tail=identity[len(prefix):]
                kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
                found=[kind for kind in kinds if ':'+kind+':' in tail]
                if found!=['fetch'] or tail.count(':fetch:')!=1:continue
                feed,item=tail.split(':fetch:',1)
                if not feed or not item:continue
                parsed.append((record,body,feed,item))
            items=sorted({item for _,_,_,item in parsed});feeds=sorted({feed for _,_,feed,_ in parsed})
            with self.db:
                jobs={row['item']:row for row in self.db.execute("SELECT id,item,problem_code FROM jobs WHERE kind='fetch' AND item=ANY(%s)",(items,))} if items else {}
                from oko.storage.fetch_job_problems import managed_candidates_many
                expected=managed_candidates_many(self.store,[row['id'] for row in jobs.values()],self.store.wall_clock()) if jobs else {}
                affected={obj['id'] for _,body,_,_ in parsed for obj in body['objects']}
                possible_active=self.active_fetch_roots(affected)
                linked=self.fetch_linked_pairs(set(items),affected)
                publications={row['id']:row for row in self.db.execute('SELECT id,viewed,source_deleted FROM publications WHERE id=ANY(%s)',(items,))} if items else {}
                alive={row['id'] for row in self.db.execute("SELECT id FROM feeds WHERE id=ANY(%s) AND connection!='deleting'",(feeds,))} if feeds else set()
                resolved_ids: list[str]=[]
                irrelevant_ids: list[str]=[]
                updates: list[tuple[str,str]]=[]
                pending_jobs: dict[int,list[dict[str,Any]]]={}
                for record,body,feed,item in parsed:
                    job=jobs.get(item)
                    if body['code']=='work-fetch' and (not job or job['problem_code'] is None):continue
                    if job:
                        marked=body.get('fetch_job_id')==str(job['id'])
                        mentioned={obj['id'] for obj in body['objects']}
                        expected_objects={obj['id'] for candidate in expected.get(job['id'],[]) for obj in candidate['objects']}
                        if not mentioned.issubset(expected_objects | {item}) or (not marked and item not in mentioned and not mentioned.intersection(expected_objects)):
                            continue  # Every saved object must be attributable to this Job.
                        canonical=body.get('fetch_job_identity',record['identity'])
                        current=next((c for c in expected.get(job['id'],[]) if c['id']==canonical),None)
                        if current is not None and body.get('fetch_job_id')==str(job['id']):
                            desired={key:value for key,value in current.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
                            if body.get('fetch_job_identity') is not None:desired['fetch_job_identity']=canonical
                            if body==desired:continue
                        candidate=dict(id=record['identity'],code=body['code'],objects=body['objects'],fetch_job_id=str(job['id']))
                        if self.fetch_record_matches(record,candidate):
                            if current is not None:
                                desired={key:value for key,value in current.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
                                if body.get('fetch_job_identity') is not None:desired['fetch_job_identity']=canonical
                                updates.append((record['id'],json.dumps(desired)))
                            else:
                                pending_jobs.setdefault(job['id'],[]).append(candidate)
                    else:
                        if any(obj['id'] in possible_active for obj in body['objects']):
                            continue  # The stored identity may be misleading; preserve ambiguous evidence.
                        if any(obj['id']!=item and (obj['id'],item) not in linked for obj in body['objects']):
                            continue  # Every stored object needs evidence of its relationship to this Job.
                        publication=publications.get(item)
                        irrelevant=feed not in alive or bool(publication and (publication['viewed'] or publication['source_deleted']))
                        (irrelevant_ids if irrelevant else resolved_ids).append(record['id'])
                for job_id,previous in pending_jobs.items():
                    self.sync_fetch(job_id,previous,self.store.wall_clock())
                if updates:
                    self.db.execute('UPDATE reader_problems p SET body=source.body FROM unnest(%s::text[],%s::text[]) AS source(id,body) WHERE p.id=source.id AND p.finished IS NULL',( [row[0] for row in updates], [row[1] for row in updates]))
                    status_changed(self.db)
                for outcome,ids in (('resolved',resolved_ids),('irrelevant',irrelevant_ids)):
                    if ids:
                        self.db.execute('UPDATE reader_problems SET finished=%s,outcome=%s WHERE id=ANY(%s) AND finished IS NULL',(self.store.wall_clock(),outcome,ids))
                        status_changed(self.db)
            problem_cursor=(records[-1]['created'],records[-1]['id'])
            self.store.fault('fetch_problem_record_recovery_batch')
        cursor: int | None=None
        while True:
            query="""SELECT id FROM jobs WHERE kind='fetch' AND item IS NOT NULL AND item<>'' AND problem_code IS NOT NULL
                AND problem_code NOT LIKE 'source-%%' AND problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')"""
            if cursor is not None:query+=' AND id>%s'
            rows=self.db.execute(query+' ORDER BY id LIMIT 100',(cursor,) if cursor is not None else ()).fetchall()
            if not rows:break
            with self.db:
                complex_rows=self.recover_fetch_batch(list(rows),self.store.wall_clock())
                for row in complex_rows:self.sync_fetch(row['id'],[],self.store.wall_clock())
            cursor=rows[-1]['id']
            self.store.fault('fetch_problem_recovery_batch')
        with self.db:self.db.execute("INSERT INTO state(key,value) VALUES ('fetch-problems-recovered-v2','true') ON CONFLICT DO NOTHING")
        self.recover_fetch_isolated_work()

    def fetch_record_matches(self, record: Any, candidate: dict[str, Any]) -> bool:
        try:body=json.loads(record['body'])
        except (TypeError,ValueError):return False
        if not isinstance(body,dict) or body.get('code')!=candidate['code']:
            return False
        operation=body.get('operation')
        objects=body.get('objects')
        if not isinstance(objects,list) or not objects or any(not isinstance(obj,dict) or obj.get('kind')!='material' or not isinstance(obj.get('id'),str) for obj in objects):return False
        allowed={(obj.get('kind'),obj.get('id')) for obj in candidate['objects'] if isinstance(obj,dict)}
        if not all((obj.get('kind'),obj.get('id')) in allowed for obj in objects):return False
        status_identity=body.get('status_job_identity')
        if status_identity is not None:
            return bool(operation=='producer:fetch' and status_identity==candidate['id'] and candidate.get('_fetch_item') is not None and body.get('status_job_target')==candidate['_fetch_item'])
        marker=body.get('fetch_job_id')
        if marker is not None:
            return bool(operation=='producer:fetch' and marker==candidate['fetch_job_id'] and body.get('fetch_job_identity',candidate['id'])==candidate['id'])
        if operation not in (None,'producer:status','producer:fetch'):
            return False
        identity=record['identity']
        prefix='job:'+candidate['code']+':'
        if not identity.startswith(prefix):return False
        tail=identity[len(prefix):]
        kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
        found=[kind for kind in kinds if ':'+kind+':' in tail]
        if found!=['fetch'] or tail.count(':fetch:')!=1:return False
        return True

    def fetch_record(self, candidate: dict[str, Any]) -> Any:
        rows=self.db.execute("SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:fetch' AND body::jsonb->>'fetch_job_identity'=%s",(candidate['id'],)).fetchall()
        for row in rows:
            if self.fetch_record_matches(row,candidate):return row
        canonical_row=self.db.execute('SELECT id,identity,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(candidate['id'],)).fetchone()
        return canonical_row if canonical_row and self.fetch_record_matches(canonical_row,candidate) else None

    def protect_foreign_identity_fetch(self, row: Any) -> None:
        """Protect an occupied foreign occurrence without changing its contents."""
        key='fetch-null-protected:'+hashlib.sha256(row['id'].encode()).hexdigest()
        value=json.dumps(['oko-fetch-null-v2',row['id'],row['identity']])
        self.db.execute('INSERT INTO state(key,value) VALUES (%s,%s) ON CONFLICT DO NOTHING',(key,value))
        saved=self.db.execute('SELECT value FROM state WHERE key=%s',(key,)).fetchone()
        if not saved or saved['value']!=value:raise RuntimeError('Conflicting fetch protection marker')

    def sync_fetch(self, job_id: int, previous: list[dict[str,Any]], now: float, *, allow_rekey: bool = True) -> None:
        if getattr(self.store,'youtube_admission_ready',False):
            from oko.storage import memberships
            from oko.storage.youtube_admission_jobs import capture_fetch_flag_changes
            memberships.lock_graph(self.db)
            capture_fetch_flag_changes(self.store,[job_id])
        from oko.storage.fetch_job_problems import managed_candidates
        candidates=managed_candidates(self.store,job_id,now)
        self.db.execute('UPDATE jobs SET fetch_null_owned=(%s=1) WHERE id=%s AND problem_code IS NULL AND error IS NOT NULL AND fetch_null_owned IS DISTINCT FROM (%s=1)',(bool(candidates),job_id,bool(candidates)))
        legacy=self.db.execute("""SELECT r.id FROM jobs j CROSS JOIN LATERAL (
            SELECT id FROM reader_problems WHERE finished IS NULL AND identity LIKE 'job:%%' AND body::jsonb->>'code'='work-fetch'
            AND body::jsonb->>'operation' IN ('producer:fetch','producer:status')
            AND substring(coalesce(body::jsonb->>'status_job_identity',identity) FROM ':fetch:(.*)$')=j.item LIMIT 101) r
            WHERE j.id=%s AND j.kind='fetch' AND (j.fetch_null_ambiguous OR (j.problem_code IS NULL AND j.error IS NOT NULL))""",(job_id,)).fetchall()
        if len(legacy)>=101:
            self.protect_fetch_null_legacy([job_id])
            self.db.execute('UPDATE jobs SET fetch_null_owned=false,fetch_null_ambiguous=true,identity_event_owned=false WHERE id=%s',(job_id,))
            return
        if previous and not candidates:
            from oko.storage.fetch_job_problems import candidates as all_candidates
            handoff=all_candidates(self.store,job_id,now)
            job=self.db.execute("SELECT item FROM jobs WHERE id=%s",(job_id,)).fetchone()
            candidates=[dict({k:v for k,v in candidate.items() if k!='_code_present'},fetch_job_id=str(job_id),_handoff_to_get=True,_handoff_target=job['item'])
                        for candidate in handoff if job and not candidate['_code_present'] and candidate['code']=='work-fetch']
        keys={candidate['id'] for candidate in previous+candidates}
        keys.update(candidate['_legacy_id'] for candidate in candidates if candidate.get('_legacy_id'))
        rows=self.db.execute('SELECT * FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(sorted(keys),)).fetchall() if keys else []
        by_identity={row['identity']:row for row in rows}
        isolated: dict[str,list[Any]]={}
        if keys:
            for field in ('fetch_job_identity','status_job_identity'):
                query=f"""SELECT r.*,target.canonical FROM unnest(%s::text[]) target(canonical) CROSS JOIN LATERAL (
                    SELECT * FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:fetch'
                    AND body::jsonb->>'{field}'=target.canonical LIMIT 101) r"""
                for row in self.db.execute(query,(sorted(keys),)):
                    isolated.setdefault(row['canonical'],[]).append(row)
            if any(len(rows)>=101 for rows in isolated.values()):
                self.protect_fetch_null_legacy([job_id])
                self.db.execute('UPDATE jobs SET fetch_null_owned=false,fetch_null_ambiguous=true,identity_event_owned=false WHERE id=%s',(job_id,))
                return  # Preserve ambiguous records and keep NULL failures visible through GET.

        self.db.execute('UPDATE jobs SET fetch_null_ambiguous=false WHERE id=%s AND fetch_null_ambiguous',(job_id,))
        def record(candidate: dict[str,Any]) -> Any:
            for saved in isolated.get(candidate['id'],[]):
                if self.fetch_record_matches(saved,candidate):return saved
            saved=by_identity.get(candidate['id'])
            return saved if saved and self.fetch_record_matches(saved,candidate) else None
        for candidate in previous:
            matched=record(candidate)
            candidate['id']=matched['identity'] if matched else ''
        for candidate in candidates:
            legacy=candidate.pop('_legacy_id',None)
            if legacy:
                legacy_candidate=dict(candidate,id=legacy)
                matched=record(legacy_candidate)
                if matched and not any(entry['id']==matched['identity'] for entry in previous):previous.append(dict(legacy_candidate,id=matched['identity']))
        for candidate in candidates:
            canonical=candidate['id']
            matched=record(candidate)
            if matched and matched['identity']!=canonical:
                candidate['id']=matched['identity'];candidate['fetch_job_identity']=canonical
                continue
            occupied=by_identity.get(canonical)
            tail=canonical[len('job:'+candidate['code']+':'):]
            kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
            found=[kind for kind in kinds if ':'+kind+':' in tail]
            if not matched and (occupied or found!=['fetch'] or tail.count(':fetch:')!=1):
                if occupied and candidate['code']=='identity-unconfirmed':self.protect_foreign_identity_fetch(occupied)
                digest=hashlib.sha256(canonical.encode('utf-8')).hexdigest()
                for _ in range(3):
                    alternate='job:fetch-isolated:'+digest+':'+new_read_problem_token()
                    if not self.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(alternate,)).fetchone():
                        candidate['id']=alternate;candidate['fetch_job_identity']=canonical
                        break
                else:raise RuntimeError('Unable to allocate fetch problem identity')
        current={candidate['id']:candidate for candidate in candidates}
        old_ids={candidate['id'] for candidate in previous if candidate['id'] and candidate['id'] not in current}
        old_candidates={candidate['id']:candidate for candidate in previous if candidate['id']}
        if current and old_ids and allow_rekey:
            existing={row['identity']:row for row in self.db.execute('SELECT * FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(sorted(old_ids | set(current)),))}
            assigned: dict[str,list[Any]]={}
            for old in sorted((existing[identity] for identity in old_ids if identity in existing),key=lambda row:(row['created'],row['id'])):
                old_body=json.loads(old['body'])
                if not isinstance(old_body,dict):continue
                same_code=[identity for identity,candidate in current.items() if candidate['code']==old_body.get('code') or (old_candidates[old['identity']].get('fetch_job_id')==candidate.get('fetch_job_id') and ((old_body.get('code')=='work-fetch' and old_candidates[old['identity']].get('fetch_implicit_code') is True) or (candidate['code']=='work-fetch' and candidate.get('fetch_implicit_code') is True)))]
                if not same_code:continue
                old_objects=old_body.get('objects',[])
                matching=[identity for identity in same_code if any(obj in current[identity]['objects'] for obj in old_objects if isinstance(obj,dict))]
                target=(sorted(matching)[0] if matching else sorted(same_code)[0])
                assigned.setdefault(target,[]).append(old)
            close_ids: list[str]=[]
            rekeys: list[tuple[str,str]]=[]
            for target,prior_rows in assigned.items():
                occupied=existing.get(target)
                own=occupied if occupied and self.fetch_record_matches(occupied,current[target]) else None
                choices=sorted([*prior_rows,*([own] if own else [])],key=lambda row:(row['created'],row['id']))
                if occupied and own is None:continue
                winner=choices[0]
                close_ids.extend(row['id'] for row in choices[1:])
                if winner['identity']!=target:rekeys.append((winner['id'],target))
            if close_ids:
                self.db.execute("UPDATE reader_problems SET finished=%s,outcome='irrelevant' WHERE id=ANY(%s) AND finished IS NULL",(now,close_ids))
            if rekeys:
                self.db.execute('UPDATE reader_problems p SET identity=source.identity FROM unnest(%s::text[],%s::text[]) AS source(id,identity) WHERE p.id=source.id AND p.finished IS NULL',([r[0] for r in rekeys],[r[1] for r in rekeys]))
            if close_ids or rekeys:status_changed(self.db)
        closing=[prior for prior in previous if prior['id'] and prior['id'] not in current]
        owners=sorted({prior['fetch_owner'] for prior in closing if prior.get('fetch_owner')})
        materials=sorted({obj['id'] for prior in closing for obj in prior['objects'] if obj.get('kind')=='material'})
        alive={row['id'] for row in self.db.execute("SELECT id FROM feeds WHERE id=ANY(%s) AND connection!='deleting'",(owners,))} if owners else set()
        removed={row['id'] for row in self.db.execute('SELECT id FROM publications WHERE id=ANY(%s) AND (viewed=1 OR source_deleted)',(materials,))} if materials else set()
        finished: dict[str,list[str]]={'resolved':[],'irrelevant':[]}
        for prior in closing:
            owner=prior.get('fetch_owner')
            irrelevant=bool(owner and owner not in alive) or any(obj.get('kind')=='material' and obj['id'] in removed for obj in prior['objects'])
            finished['irrelevant' if irrelevant else 'resolved'].append(prior['id'])
        for outcome,identities in finished.items():
            if identities:
                changed=self.db.execute('UPDATE reader_problems SET finished=%s,outcome=%s WHERE identity=ANY(%s) AND finished IS NULL',(now,outcome,identities)).rowcount
                if changed:status_changed(self.db)
        saved={row['identity']:row for row in self.db.execute('SELECT identity,body FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(sorted(current),))} if current else {}
        updates=[];added=[]
        for identity,candidate in current.items():
            body={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_handoff_to_get','_handoff_target')}
            if candidate.get('_handoff_to_get'):
                body.pop('fetch_job_id',None)
                body.pop('fetch_owner',None)
                if body.get('fetch_job_identity') is not None:
                    body['status_job_identity']=body.pop('fetch_job_identity')
                    body['status_job_target']=candidate['_handoff_target']
            body['objects']=[{key:value for key,value in obj.items() if key!='label'} for obj in candidate['objects']]
            encoded=json.dumps(body)
            if identity in saved:
                if saved[identity]['body']!=encoded:updates.append((identity,encoded))
            else:added.append((uuid.uuid4().hex,identity,'background',encoded,now))
        if updates:
            self.db.execute('UPDATE reader_problems p SET body=source.body FROM unnest(%s::text[],%s::text[]) AS source(identity,body) WHERE p.identity=source.identity AND p.finished IS NULL',([r[0] for r in updates],[r[1] for r in updates]))
        if added:
            self.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome)
                SELECT id,identity,origin,body,created,NULL,NULL FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::double precision[]) AS p(id,identity,origin,body,created)""",tuple([row[i] for row in added] for i in range(5)))
        if updates or added:status_changed(self.db)
        self.db.execute("UPDATE jobs SET identity_event_owned=(problem_code IS NOT DISTINCT FROM 'identity-unconfirmed' AND item IS NOT NULL AND item<>'' AND NOT fetch_null_ambiguous) WHERE id=%s AND identity_event_owned IS DISTINCT FROM (problem_code IS NOT DISTINCT FROM 'identity-unconfirmed' AND item IS NOT NULL AND item<>'' AND NOT fetch_null_ambiguous)",(job_id,))

    def active_media_roots(self, items: set[str]) -> set[str]:
        result: set[str]=set()
        ordered=sorted(items)
        for start in range(0,len(ordered),400):
            result.update(row['root'] for row in self.db.execute("""SELECT target.root FROM unnest(%s::text[]) target(root)
                WHERE EXISTS (SELECT 1 FROM jobs j WHERE j.kind='download_media' AND j.item=target.root AND j.status IN ('pending','running','failed','unavailable','blocked') AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL))
                   OR EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=target.root AND j.status IN ('pending','running','failed','unavailable','blocked') AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL))
                   OR EXISTS (SELECT 1 FROM publication_links l JOIN media_files m ON m.publication_id=l.target_publication_id JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE l.source_publication_id=target.root AND j.status IN ('pending','running','failed','unavailable','blocked') AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL))""",(ordered[start:start+400],)))
        return result

    def recover_media_batch(self, rows: list[Any], now: float, *, include_subtitles: bool = False, new_ids_out: set[str] | None = None) -> list[Any]:
        """Persist independent first occurrences; address legacy collisions separately."""
        from oko.storage.media_job_problems import managed_candidates_many
        projected=managed_candidates_many(self.store,[row['id'] for row in rows],now,include_subtitles=include_subtitles)
        counts: dict[str,int]={};legacy_keys: set[str]=set()
        for candidates in projected.values():
            for candidate in candidates:
                identity=candidate['id'];counts[identity]=counts.get(identity,0)+1
                if candidate.get('_legacy_id'):legacy_keys.add(candidate['_legacy_id'])
        identities=list(counts)
        if not identities:return []
        item_jobs: dict[str,tuple[int,list[dict[str,Any]]]]={}
        for job_id,candidates in projected.items():
            if not candidates:continue
            item=candidates[0]['media_file_id']
            if item and all(c['media_file_id']==item and len(c['id'].split(':download_media:'))==2 for c in candidates):
                item_jobs[item]=(job_id,candidates)
        if item_jobs:
            publications={row['id']:row['publication_id'] for row in self.db.execute('SELECT id,publication_id FROM media_files WHERE id=ANY(%s)',(list(item_jobs),))}
            records=self.db.execute("""SELECT id,identity,body,substring(coalesce(body::jsonb->>'media_job_identity',body::jsonb->>'status_job_identity',identity) FROM ':download_media:(.*)$') AS item
                FROM reader_problems WHERE finished IS NULL AND identity LIKE 'job:%%' AND body::jsonb->>'code'='work-download_media'
                AND body::jsonb->>'operation' IN ('producer:download_media','producer:status')
                AND substring(coalesce(body::jsonb->>'media_job_identity',body::jsonb->>'status_job_identity',identity) FROM ':download_media:(.*)$')=ANY(%s)""",(list(item_jobs),)).fetchall()
            grouped: dict[int,list[Any]]={}
            for record in records:
                if record['item'] in item_jobs:grouped.setdefault(item_jobs[record['item']][0],[]).append(record)
            occupied_legacy={row['identity'] for row in self.db.execute('SELECT identity FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(identities,))}
            updates: list[tuple[str,str,str]]=[]
            fallback: dict[int,list[dict[str,Any]]]={}
            matches: list[tuple[str,int,dict[str,Any],Any,dict[str,Any],str,list[Any]]] = []
            for item,(job_id,candidates) in item_jobs.items():
                for old in grouped.get(job_id,[]):
                    try:body=json.loads(old['body'])
                    except (TypeError,ValueError):continue
                    if not isinstance(body,dict) or body.get('media_job_id') not in (None,str(job_id)) or (body.get('media_job_id') is not None and body.get('media_file_id')!=item):continue
                    old_identity=body.get('media_job_identity',body.get('status_job_identity',old['identity']))
                    if not isinstance(old_identity,str) or not old_identity.startswith('job:work-download_media:') or old_identity.count(':download_media:')!=1:continue
                    objects=body.get('objects')
                    if not isinstance(objects,list) or not objects or any(not isinstance(obj,dict) or obj.get('kind')!='material' or not isinstance(obj.get('id'),str) for obj in objects):continue
                    mentioned={obj['id'] for obj in objects}
                    linked=[c for c in candidates if mentioned.intersection(obj['id'] for obj in c['objects'] if obj.get('kind')=='material')]
                    if len(linked)==1:candidate=linked[0]
                    elif len(candidates)==1:candidate=candidates[0]
                    else:continue
                    if counts[candidate['id']]!=1:continue
                    old_orphan=(body.get('media_orphan') is True and body.get('media_file_id')==item and body.get('media_job_id')==str(job_id)) or (body.get('media_job_id') is None and objects==[dict(kind='material',id=item)])
                    allowed={obj['id'] for obj in candidate['objects'] if obj.get('kind')=='material'} | ({item} if old_orphan else set())
                    if item in publications:allowed.add(publications[item])
                    if not mentioned.issubset(allowed):continue
                    if body.get('status_job_identity') is not None and body.get('status_job_target')!=item:continue
                    if candidate['code']!='work-download_media':
                        if body.get('media_job_id')!=str(job_id):continue
                        if not isinstance(body.get('media_implicit_code'),bool):continue
                    matches.append((item,job_id,candidate,old,body,old_identity,objects))
            target_counts: dict[str,int]={}
            for _,_,candidate,_,_,_,_ in matches:target_counts[candidate['id']]=target_counts.get(candidate['id'],0)+1
            for item,job_id,candidate,old,body,old_identity,objects in matches:
                if target_counts[candidate['id']]!=1:continue
                if candidate['code']!='work-download_media' and body['media_implicit_code'] is False:
                    fallback.setdefault(job_id,[]).append(dict(id=old_identity,code='work-download_media',objects=objects,media_job_id=str(job_id),media_implicit_code=False,media_file_id=item))
                    continue
                if candidate['id'] in occupied_legacy and candidate['id']!=old['identity']:
                    fallback.setdefault(job_id,[]).append(dict(id=old_identity,code='work-download_media',objects=objects,media_job_id=str(job_id),media_implicit_code=body.get('media_implicit_code'),media_file_id=item))
                    continue
                desired={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
                updates.append((old['id'],candidate['id'],json.dumps(desired)))
            if updates:
                self.db.execute('UPDATE reader_problems p SET identity=source.identity,body=source.body FROM unnest(%s::text[],%s::text[],%s::text[]) AS source(id,identity,body) WHERE p.id=source.id AND p.finished IS NULL',([r[0] for r in updates],[r[1] for r in updates],[r[2] for r in updates]))
                status_changed(self.db)
            for job_id,previous in fallback.items():self.sync_media_problem(job_id,previous,now)
        occupied_rows={row['identity']:row for row in self.db.execute('SELECT id,identity,body FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(identities+list(legacy_keys),))}
        occupied=set(occupied_rows)
        isolated_rows: dict[str,list[Any]]={}
        for row in self.db.execute("SELECT id,identity,body,body::jsonb->>'media_job_identity' AS canonical FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:download_media' AND body::jsonb->>'media_job_identity'=ANY(%s)",(identities,)):
            isolated_rows.setdefault(row['canonical'],[]).append(row)
        isolated=set(isolated_rows)
        simple=[];complex_ids=set()
        def ambiguous(candidate: dict[str,Any]) -> bool:
            tail=candidate['id'][len('job:'+candidate['code']+':'):]
            kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
            found=[kind for kind in kinds if ':'+kind+':' in tail]
            return found!=['download_media'] or tail.count(':download_media:')!=1
        def already_current(candidate: dict[str,Any]) -> bool:
            canonical=candidate['id']
            matches=([occupied_rows[canonical]] if canonical in occupied_rows else [])+isolated_rows.get(canonical,[])
            if len(matches)!=1 or not self.media_record_matches(matches[0],candidate):return False
            body=json.loads(matches[0]['body'])
            desired={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
            if matches[0]['identity']!=canonical:desired['media_job_identity']=canonical
            return bool(body==desired)
        for job_id,candidates in projected.items():
            if candidates and all(counts[c['id']]==1 and already_current(c) for c in candidates):
                continue
            if any(counts[c['id']]!=1 or c['id'] in occupied or c['id'] in isolated or c['id'] in legacy_keys or c.get('_legacy_id') in occupied or ambiguous(c) for c in candidates):
                complex_ids.add(job_id)
            else:
                simple.extend(candidates)
        added=[]
        for candidate in simple:
            body={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
            added.append((uuid.uuid4().hex,candidate['id'],'background',json.dumps(body),now))
        if added:
            if new_ids_out is not None:new_ids_out.update(row[0] for row in added)
            self.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome)
                SELECT id,identity,origin,body,created,NULL,NULL FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::double precision[]) AS p(id,identity,origin,body,created)""",tuple([row[i] for row in added] for i in range(5)))
            status_changed(self.db)
        return [row for row in rows if row['id'] in complex_ids]

    def recover_subtitles(self) -> None:
        if self.db.execute("SELECT 1 FROM state WHERE key='subtitle-problems-recovered-v1'").fetchone():return
        cursor: str | None=None
        while True:
            query="""SELECT m.id AS file_id,j.id,j.subtitle_event_owned FROM media_files m JOIN jobs j ON j.item=m.id AND j.kind='download_media'
                WHERE m.resource_key='youtube:subtitles' AND j.status IN ('pending','running','failed','unavailable','blocked')
                AND ((j.problem_code IS NULL AND j.error IS NOT NULL) OR (j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%'
                    AND j.problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')))"""
            if cursor is not None:query+=' AND m.id COLLATE "C">%s'
            rows=self.db.execute(query+' ORDER BY m.id COLLATE "C" LIMIT 100',(cursor,) if cursor is not None else ()).fetchall()
            if not rows:break
            with self.db:
                from oko.storage import memberships
                memberships.lock_graph(self.db)
                now=self.store.wall_clock()
                from oko.storage.media_job_problems import managed_candidates_many
                legacy_rows=self.db.execute("""SELECT t.item,r.id FROM unnest(%s::text[]) t(item) CROSS JOIN LATERAL (
                    SELECT id FROM reader_problems WHERE finished IS NULL AND identity LIKE 'job:%%' AND body::jsonb->>'code'='work-download_media'
                    AND body::jsonb->>'operation' IN ('producer:download_media','producer:status')
                    AND md5(substring(coalesce(body::jsonb->>'media_job_identity',body::jsonb->>'status_job_identity',identity) FROM ':download_media:(.*)$'))=md5(t.item)
                    AND substring(coalesce(body::jsonb->>'media_job_identity',body::jsonb->>'status_job_identity',identity) FROM ':download_media:(.*)$')=t.item LIMIT 101) r""",([row['file_id'] for row in rows if not row['subtitle_event_owned']],)).fetchall()
                legacy_counts: dict[str,int]={}
                for legacy in legacy_rows:legacy_counts[legacy['item']]=legacy_counts.get(legacy['item'],0)+1
                legacy_items=set(legacy_counts)
                overflow_items={item for item,count in legacy_counts.items() if count>100}
                if legacy_items:
                    from oko.storage.subtitle_problems import protect_legacy
                    for item in legacy_items:protect_legacy(self.store,item)
                if overflow_items:self.db.execute("UPDATE jobs SET subtitle_event_owned=false WHERE id=ANY(%s)",([row['id'] for row in rows if row['file_id'] in overflow_items],))
                direct_rows=[row for row in rows if row['file_id'] in legacy_items and row['file_id'] not in overflow_items]
                bounded=[row for row in rows if row['file_id'] not in legacy_items]
                for row in direct_rows:self.sync_media_problem(row['id'],[],now)
                projected=managed_candidates_many(self.store,[row['id'] for row in bounded],now,include_subtitles=True) if bounded else {}
                key_jobs: dict[str,set[int]]={}
                for job_id,parts in projected.items():
                    for candidate in parts:key_jobs.setdefault(candidate['id'],set()).add(job_id)
                conflicting_jobs: set[int]=set()
                from oko.storage.subtitle_problems import protect_isolated
                for field in ('media_job_identity','status_job_identity'):
                    query=f"""SELECT t.canonical FROM unnest(%s::text[]) t(canonical) CROSS JOIN LATERAL (
                        SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:download_media'
                        AND body::jsonb->>'{field}'=t.canonical LIMIT 101) r GROUP BY t.canonical HAVING count(*)>100"""
                    conflicts=self.db.execute(query,(sorted(key_jobs),)).fetchall() if key_jobs else []
                    for conflict in conflicts:
                        canonical=conflict['canonical'];protect_isolated(self.store,field,canonical)
                        conflicting_jobs.update(key_jobs[canonical])
                if conflicting_jobs:
                    self.db.execute('UPDATE jobs SET subtitle_event_owned=false WHERE id=ANY(%s)',(sorted(conflicting_jobs),))
                    bounded=[row for row in bounded if row['id'] not in conflicting_jobs]
                    projected={job:parts for job,parts in projected.items() if job not in conflicting_jobs}
                new_ids: set[str]=set()
                complex_rows=self.recover_media_batch(bounded,now,include_subtitles=True,new_ids_out=new_ids) if bounded else []
                complex_ids={row['id'] for row in complex_rows}
                for row in complex_rows:self.sync_media_problem(row['id'],[],now)
                owned=[job for job,candidates in projected.items() if candidates and job not in complex_ids]
                if owned:
                    self.db.execute("UPDATE jobs SET subtitle_event_owned=true WHERE id=ANY(%s)",(owned,))
                    if self.store.youtube.status(now)['next_at']>now:
                        from oko.storage.subtitle_problems import sync_timer
                        sync_timer(self.store,now,owned,new_ids=new_ids)
            cursor=rows[-1]['file_id']
            self.store.fault('subtitle_problem_recovery_batch')
        with self.db:self.db.execute("INSERT INTO state(key,value) VALUES ('subtitle-problems-recovered-v1','true') ON CONFLICT DO NOTHING")

    def recover_media_problems(self) -> None:
        if self.db.execute("SELECT 1 FROM state WHERE key='media-problems-recovered-v2'").fetchone():return
        problem_cursor: tuple[float,str] | None=None
        while True:
            query="""SELECT id,identity,body,created FROM reader_problems WHERE finished IS NULL AND identity LIKE 'job:%%' AND coalesce(body::jsonb->>'media_job_identity',identity) LIKE 'job:%%:download_media:%%' AND body::jsonb->>'operation' IN ('producer:download_media','producer:status')
                AND body::jsonb->>'code' IS NOT NULL AND body::jsonb->>'code'<>'' AND body::jsonb->>'code' NOT LIKE 'source-%%'
                AND body::jsonb->>'code' NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch','work-download_media')"""
            if problem_cursor is not None:query+=' AND (created,id COLLATE "C")>(%s,%s)'
            records=self.db.execute(query+' ORDER BY created,id COLLATE "C" LIMIT 100',problem_cursor if problem_cursor is not None else ()).fetchall()
            if not records:break
            parsed=[]
            for record in records:
                try:body=json.loads(record['body'])
                except (ValueError,TypeError):continue
                if not isinstance(body,dict) or not isinstance(body.get('code'),str) or not isinstance(body.get('objects'),list):continue
                if not body['objects'] or any(not isinstance(obj,dict) or obj.get('kind')!='material' or not isinstance(obj.get('id'),str) or not obj['id'] for obj in body['objects']):continue
                code=body['code'];identity=body.get('media_job_identity',record['identity']);prefix='job:'+code+':'
                if not isinstance(identity,str) or not identity.startswith(prefix):continue
                tail=identity[len(prefix):]
                kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
                found=[kind for kind in kinds if ':'+kind+':' in tail]
                if found!=['download_media'] or tail.count(':download_media:')!=1:continue
                feed,item=tail.split(':download_media:',1)
                if not feed or not item:continue
                parsed.append((record,body,feed,item))
            items=sorted({item for _,_,_,item in parsed});feeds=sorted({feed for _,_,feed,_ in parsed})
            with self.db:
                jobs={row['item']:row for row in self.db.execute("SELECT id,item FROM jobs WHERE kind='download_media' AND item=ANY(%s)",(items,))} if items else {}
                from oko.storage.media_job_problems import managed_candidates_many
                expected=managed_candidates_many(self.store,[row['id'] for row in jobs.values()],self.store.wall_clock()) if jobs else {}
                affected={obj['id'] for _,body,_,_ in parsed for obj in body['objects']}
                files={row['id']:row for row in self.db.execute('SELECT id,publication_id,resource_key FROM media_files WHERE id=ANY(%s)',(items,))} if items else {}
                publications_ids={row['publication_id'] for row in files.values()}
                possible_active=self.active_media_roots(affected)
                linked=self.fetch_linked_pairs(publications_ids,affected)
                publications={row['id']:row for row in self.db.execute('SELECT id,viewed,source_deleted FROM publications WHERE id=ANY(%s)',(list(publications_ids),))} if publications_ids else {}
                resolved_ids: list[str]=[]
                irrelevant_ids: list[str]=[]
                updates: list[tuple[str,str]]=[]
                pending_jobs: dict[int,list[dict[str,Any]]]={}
                for record,body,feed,item in parsed:
                    file=files.get(item)
                    if file and file['resource_key']=='youtube:subtitles':continue
                    publication_id=file['publication_id'] if file else item
                    job=jobs.get(item)
                    if job:
                        marked=body.get('media_job_id')==str(job['id'])
                        mentioned={obj['id'] for obj in body['objects']}
                        expected_objects={obj['id'] for candidate in expected.get(job['id'],[]) for obj in candidate['objects']}
                        old_orphan=(body.get('media_orphan') is True and body.get('media_file_id')==item and marked) or (not marked and mentioned=={item})
                        allowed_objects=expected_objects | {publication_id} | ({item} if old_orphan else set())
                        if not mentioned.issubset(allowed_objects) or (not marked and publication_id not in mentioned and not mentioned.intersection(expected_objects) and not old_orphan):
                            continue  # Every saved object must be attributable to this Job.
                        canonical=body.get('media_job_identity',record['identity'])
                        current=next((c for c in expected.get(job['id'],[]) if c['id']==canonical),None)
                        if current is not None and body.get('media_job_id')==str(job['id']):
                            desired={key:value for key,value in current.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
                            if body.get('media_job_identity') is not None:desired['media_job_identity']=canonical
                            if body==desired:continue
                        candidate=dict(id=record['identity'],code=body['code'],objects=body['objects'],media_job_id=str(job['id']),media_file_id=item)
                        if self.media_record_matches(record,candidate):
                            if current is not None:
                                desired={key:value for key,value in current.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
                                if body.get('media_job_identity') is not None:desired['media_job_identity']=canonical
                                updates.append((record['id'],json.dumps(desired)))
                            else:
                                pending_jobs.setdefault(job['id'],[]).append(candidate)
                    else:
                        if any(obj['id'] in possible_active for obj in body['objects']):
                            continue  # The stored identity may be misleading; preserve ambiguous evidence.
                        if any(obj['id']!=publication_id and (obj['id'],publication_id) not in linked for obj in body['objects']):
                            continue  # Every stored object needs evidence of its relationship to this Job.
                        publication=publications.get(publication_id)
                        irrelevant=bool(publication and (publication['viewed'] or publication['source_deleted']))
                        (irrelevant_ids if irrelevant else resolved_ids).append(record['id'])
                for job_id,previous in pending_jobs.items():
                    self.sync_media_problem(job_id,previous,self.store.wall_clock())
                if updates:
                    self.db.execute('UPDATE reader_problems p SET body=source.body FROM unnest(%s::text[],%s::text[]) AS source(id,body) WHERE p.id=source.id AND p.finished IS NULL',( [row[0] for row in updates], [row[1] for row in updates]))
                    status_changed(self.db)
                for outcome,ids in (('resolved',resolved_ids),('irrelevant',irrelevant_ids)):
                    if ids:
                        self.db.execute('UPDATE reader_problems SET finished=%s,outcome=%s WHERE id=ANY(%s) AND finished IS NULL',(self.store.wall_clock(),outcome,ids))
                        status_changed(self.db)
            problem_cursor=(records[-1]['created'],records[-1]['id'])
            self.store.fault('media_problem_record_recovery_batch')
        cursor: int | None=None
        while True:
            query="""SELECT id FROM jobs WHERE kind='download_media' AND item IS NOT NULL AND item<>'' AND problem_code IS NOT NULL
                AND problem_code NOT LIKE 'source-%%' AND problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')"""
            if cursor is not None:query+=' AND id>%s'
            rows=self.db.execute(query+' ORDER BY id LIMIT 100',(cursor,) if cursor is not None else ()).fetchall()
            if not rows:break
            with self.db:
                complex_rows=self.recover_media_batch(list(rows),self.store.wall_clock())
                for row in complex_rows:self.sync_media_problem(row['id'],[],self.store.wall_clock())
            cursor=rows[-1]['id']
            self.store.fault('media_problem_recovery_batch')
        with self.db:self.db.execute("INSERT INTO state(key,value) VALUES ('media-problems-recovered-v2','true') ON CONFLICT DO NOTHING")

    def media_record_matches(self, record: Any, candidate: dict[str, Any]) -> bool:
        try:body=json.loads(record['body'])
        except (TypeError,ValueError):return False
        if not isinstance(body,dict) or body.get('code')!=candidate['code']:
            return False
        if candidate.get('subtitle_event'):
            allowed_fields={'code','section','title','consequence','next_step','objects','action','operation','state','next_at','reason',
                'media_owner','media_file_id','media_orphan','media_job_id','media_job_identity','media_implicit_code',
                'status_job_identity','status_job_target','subtitle_event','subtitle_wait_on_next','subtitle_reservation','subtitle_canonical'}
            if set(body)-allowed_fields:return False
        operation=body.get('operation')
        objects=body.get('objects')
        if not isinstance(objects,list) or not objects or any(not isinstance(obj,dict) or obj.get('kind')!='material' or not isinstance(obj.get('id'),str) for obj in objects):return False
        allowed={(obj.get('kind'),obj.get('id')) for obj in candidate['objects'] if isinstance(obj,dict)}
        if not all((obj.get('kind'),obj.get('id')) in allowed for obj in objects):return False
        status_identity=body.get('status_job_identity')
        if status_identity is not None:
            return bool(operation=='producer:download_media' and status_identity==candidate['id'] and body.get('status_job_target')==candidate['media_file_id'])
        marker=body.get('media_job_id')
        if marker is not None:
            return bool(operation=='producer:download_media' and marker==candidate['media_job_id'] and body.get('media_file_id')==candidate['media_file_id'] and body.get('media_job_identity',candidate['id'])==candidate['id'])
        if operation not in (None,'producer:status','producer:download_media'):
            return False
        identity=record['identity']
        prefix='job:'+candidate['code']+':'
        if not identity.startswith(prefix):return False
        tail=identity[len(prefix):]
        kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
        found=[kind for kind in kinds if ':'+kind+':' in tail]
        if found!=['download_media'] or tail.count(':download_media:')!=1:return False
        return True

    def media_record(self, candidate: dict[str, Any]) -> Any:
        rows=self.db.execute("SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:download_media' AND body::jsonb->>'media_job_identity'=%s",(candidate['id'],)).fetchall()
        for row in rows:
            if self.media_record_matches(row,candidate):return row
        canonical_row=self.db.execute('SELECT id,identity,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(candidate['id'],)).fetchone()
        return canonical_row if canonical_row and self.media_record_matches(canonical_row,candidate) else None

    def saved_media_candidates(self, job_ids: list[int]) -> dict[int,list[dict[str,Any]]]:
        if not job_ids:return {}
        rows=self.db.execute("SELECT identity,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:download_media' AND body::jsonb->>'media_job_id'=ANY(%s)",([str(job) for job in job_ids],)).fetchall()
        groups: dict[int,list[dict[str,Any]]]={}
        requested={str(job):job for job in job_ids}
        for row in rows:
            body=json.loads(row['body'])
            marker=body.get('media_job_id') if isinstance(body,dict) else None
            if not isinstance(marker,str) or marker not in requested or not isinstance(body.get('objects'),list):continue
            body['id']=body.get('media_job_identity',row['identity'])
            groups.setdefault(requested[marker],[]).append(body)
        return groups

    def sync_media_problem(self, job_id: int, previous: list[dict[str,Any]], now: float, *, successful: bool = False) -> None:
        from oko.storage.media_job_problems import managed_candidates
        previous_subtitle=[dict(candidate) for candidate in previous if candidate.get('subtitle_event')]
        candidates=[] if successful else managed_candidates(self.store,job_id,now)
        subtitle_items={candidate['media_file_id'] for candidate in previous_subtitle+candidates if candidate.get('subtitle_event')}
        for item in subtitle_items:
            legacy=self.db.execute("""SELECT id FROM reader_problems WHERE finished IS NULL AND identity LIKE 'job:%%' AND body::jsonb->>'code'='work-download_media'
                AND body::jsonb->>'operation' IN ('producer:download_media','producer:status')
                AND md5(substring(coalesce(body::jsonb->>'media_job_identity',body::jsonb->>'status_job_identity',identity) FROM ':download_media:(.*)$'))=md5(%s)
                AND substring(coalesce(body::jsonb->>'media_job_identity',body::jsonb->>'status_job_identity',identity) FROM ':download_media:(.*)$')=%s LIMIT 101""",(item,item)).fetchall()
            if len(legacy)>100:
                from oko.storage.subtitle_problems import protect_legacy
                protect_legacy(self.store,item)
                if self.db.execute('UPDATE jobs SET subtitle_event_owned=false WHERE id=%s AND subtitle_event_owned',(job_id,)).rowcount:status_changed(self.db)
                return
        current_subtitle_ids={candidate['id'] for candidate in candidates if candidate.get('subtitle_event')}
        desired_subtitle=[dict(candidate) for candidate in candidates if candidate.get('subtitle_event')]
        ownership_changed=self.db.execute("UPDATE jobs SET subtitle_event_owned=(%s=1) WHERE id=%s AND kind='download_media' AND EXISTS (SELECT 1 FROM media_files WHERE id=jobs.item AND resource_key='youtube:subtitles') AND subtitle_event_owned IS DISTINCT FROM (%s=1)",(bool(candidates),job_id,bool(candidates))).rowcount
        if ownership_changed:status_changed(self.db)
        if previous and not candidates and not successful:
            subtitle=self.db.execute("SELECT 1 FROM jobs j JOIN media_files m ON m.id=j.item WHERE j.id=%s AND j.kind='download_media' AND m.resource_key='youtube:subtitles'",(job_id,)).fetchone()
            if not subtitle or not self.store.youtube.status(now)['paused']:
                from oko.storage.media_job_problems import candidates_many
                handoff=candidates_many(self.store,[job_id],now,include_subtitles=bool(subtitle)).get(job_id,[])
                candidates=[dict({k:v for k,v in candidate.items() if k not in ('_code_present','_subtitle')},media_job_id=str(job_id),_handoff_to_get=True)
                    for candidate in handoff if (subtitle or not candidate['_code_present']) and not (subtitle and not candidate['_code_present'] and self.store.youtube.status(now)['next_at']>now) and candidate['code'] not in ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch') and not candidate['code'].startswith('source-')]
        keys={candidate['id'] for candidate in previous+candidates}
        keys.update(candidate['_legacy_id'] for candidate in candidates if candidate.get('_legacy_id'))
        rows=self.db.execute('SELECT * FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(sorted(keys),)).fetchall() if keys else []
        by_identity={row['identity']:row for row in rows}
        isolated: dict[str,list[Any]]={}
        if keys:
            if previous_subtitle or any(candidate.get('subtitle_event') for candidate in candidates):
                for field in ('media_job_identity','status_job_identity'):
                    query=f"""SELECT r.*,t.canonical FROM unnest(%s::text[]) t(canonical) CROSS JOIN LATERAL (
                        SELECT * FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:download_media'
                        AND body::jsonb->>'{field}'=t.canonical LIMIT 101) r"""
                    for row in self.db.execute(query,(sorted(keys),)):isolated.setdefault(row['canonical'],[]).append(row)
                overflow_keys=[key for key,rows in isolated.items() if len(rows)>100]
                if overflow_keys:
                    from oko.storage.subtitle_problems import protect_isolated
                    for key in overflow_keys:
                        for field in ('media_job_identity','status_job_identity'):protect_isolated(self.store,field,key)
                    if self.db.execute('UPDATE jobs SET subtitle_event_owned=false WHERE id=%s AND subtitle_event_owned',(job_id,)).rowcount:status_changed(self.db)
                    return
            else:
                for row in self.db.execute("SELECT *,body::jsonb->>'media_job_identity' AS canonical FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:download_media' AND body::jsonb->>'media_job_identity'=ANY(%s)",(sorted(keys),)):
                    isolated.setdefault(row['canonical'],[]).append(row)
                for row in self.db.execute("SELECT *,body::jsonb->>'status_job_identity' AS canonical FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:download_media' AND body::jsonb->>'status_job_identity'=ANY(%s)",(sorted(keys),)):
                    isolated.setdefault(row['canonical'],[]).append(row)
        subtitle_foreign: dict[str,Any]={}
        for candidate in candidates:
            if not candidate.get('subtitle_event'):continue
            for saved in ([by_identity[candidate['id']]] if candidate['id'] in by_identity else [])+isolated.get(candidate['id'],[]):
                if not self.media_record_matches(saved,candidate):subtitle_foreign[saved['id']]=saved
        if subtitle_foreign:
            from oko.storage.subtitle_problems import protect_rows
            protect_rows(self.store,list(subtitle_foreign.values()))
        def record(candidate: dict[str,Any]) -> Any:
            for saved in isolated.get(candidate['id'],[]):
                if self.media_record_matches(saved,candidate):return saved
            saved=by_identity.get(candidate['id'])
            return saved if saved and self.media_record_matches(saved,candidate) else None
        for candidate in previous:
            matched=record(candidate)
            candidate['id']=matched['identity'] if matched else ''
        for candidate in candidates:
            legacy=candidate.pop('_legacy_id',None)
            if legacy:
                legacy_candidate=dict(candidate,id=legacy)
                matched=record(legacy_candidate)
                if matched and not any(entry['id']==matched['identity'] for entry in previous):previous.append(dict(legacy_candidate,id=matched['identity']))
        for candidate in candidates:
            canonical=candidate['id']
            matched=record(candidate)
            if matched and matched['identity']!=canonical:
                candidate['id']=matched['identity'];candidate['media_job_identity']=canonical
                continue
            occupied=by_identity.get(canonical)
            tail=canonical[len('job:'+candidate['code']+':'):]
            kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
            found=[kind for kind in kinds if ':'+kind+':' in tail]
            if not matched and (occupied or found!=['download_media'] or tail.count(':download_media:')!=1):
                digest=hashlib.sha256(canonical.encode('utf-8')).hexdigest()
                for _ in range(3):
                    alternate='job:media-isolated:'+digest+':'+new_read_problem_token()
                    if not self.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(alternate,)).fetchone():
                        candidate['id']=alternate;candidate['media_job_identity']=canonical
                        break
                else:raise RuntimeError('Unable to allocate media problem identity')
        current={candidate['id']:candidate for candidate in candidates}
        old_ids={candidate['id'] for candidate in previous if candidate['id'] and candidate['id'] not in current}
        old_candidates={candidate['id']:candidate for candidate in previous if candidate['id']}
        if current and old_ids:
            existing={row['identity']:row for row in self.db.execute('SELECT * FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(sorted(old_ids | set(current)),))}
            assigned: dict[str,list[Any]]={}
            for old in sorted((existing[identity] for identity in old_ids if identity in existing),key=lambda row:(row['created'],row['id'])):
                old_body=json.loads(old['body'])
                if not isinstance(old_body,dict):continue
                same_code=[identity for identity,candidate in current.items() if candidate['code']==old_body.get('code') or (old_candidates[old['identity']].get('media_job_id')==candidate.get('media_job_id') and ((old_body.get('code')=='work-download_media' and old_candidates[old['identity']].get('media_implicit_code') is True) or (candidate['code']=='work-download_media' and candidate.get('media_implicit_code') is True)))]
                if not same_code:continue
                old_objects=old_body.get('objects',[])
                matching=[identity for identity in same_code if any(obj in current[identity]['objects'] for obj in old_objects if isinstance(obj,dict))]
                target=(sorted(matching)[0] if matching else sorted(same_code)[0])
                assigned.setdefault(target,[]).append(old)
            close_ids: list[str]=[]
            rekeys: list[tuple[str,str]]=[]
            for target,prior_rows in assigned.items():
                occupied=existing.get(target)
                own=occupied if occupied and self.media_record_matches(occupied,current[target]) else None
                choices=sorted([*prior_rows,*([own] if own else [])],key=lambda row:(row['created'],row['id']))
                if occupied and own is None:continue
                winner=choices[0]
                close_ids.extend(row['id'] for row in choices[1:])
                if winner['identity']!=target:rekeys.append((winner['id'],target))
            if close_ids:
                self.db.execute("UPDATE reader_problems SET finished=%s,outcome='irrelevant' WHERE id=ANY(%s) AND finished IS NULL",(now,close_ids))
            if rekeys:
                self.db.execute('UPDATE reader_problems p SET identity=source.identity FROM unnest(%s::text[],%s::text[]) AS source(id,identity) WHERE p.id=source.id AND p.finished IS NULL',([r[0] for r in rekeys],[r[1] for r in rekeys]))
            if close_ids or rekeys:status_changed(self.db)
        closing=[prior for prior in previous if prior['id'] and prior['id'] not in current]
        owners=sorted({prior['media_owner'] for prior in closing if prior.get('media_owner')})
        materials=sorted({obj['id'] for prior in closing for obj in prior['objects'] if obj.get('kind')=='material'})
        alive={row['id'] for row in self.db.execute("SELECT id FROM feeds WHERE id=ANY(%s) AND connection!='deleting'",(owners,))} if owners else set()
        removed={row['id'] for row in self.db.execute('SELECT id FROM publications WHERE id=ANY(%s) AND (viewed=1 OR source_deleted)',(materials,))} if materials else set()
        files=sorted({prior['media_file_id'] for prior in closing if prior.get('media_file_id')})
        existing_files={row['id'] for row in self.db.execute('SELECT id FROM media_files WHERE id=ANY(%s)',(files,))} if files else set()
        finished: dict[str,list[str]]={'resolved':[],'irrelevant':[]}
        for prior in closing:
            owner=prior.get('media_owner')
            irrelevant=not successful and ((not prior.get('media_orphan') and (bool(owner and owner not in alive) or bool(prior.get('media_file_id') and prior['media_file_id'] not in existing_files))) or any(obj.get('kind')=='material' and obj['id'] in removed for obj in prior['objects']))
            finished['irrelevant' if irrelevant else 'resolved'].append(prior['id'])
        for outcome,identities in finished.items():
            if identities:
                changed=self.db.execute('UPDATE reader_problems SET finished=%s,outcome=%s WHERE identity=ANY(%s) AND finished IS NULL',(now,outcome,identities)).rowcount
                if changed:status_changed(self.db)
        saved={row['identity']:row for row in self.db.execute('SELECT identity,body FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(sorted(current),))} if current else {}
        updates=[];added=[]
        for identity,candidate in current.items():
            body={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_handoff_to_get')}
            if candidate.get('_handoff_to_get'):
                for key in ('media_job_id','media_file_id','media_owner','media_orphan'):body.pop(key,None)
                if body.get('media_job_identity') is not None:
                    body['status_job_identity']=body.pop('media_job_identity')
                    body['status_job_target']=candidate['media_file_id']
            body['objects']=[{key:value for key,value in obj.items() if key!='label'} for obj in candidate['objects']]
            encoded=json.dumps(body)
            if identity in saved:
                if saved[identity]['body']!=encoded:updates.append((identity,encoded))
            else:added.append((uuid.uuid4().hex,identity,'background',encoded,now))
        if updates:
            self.db.execute('UPDATE reader_problems p SET body=source.body FROM unnest(%s::text[],%s::text[]) AS source(identity,body) WHERE p.identity=source.identity AND p.finished IS NULL',([r[0] for r in updates],[r[1] for r in updates]))
        if added:
            self.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome)
                SELECT id,identity,origin,body,created,NULL,NULL FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::double precision[]) AS p(id,identity,origin,body,created)""",tuple([row[i] for row in added] for i in range(5)))
        if updates or added:status_changed(self.db)
        from oko.storage.subtitle_problems import transfer_reservation
        transferred=transfer_reservation(self.store,job_id,previous_subtitle,desired_subtitle,{row[0] for row in added}) if previous_subtitle and desired_subtitle else set()
        removed_subtitle=[candidate for candidate in previous_subtitle if candidate['id'] not in current_subtitle_ids and candidate['id'] not in transferred]
        if removed_subtitle:
            from oko.storage.subtitle_problems import cancel_reservations
            cancel_reservations(self.store,job_id,removed_subtitle)
        if any(candidate.get('subtitle_event') for candidate in candidates):
            from oko.storage.subtitle_problems import sync_timer
            sync_timer(self.store,now,[job_id],new_ids={row[0] for row in added},previous=previous_subtitle)

    def active_processing_roots(self, items: set[str]) -> set[str]:
        result: set[str]=set()
        ordered=sorted(items)
        for start in range(0,len(ordered),400):
            result.update(row['root'] for row in self.db.execute("""SELECT target.root FROM unnest(%s::text[]) target(root)
                WHERE EXISTS (SELECT 1 FROM jobs j WHERE j.kind IN ('filter','theses') AND j.item=target.root AND j.status IN ('pending','running','failed','unavailable','blocked') AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL))
                   OR EXISTS (SELECT 1 FROM publication_links link JOIN jobs j ON j.kind IN ('filter','theses') AND j.item=link.target_publication_id
                       WHERE link.source_publication_id=target.root AND j.status IN ('pending','running','failed','unavailable','blocked') AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL))""",(ordered[start:start+400],)))
        return result

    def recover_processing_batch(self, rows: list[Any], now: float) -> list[Any]:
        """Persist independent first occurrences; address legacy collisions separately."""
        from oko.storage.processing_job_problems import managed_candidates_many
        projected=managed_candidates_many(self.store,[row['id'] for row in rows],now)
        counts: dict[str,int]={};legacy_keys: set[str]=set()
        for candidates in projected.values():
            for candidate in candidates:
                identity=candidate['id'];counts[identity]=counts.get(identity,0)+1
                if candidate.get('_legacy_id'):legacy_keys.add(candidate['_legacy_id'])
        identities=list(counts)
        if not identities:return []
        occupied_rows={row['identity']:row for row in self.db.execute('SELECT id,identity,body FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(identities+list(legacy_keys),))}
        occupied=set(occupied_rows)
        isolated_rows: dict[str,list[Any]]={}
        for row in self.db.execute("SELECT id,identity,body,body::jsonb->>'processing_job_identity' AS canonical FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation' IN ('producer:filter','producer:theses') AND body::jsonb->>'processing_job_identity'=ANY(%s)",(identities,)):
            isolated_rows.setdefault(row['canonical'],[]).append(row)
        isolated=set(isolated_rows)
        simple=[];complex_ids=set()
        def ambiguous(candidate: dict[str,Any]) -> bool:
            tail=candidate['id'][len('job:'+candidate['code']+':'):]
            kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
            found=[kind for kind in kinds if ':'+kind+':' in tail]
            return found!=[candidate['processing_kind']] or tail.count(':'+candidate['processing_kind']+':')!=1
        def already_current(candidate: dict[str,Any]) -> bool:
            canonical=candidate['id']
            matches=([occupied_rows[canonical]] if canonical in occupied_rows else [])+isolated_rows.get(canonical,[])
            if len(matches)!=1 or not self.processing_record_matches(matches[0],candidate):return False
            body=json.loads(matches[0]['body'])
            desired={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
            if matches[0]['identity']!=canonical:desired['processing_job_identity']=canonical
            return bool(body==desired)
        for job_id,candidates in projected.items():
            if candidates and all(counts[c['id']]==1 and already_current(c) for c in candidates):
                continue
            if any(counts[c['id']]!=1 or c['id'] in occupied or c['id'] in isolated or c['id'] in legacy_keys or c.get('_legacy_id') in occupied or ambiguous(c) for c in candidates):
                complex_ids.add(job_id)
            else:
                simple.extend(candidates)
        added=[]
        for candidate in simple:
            body={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
            added.append((uuid.uuid4().hex,candidate['id'],'background',json.dumps(body),now))
        if added:
            self.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome)
                SELECT id,identity,origin,body,created,NULL,NULL FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::double precision[]) AS p(id,identity,origin,body,created)""",tuple([row[i] for row in added] for i in range(5)))
            status_changed(self.db)
        return [row for row in rows if row['id'] in complex_ids]

    def recover_processing_problems(self) -> None:
        if self.db.execute("SELECT 1 FROM state WHERE key='processing-problems-recovered-v2'").fetchone():return
        def saved_job_id(value: Any) -> int | None:
            if not isinstance(value,str) or len(value)>20 or not re.fullmatch(r'-?(?:0|[1-9][0-9]*)',value):return None
            result=int(value)
            return result if str(result)==value and -(2**63)<=result<2**63 else None
        problem_cursor: tuple[float,str] | None=None
        while True:
            query="""SELECT id,identity,body,created FROM reader_problems WHERE finished IS NULL AND identity LIKE 'job:%%' AND (coalesce(body::jsonb->>'processing_job_identity',identity) LIKE 'job:%%:filter:%%' OR coalesce(body::jsonb->>'processing_job_identity',identity) LIKE 'job:%%:theses:%%') AND body::jsonb->>'operation' IN ('producer:filter','producer:theses','producer:status')
                AND body::jsonb->>'code' IS NOT NULL AND body::jsonb->>'code'<>'' AND body::jsonb->>'code' NOT LIKE 'source-%%'
                AND body::jsonb->>'code' NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')"""
            if problem_cursor is not None:query+=' AND (created,id COLLATE "C")>(%s,%s)'
            records=self.db.execute(query+' ORDER BY created,id COLLATE "C" LIMIT 100',problem_cursor if problem_cursor is not None else ()).fetchall()
            if not records:break
            parsed=[]
            for record in records:
                try:body=json.loads(record['body'])
                except (ValueError,TypeError):continue
                if not isinstance(body,dict) or not isinstance(body.get('code'),str) or not isinstance(body.get('objects'),list):continue
                if not body['objects'] or any(not isinstance(obj,dict) or obj.get('kind')!='material' or not isinstance(obj.get('id'),str) or not obj['id'] for obj in body['objects']):continue
                code=body['code'];identity=body.get('processing_job_identity',record['identity']);prefix='job:'+code+':'
                if not isinstance(identity,str) or not identity.startswith(prefix):continue
                tail=identity[len(prefix):]
                kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
                found=[kind for kind in kinds if ':'+kind+':' in tail]
                if len(found)!=1 or found[0] not in ('filter','theses'):continue
                kind=found[0];delimiter=':'+kind+':'
                if body.get('operation') not in (None,'producer:status','producer:'+kind):continue
                if tail.count(delimiter)!=1:continue
                feed,item=tail.split(delimiter,1)
                if not feed or not item:continue
                parsed.append((record,body,feed,item,kind))
            items=sorted({item for _,_,_,item,_ in parsed});feeds=sorted({feed for _,_,feed,_,_ in parsed})
            with self.db:
                jobs={(row['kind'],row['item']):row for row in self.db.execute("SELECT id,kind,item,problem_code FROM jobs WHERE kind IN ('filter','theses') AND item=ANY(%s)",(items,))} if items else {}
                from oko.storage.processing_job_problems import managed_candidates_many
                expected=managed_candidates_many(self.store,[row['id'] for row in jobs.values()],self.store.wall_clock()) if jobs else {}
                marker_ids={marker for _,body,_,_,_ in parsed if (marker:=saved_job_id(body.get('processing_job_id'))) is not None}
                existing_job_ids={row['id'] for row in self.db.execute('SELECT id FROM jobs WHERE id=ANY(%s)',(sorted(marker_ids),))} if marker_ids else set()
                affected={obj['id'] for _,body,_,_,_ in parsed for obj in body['objects']}
                possible_active=self.active_processing_roots(affected)
                linked=self.fetch_linked_pairs(set(items),affected)
                publications={row['id']:row for row in self.db.execute('SELECT id,viewed,source_deleted FROM publications WHERE id=ANY(%s)',(items,))} if items else {}
                alive={row['id'] for row in self.db.execute("SELECT id FROM feeds WHERE id=ANY(%s) AND connection!='deleting'",(feeds,))} if feeds else set()
                resolved_ids: list[str]=[]
                irrelevant_ids: list[str]=[]
                updates: list[tuple[str,str]]=[]
                pending_jobs: dict[int,list[dict[str,Any]]]={}
                for record,body,feed,item,kind in parsed:
                    job=jobs.get((kind,item))
                    if body['code']=='work-'+kind and not job:
                        continue  # The same legacy code may come from a NULL code outside this slice.
                    if job:
                        marked=body.get('processing_job_id')==str(job['id'])
                        mentioned={obj['id'] for obj in body['objects']}
                        expected_objects={obj['id'] for candidate in expected.get(job['id'],[]) for obj in candidate['objects']}
                        if not mentioned.issubset(expected_objects | {item}) or (not marked and item not in mentioned and not mentioned.intersection(expected_objects)):
                            continue  # Every saved object must be attributable to this Job.
                        canonical=body.get('processing_job_identity',record['identity'])
                        current=next((c for c in expected.get(job['id'],[]) if c['id']==canonical),None)
                        if current is not None and body.get('processing_job_id')==str(job['id']):
                            desired={key:value for key,value in current.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
                            if body.get('processing_job_identity') is not None:desired['processing_job_identity']=canonical
                            if body==desired:continue
                        candidate=dict(id=record['identity'],code=body['code'],objects=body['objects'],processing_job_id=str(job['id']),processing_kind=kind,operation='producer:'+kind)
                        if self.processing_record_matches(record,candidate):
                            if current is not None:
                                desired={key:value for key,value in current.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
                                if body.get('processing_job_identity') is not None:desired['processing_job_identity']=canonical
                                updates.append((record['id'],json.dumps(desired)))
                            else:
                                pending_jobs.setdefault(job['id'],[]).append(candidate)
                    else:
                        marker=saved_job_id(body.get('processing_job_id'))
                        proven_missing=(marker is not None and marker not in existing_job_ids and body.get('processing_kind')==kind and body.get('operation')=='producer:'+kind)
                        if not proven_missing and any(obj['id'] in possible_active for obj in body['objects']):
                            continue  # The stored identity may be misleading; preserve ambiguous evidence.
                        if any(obj['id']!=item and (obj['id'],item) not in linked for obj in body['objects']):
                            continue  # Every stored object needs evidence of its relationship to this Job.
                        publication=publications.get(item)
                        irrelevant=feed not in alive or bool(publication and (publication['viewed'] or publication['source_deleted']))
                        (irrelevant_ids if irrelevant else resolved_ids).append(record['id'])
                for job_id,previous in pending_jobs.items():
                    self.sync_processing_problem(job_id,previous,self.store.wall_clock())
                if updates:
                    self.db.execute('UPDATE reader_problems p SET body=source.body FROM unnest(%s::text[],%s::text[]) AS source(id,body) WHERE p.id=source.id AND p.finished IS NULL',( [row[0] for row in updates], [row[1] for row in updates]))
                    status_changed(self.db)
                for outcome,ids in (('resolved',resolved_ids),('irrelevant',irrelevant_ids)):
                    if ids:
                        self.db.execute('UPDATE reader_problems SET finished=%s,outcome=%s WHERE id=ANY(%s) AND finished IS NULL',(self.store.wall_clock(),outcome,ids))
                        status_changed(self.db)
            problem_cursor=(records[-1]['created'],records[-1]['id'])
            self.store.fault('processing_problem_record_recovery_batch')
        cursor: int | None=None
        while True:
            query="""SELECT id FROM jobs WHERE kind IN ('filter','theses') AND item IS NOT NULL AND item<>'' AND (((problem_code='' OR (problem_code IS NULL AND error IS NOT NULL))) OR (problem_code IS NOT NULL AND problem_code<>''
                AND problem_code NOT LIKE 'source-%%' AND problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')))"""
            if cursor is not None:query+=' AND id>%s'
            rows=self.db.execute(query+' ORDER BY id LIMIT 100',(cursor,) if cursor is not None else ()).fetchall()
            if not rows:break
            with self.db:
                complex_rows=self.recover_processing_batch(list(rows),self.store.wall_clock())
                for row in complex_rows:self.sync_processing_problem(row['id'],[],self.store.wall_clock())
            cursor=rows[-1]['id']
            self.store.fault('processing_problem_recovery_batch')
        with self.db:self.db.execute("INSERT INTO state(key,value) VALUES ('processing-problems-recovered-v2','true') ON CONFLICT DO NOTHING")

    def processing_record_matches(self, record: Any, candidate: dict[str, Any]) -> bool:
        try:body=json.loads(record['body'])
        except (TypeError,ValueError):return False
        if not isinstance(body,dict) or body.get('code')!=candidate['code']:
            return False
        operation=body.get('operation')
        objects=body.get('objects')
        if not isinstance(objects,list) or not objects or any(not isinstance(obj,dict) or obj.get('kind')!='material' or not isinstance(obj.get('id'),str) for obj in objects):return False
        allowed={(obj.get('kind'),obj.get('id')) for obj in candidate['objects'] if isinstance(obj,dict)}
        if not all((obj.get('kind'),obj.get('id')) in allowed for obj in objects):return False
        marker=body.get('processing_job_id')
        if marker is not None:
            return bool(operation==candidate['operation'] and marker==candidate['processing_job_id'] and body.get('processing_job_identity',candidate['id'])==candidate['id'])
        if operation not in (None,'producer:status',candidate['operation']):
            return False
        identity=record['identity']
        prefix='job:'+candidate['code']+':'
        if not identity.startswith(prefix):return False
        tail=identity[len(prefix):]
        kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
        found=[kind for kind in kinds if ':'+kind+':' in tail]
        if found!=[candidate['processing_kind']] or tail.count(':'+candidate['processing_kind']+':')!=1:return False
        return True

    def processing_record(self, candidate: dict[str, Any]) -> Any:
        rows=self.db.execute("SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation' IN ('producer:filter','producer:theses') AND body::jsonb->>'processing_job_identity'=%s",(candidate['id'],)).fetchall()
        for row in rows:
            if self.processing_record_matches(row,candidate):return row
        canonical_row=self.db.execute('SELECT id,identity,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(candidate['id'],)).fetchone()
        return canonical_row if canonical_row and self.processing_record_matches(canonical_row,candidate) else None

    def saved_processing_candidates(self, job_ids: list[int]) -> dict[int,list[dict[str,Any]]]:
        if not job_ids:return {}
        rows=self.db.execute("SELECT identity,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation' IN ('producer:filter','producer:theses') AND body::jsonb->>'processing_job_id'=ANY(%s)",([str(job) for job in job_ids],)).fetchall()
        groups: dict[int,list[dict[str,Any]]]={}
        requested={str(job):job for job in job_ids}
        for row in rows:
            body=json.loads(row['body'])
            marker=body.get('processing_job_id') if isinstance(body,dict) else None
            if not isinstance(marker,str) or marker not in requested or not isinstance(body.get('objects'),list):continue
            body['id']=body.get('processing_job_identity',row['identity'])
            groups.setdefault(requested[marker],[]).append(body)
        return groups

    def sync_processing_problems(self, previous_by_job: dict[int,list[dict[str,Any]]], now: float) -> None:
        """Batch unchanged-owner single-occurrence transitions; isolate complex groups."""
        from oko.storage.processing_job_problems import managed_candidates_many
        ids=sorted(previous_by_job)
        for start in range(0,len(ids),400):
            batch=ids[start:start+400]
            current=managed_candidates_many(self.store,batch,now)
            saved: dict[int,list[Any]]={}
            requested={str(job):job for job in batch}
            for row in self.db.execute("SELECT id,identity,body,body::jsonb->>'processing_job_id' AS owner FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation' IN ('producer:filter','producer:theses') AND body::jsonb->>'processing_job_id'=ANY(%s)",(list(requested),)):
                if row['owner'] in requested:saved.setdefault(requested[row['owner']],[]).append(row)
            updates: list[tuple[str,str]]=[];closing: list[tuple[str,dict[str,Any]]]=[];fallback: list[int]=[]
            for job in batch:
                before=previous_by_job[job];records=saved.get(job,[]);after=current.get(job,[])
                if len(before)!=1 or len(records)!=1 or len(after)>1 or not self.processing_record_matches(records[0],before[0]):
                    fallback.append(job);continue
                record=records[0]
                if not after:
                    closing.append((record['id'],before[0]));continue
                candidate=after[0]
                body=json.loads(record['body'])
                canonical=body.get('processing_job_identity',record['identity'])
                if candidate['id']!=canonical:
                    fallback.append(job);continue
                desired={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
                if record['identity']!=canonical:desired['processing_job_identity']=canonical
                encoded=json.dumps(desired)
                if record['body']!=encoded:updates.append((record['id'],encoded))
            if updates:
                self.db.execute('UPDATE reader_problems p SET body=source.body FROM unnest(%s::text[],%s::text[]) AS source(id,body) WHERE p.id=source.id AND p.finished IS NULL',([r[0] for r in updates],[r[1] for r in updates]))
                status_changed(self.db)
            owners=sorted({candidate['processing_owner'] for _,candidate in closing if candidate.get('processing_owner')})
            materials=sorted({obj['id'] for _,candidate in closing for obj in candidate['objects'] if obj.get('kind')=='material'})
            alive={row['id'] for row in self.db.execute("SELECT id FROM feeds WHERE id=ANY(%s) AND connection!='deleting'",(owners,))} if owners else set()
            removed={row['id'] for row in self.db.execute('SELECT id FROM publications WHERE id=ANY(%s) AND (viewed=1 OR source_deleted)',(materials,))} if materials else set()
            finished: dict[str,list[str]]={'resolved':[],'irrelevant':[]}
            for identity,candidate in closing:
                owner=candidate.get('processing_owner')
                irrelevant=bool(owner and owner not in alive) or any(obj.get('kind')=='material' and obj['id'] in removed for obj in candidate['objects'])
                finished['irrelevant' if irrelevant else 'resolved'].append(identity)
            for outcome,problem_ids in finished.items():
                if problem_ids:
                    self.db.execute('UPDATE reader_problems SET finished=%s,outcome=%s WHERE id=ANY(%s) AND finished IS NULL',(now,outcome,problem_ids))
                    status_changed(self.db)
            for job in fallback:self.sync_processing_problem(job,previous_by_job[job],now)

    def sync_processing_problem(self, job_id: int, previous: list[dict[str,Any]], now: float) -> None:
        from oko.storage.processing_job_problems import managed_candidates
        candidates=managed_candidates(self.store,job_id,now)
        keys={candidate['id'] for candidate in previous+candidates}
        keys.update(candidate['_legacy_id'] for candidate in candidates if candidate.get('_legacy_id'))
        rows=self.db.execute('SELECT * FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(sorted(keys),)).fetchall() if keys else []
        by_identity={row['identity']:row for row in rows}
        isolated: dict[str,list[Any]]={}
        if keys:
            for row in self.db.execute("SELECT *,body::jsonb->>'processing_job_identity' AS canonical FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation' IN ('producer:filter','producer:theses') AND body::jsonb->>'processing_job_identity'=ANY(%s)",(sorted(keys),)):
                isolated.setdefault(row['canonical'],[]).append(row)
        def record(candidate: dict[str,Any]) -> Any:
            for saved in isolated.get(candidate['id'],[]):
                if self.processing_record_matches(saved,candidate):return saved
            saved=by_identity.get(candidate['id'])
            return saved if saved and self.processing_record_matches(saved,candidate) else None
        for candidate in previous:
            matched=record(candidate)
            candidate['id']=matched['identity'] if matched else ''
        for candidate in candidates:
            legacy=candidate.pop('_legacy_id',None)
            if legacy:
                legacy_candidate=dict(candidate,id=legacy)
                matched=record(legacy_candidate)
                if matched and not any(entry['id']==matched['identity'] for entry in previous):previous.append(dict(legacy_candidate,id=matched['identity']))
        for candidate in candidates:
            canonical=candidate['id']
            matched=record(candidate)
            if matched and matched['identity']!=canonical:
                candidate['id']=matched['identity'];candidate['processing_job_identity']=canonical
                continue
            occupied=by_identity.get(canonical)
            tail=canonical[len('job:'+candidate['code']+':'):]
            kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
            found=[kind for kind in kinds if ':'+kind+':' in tail]
            if not matched and (occupied or found!=[candidate['processing_kind']] or tail.count(':'+candidate['processing_kind']+':')!=1):
                digest=hashlib.sha256(canonical.encode('utf-8')).hexdigest()
                for _ in range(3):
                    alternate='job:processing-isolated:'+digest+':'+new_read_problem_token()
                    if not self.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(alternate,)).fetchone():
                        candidate['id']=alternate;candidate['processing_job_identity']=canonical
                        break
                else:raise RuntimeError('Unable to allocate processing problem identity')
        current={candidate['id']:candidate for candidate in candidates}
        old_ids={candidate['id'] for candidate in previous if candidate['id'] and candidate['id'] not in current}
        old_candidates={candidate['id']:candidate for candidate in previous if candidate['id']}
        if current and old_ids:
            existing={row['identity']:row for row in self.db.execute('SELECT * FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(sorted(old_ids | set(current)),))}
            assigned: dict[str,list[Any]]={}
            for old in sorted((existing[identity] for identity in old_ids if identity in existing),key=lambda row:(row['created'],row['id'])):
                old_body=json.loads(old['body'])
                if not isinstance(old_body,dict):continue
                same_code=[identity for identity,candidate in current.items() if candidate['code']==old_body.get('code') or (old_candidates[old['identity']].get('processing_job_id')==candidate.get('processing_job_id') and old_candidates[old['identity']].get('processing_kind')==candidate.get('processing_kind') and ('work-'+candidate['processing_kind'] in (candidate['code'],old_body.get('code'))))]
                if not same_code:continue
                old_objects=old_body.get('objects',[])
                matching=[identity for identity in same_code if any(obj in current[identity]['objects'] for obj in old_objects if isinstance(obj,dict))]
                target=(sorted(matching)[0] if matching else sorted(same_code)[0])
                assigned.setdefault(target,[]).append(old)
            close_ids: list[str]=[]
            rekeys: list[tuple[str,str]]=[]
            for target,prior_rows in assigned.items():
                occupied=existing.get(target)
                own=occupied if occupied and self.processing_record_matches(occupied,current[target]) else None
                choices=sorted([*prior_rows,*([own] if own else [])],key=lambda row:(row['created'],row['id']))
                if occupied and own is None:continue
                winner=choices[0]
                close_ids.extend(row['id'] for row in choices[1:])
                if winner['identity']!=target:rekeys.append((winner['id'],target))
            if close_ids:
                self.db.execute("UPDATE reader_problems SET finished=%s,outcome='irrelevant' WHERE id=ANY(%s) AND finished IS NULL",(now,close_ids))
            if rekeys:
                self.db.execute('UPDATE reader_problems p SET identity=source.identity FROM unnest(%s::text[],%s::text[]) AS source(id,identity) WHERE p.id=source.id AND p.finished IS NULL',([r[0] for r in rekeys],[r[1] for r in rekeys]))
            if close_ids or rekeys:status_changed(self.db)
        closing=[prior for prior in previous if prior['id'] and prior['id'] not in current]
        owners=sorted({prior['processing_owner'] for prior in closing if prior.get('processing_owner')})
        materials=sorted({obj['id'] for prior in closing for obj in prior['objects'] if obj.get('kind')=='material'})
        alive={row['id'] for row in self.db.execute("SELECT id FROM feeds WHERE id=ANY(%s) AND connection!='deleting'",(owners,))} if owners else set()
        removed={row['id'] for row in self.db.execute('SELECT id FROM publications WHERE id=ANY(%s) AND (viewed=1 OR source_deleted)',(materials,))} if materials else set()
        finished: dict[str,list[str]]={'resolved':[],'irrelevant':[]}
        for prior in closing:
            owner=prior.get('processing_owner')
            irrelevant=bool(owner and owner not in alive) or any(obj.get('kind')=='material' and obj['id'] in removed for obj in prior['objects'])
            finished['irrelevant' if irrelevant else 'resolved'].append(prior['id'])
        for outcome,identities in finished.items():
            if identities:
                changed=self.db.execute('UPDATE reader_problems SET finished=%s,outcome=%s WHERE identity=ANY(%s) AND finished IS NULL',(now,outcome,identities)).rowcount
                if changed:status_changed(self.db)
        saved={row['identity']:row for row in self.db.execute('SELECT identity,body FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(sorted(current),))} if current else {}
        updates=[];added=[]
        for identity,candidate in current.items():
            body={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome')}
            body['objects']=[{key:value for key,value in obj.items() if key!='label'} for obj in candidate['objects']]
            encoded=json.dumps(body)
            if identity in saved:
                if saved[identity]['body']!=encoded:updates.append((identity,encoded))
            else:added.append((uuid.uuid4().hex,identity,'background',encoded,now))
        if updates:
            self.db.execute('UPDATE reader_problems p SET body=source.body FROM unnest(%s::text[],%s::text[]) AS source(identity,body) WHERE p.identity=source.identity AND p.finished IS NULL',([r[0] for r in updates],[r[1] for r in updates]))
        if added:
            self.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome)
                SELECT id,identity,origin,body,created,NULL,NULL FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::double precision[]) AS p(id,identity,origin,body,created)""",tuple([row[i] for row in added] for i in range(5)))
        if updates or added:status_changed(self.db)

    @staticmethod
    def deletion_identity(row: Any) -> str | None:
        if row['item'] is None or (row['problem_code'] is None and row['error'] is None):
            return None
        code = row['problem_code'] or 'work-' + row['kind']
        if code not in ('delete','feed-delete','work-delete','work-feed-delete'):
            return None
        feed = row['feed_id'] or row['item'] or 'demo'
        return str('job:' + code + ':' + feed + ':' + row['kind'] + ':' + row['item'])

    def deletion_candidates(self, job_id: int, now: float) -> list[dict[str, Any]]:
        """Project one deletion Job for every affected root owner."""
        rows = self.db.execute(f"""SELECT j.kind,j.item,j.status,j.error,j.problem_code,
            coalesce({owner_expr('p')},{owner_expr('root')},j.feed_id) AS feed_id
            FROM jobs j LEFT JOIN publications p ON p.id=j.item
            LEFT JOIN publication_links l ON l.target_publication_id=p.id
            LEFT JOIN publications root ON root.id=l.source_publication_id AND root.viewed=0
            WHERE j.id=%s AND j.kind IN ('delete','feed-delete') AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL) ORDER BY root.id""", (job_id,)).fetchall()
        candidates: dict[str, dict[str, Any]] = {}
        for row in rows:
            candidate = self._deletion_candidate(row,now)
            if candidate is not None:
                candidates[candidate['id']] = candidate
        return list(candidates.values())

    def deletion_problem_ids(self, job_id: int, now: float) -> list[str]:
        """Capture current and pre-owner-fix identities before a graph mutation."""
        identities = {candidate['id'] for candidate in self.deletion_candidates(job_id,now)}
        row = self.db.execute("SELECT kind,item,error,problem_code,feed_id FROM jobs WHERE id=%s AND kind IN ('delete','feed-delete')",(job_id,)).fetchone()
        legacy = self.deletion_identity(row) if row else None
        if legacy:
            identities.add(legacy)
        return sorted(identities)

    def _deletion_candidate(self, row: Any, now: float) -> dict[str, Any] | None:
        if row['status'] not in ('pending','running','failed','unavailable','blocked') or (row['problem_code'] is None and row['error'] is None):
            return None
        identity = self.deletion_identity(row)
        if identity is None:
            return None
        code = row['problem_code'] or 'work-' + row['kind']
        feed = row['feed_id'] or row['item'] or 'demo'
        kind = row['kind']
        title = 'Удаление фида не завершено' if kind=='feed-delete' else 'Удаление не завершено'
        next_step = ('Включите обработку, чтобы повторить попытку.' if row['status']=='blocked' else
                     'Повторите загрузку вручную.' if row['status']=='failed' else
                     'Проверьте доступность источника.' if row['status']=='unavailable' else
                     'Око повторит попытку автоматически.')
        candidate = dict(id=identity,code=code,section='object',title=title,
            consequence='Око продолжает попытки удаления.',next_step=next_step,
            objects=[dict(kind='feed',id=feed)] if kind=='feed-delete' else [],action=None,
            operation='producer:'+kind,created_at=now,state='retrying' if row['status']=='running' or not row['error'] else 'current',
            finished_at=None,outcome=None,next_at=None)
        if kind!='delete' and row['error']:
            candidate['reason']=row['error']
        return candidate

    @staticmethod
    def deletion_record_matches(record: Any, candidate: dict[str, Any]) -> bool:
        try:
            body=json.loads(record['body'])
        except (TypeError,ValueError):
            return False
        if not isinstance(body,dict) or body.get('code')!=candidate['code']:
            return False
        operation=body.get('operation')
        owner=body.get('deletion_job_id')
        if owner is not None:
            return bool(operation==candidate['operation'] and owner==candidate.get('deletion_job_id') and body.get('deletion_job_identity',candidate['id'])==candidate['id'])
        if operation not in (None,'producer:status',candidate['operation']):
            return False
        prefix='job:'+candidate['code']+':'
        identity=body.get('deletion_job_identity',record['identity'])
        if not isinstance(identity,str):
            return False
        if not identity.startswith(prefix):
            return False
        tail=identity[len(prefix):]
        kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
        matches=[kind for kind in kinds if ':'+kind+':' in tail]
        return matches==[candidate['operation'].removeprefix('producer:')] and tail.count(':'+matches[0]+':')==1

    def deletion_record(self, candidate: dict[str, Any]) -> Any:
        canonical=candidate['id']
        rows=self.db.execute("SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation' IN ('producer:delete','producer:feed-delete') AND body::jsonb->>'deletion_job_identity'=%s",(canonical,)).fetchall()
        for row in rows:
            if self.deletion_record_matches(row,candidate):
                return row
        canonical_row=self.db.execute('SELECT id,identity,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(canonical,)).fetchone()
        return canonical_row if canonical_row and self.deletion_record_matches(canonical_row,candidate) else None

    def sync_deletion(self, job_id: int, previous: list[dict[str, Any]], now: float) -> None:
        for candidate in previous:
            candidate['deletion_job_id']=str(job_id)
            if 'code' not in candidate or 'operation' not in candidate:
                identity=candidate['id']
                codes=[code for code in ('delete','feed-delete','work-delete','work-feed-delete') if identity.startswith('job:'+code+':')]
                kinds=[kind for kind in ('fetch','discover','download_media','filter','theses','read','delete','feed-delete') if ':'+kind+':' in identity]
                if len(codes)!=1 or len(kinds)!=1 or kinds[0] not in ('delete','feed-delete'):
                    candidate['id']=''
                    continue
                candidate.update(code=codes[0],operation='producer:'+kinds[0])
            matched=self.deletion_record(candidate)
            if matched:
                candidate['id']=matched['identity']
            else:
                candidate['id']=''  # Never close an unrelated occurrence sharing the legacy key.
        current_candidates=self.deletion_candidates(job_id,now)
        for candidate in current_candidates:
            candidate['deletion_job_id']=str(job_id)
            canonical=candidate['id']
            matched=self.deletion_record(candidate)
            if matched and matched['identity']!=canonical:
                candidate['id']=matched['identity']
                candidate['deletion_job_identity']=canonical
                continue
            occupied=self.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(canonical,)).fetchone()
            tail=canonical[len('job:'+candidate['code']+':'):]
            kind_names=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
            tokens=[kind for kind in kind_names if ':'+kind+':' in tail]
            ambiguous=len(tokens)!=1 or tail.count(':'+tokens[0]+':')!=1
            if not matched and (occupied or ambiguous):
                digest=hashlib.sha256(canonical.encode('utf-8')).hexdigest()
                for _ in range(3):
                    alternate='job:delete-isolated:'+digest+':'+new_read_problem_token()
                    if not self.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(alternate,)).fetchone():
                        candidate['id']=alternate
                        candidate['deletion_job_identity']=canonical
                        break
                else:
                    raise RuntimeError('Unable to allocate deletion problem identity')
        current = {candidate['id']:candidate for candidate in current_candidates}
        if current:
            row = self.db.execute("SELECT kind,item,error,problem_code,feed_id FROM jobs WHERE id=%s",(job_id,)).fetchone()
            legacy = self.deletion_identity(row) if row else None
            if legacy and legacy not in current:
                old = self.db.execute('SELECT id,identity,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(legacy,)).fetchone()
                exemplar=next(iter(current.values()))
                if old and self.deletion_record_matches(old,dict(exemplar,id=legacy)):
                    vacant = next((identity for identity in sorted(current) if not self.db.execute(
                        'SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()),None)
                    if vacant:
                        self.db.execute('UPDATE reader_problems SET identity=%s WHERE id=%s',(vacant,old['id']))
                        status_changed(self.db)
                    else:
                        self.sync_candidate(legacy,None,now,removed_outcome='irrelevant')
        if current and previous:
            old_ids = {candidate['id'] for candidate in previous if candidate['id'] not in current}
            if old_ids:
                existing = {row['identity']:row for row in self.db.execute(
                    'SELECT * FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)', (sorted(old_ids | set(current)),))}
                vacant = sorted(identity for identity in current if identity not in existing)
                destinations = vacant or sorted(current)
                prior = sorted((existing[identity] for identity in old_ids if identity in existing),
                               key=lambda row:(row['created'],row['id']))
                assigned: dict[str,set[str]] = {}
                for index,row in enumerate(prior):
                    target = destinations[min(index,len(destinations)-1)]
                    assigned.setdefault(target,set()).add(row['identity'])
                targets = [dict(current[identity],_legacy_ids=legacy) for identity,legacy in assigned.items()]
                moved,merged = self._rekey_regrouped_jobs(existing,targets,now)
                if moved or merged:
                    status_changed(self.db)
        for candidate in previous:
            if candidate['id'] and candidate['id'] not in current:
                self.sync_candidate(candidate['id'],None,now)
        for candidate in current.values():
            self.sync_candidate(candidate['id'],candidate,now)

    @staticmethod
    def valid_read_problem_body(raw: str) -> bool:
        try:
            body = json.loads(raw)
        except (TypeError,ValueError):
            return False
        if not isinstance(body,dict) or not isinstance(body.get('code'),str) or not isinstance(body.get('objects'),list):
            return False
        return all(isinstance(obj,dict) and obj.get('kind')=='feed' and isinstance(obj.get('id'),str) for obj in body['objects'])

    def discover_profile_suppresses(self, feed: str, code: str) -> bool:
        if code not in ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'):
            return False
        binding=self.db.execute('SELECT indexed_role FROM telegram_bindings WHERE feed=%s',(feed,)).fetchone()
        profile_identity='profile:'+(binding['indexed_role'] if binding and binding['indexed_role'] else feed)
        profile=self.db.execute("SELECT body FROM reader_problems WHERE identity=%s AND finished IS NULL",(profile_identity,)).fetchone()
        return bool(profile and any(obj.get('kind')=='feed' and obj.get('id')==feed for obj in json.loads(profile['body'])['objects']))

    def recover_simple_discover_batch(self, rows: list[Any], now: float) -> list[Any]:
        """Recover independent first occurrences in bulk and return the complex cases."""
        unsupported={'codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'}
        pairs=[]
        counts: dict[str,int]={}
        for row in rows:
            feed=row['item']
            if not isinstance(feed,str) or not feed or feed=='demo':
                continue
            code=row['problem_code'] or 'work-discover'
            identity='job:'+code+':'+feed+':discover'+('' if code.startswith('source-') else ':'+feed)
            counts[identity]=counts.get(identity,0)+1
            if row['feed_id'] not in (None,feed) or row['status'] not in ('pending','running','failed','unavailable','blocked') or code in unsupported:
                continue
            pairs.append((row,identity))
        if not pairs:return rows
        feeds=[row['item'] for row,_ in pairs]
        identities=[identity for _,identity in pairs]
        all_items=[row['item'] for row in rows if isinstance(row['item'],str) and row['item']]
        publication_ids={row['id'] for row in self.db.execute('SELECT id FROM publications WHERE id=ANY(%s)',(all_items,))}
        deleting_feeds={row['id'] for row in self.db.execute("SELECT id FROM feeds WHERE id=ANY(%s) AND connection='deleting'",(feeds,))}
        occupied={row['identity'] for row in self.db.execute('SELECT identity FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(identities,))}
        prior_feeds={row['feed'] for row in self.db.execute("""SELECT target.feed FROM unnest(%s::text[]) target(feed) CROSS JOIN LATERAL (
            SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:discover'
            AND (body::jsonb->'objects') @> jsonb_build_array(jsonb_build_object('kind','feed','id',target.feed)) LIMIT 1) p""",(feeds,))}
        # Complex jobs may still own a legacy identity that collides with an
        # otherwise simple job; preserve the original per-job ordering there.
        legacy_sources=[row for row in rows if row['item'] in publication_ids or row['feed_id'] not in (None,row['item'])]
        legacy_keys: set[str] = set()
        if legacy_sources:
            legacy_rows=self.db.execute(f"""SELECT j.id,j.item,j.problem_code,coalesce({owner_expr('items')},{owner_expr('linked_root')},j.feed_id) AS legacy_owner
                FROM jobs j LEFT JOIN publications items ON items.id=j.item
                LEFT JOIN publication_links link ON link.target_publication_id=items.id
                LEFT JOIN publications linked_root ON linked_root.id=link.source_publication_id AND linked_root.viewed=0
                WHERE j.id=ANY(%s)""",([row['id'] for row in legacy_sources],)).fetchall()
            for row in legacy_rows:
                code=row['problem_code'] or 'work-discover'
                owner=row['legacy_owner'] or row['item'] or 'demo'
                legacy_keys.add('job:'+code+':'+owner+':discover'+('' if code.startswith('source-') else ':'+(row['item'] or '')))
        simple=[(row,identity) for row,identity in pairs if counts[identity]==1 and row['item'] not in publication_ids and row['item'] not in deleting_feeds and identity not in occupied and row['item'] not in prior_feeds and identity not in legacy_keys]
        if not simple:return rows
        simple_ids={row['id'] for row,_ in simple}
        added=[]
        for row,identity in simple:
            code=row['problem_code'] or 'work-discover'
            body=dict(code=code,section='object',title='Не удалось обновить источник',consequence='Сохранённое содержимое остаётся доступным.',next_step='Включите обработку, чтобы повторить попытку.' if row['status']=='blocked' else 'Повторите загрузку вручную.' if row['status']=='failed' else 'Проверьте доступность источника.' if row['status']=='unavailable' else 'Око повторит попытку автоматически.',objects=[dict(kind='feed',id=row['item'])],action=None,operation='producer:discover',state='retrying' if row['status']=='running' or not row['error'] else 'current',next_at=None)
            if row['error']:body['reason']=row['error']
            if code=='source-reconnect':body.update(section='action',next_step='Проверьте привязку источника; доступ не изменяется автоматически.')
            added.append((uuid.uuid4().hex,identity,'background',json.dumps(body),now))
        self.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome)
            SELECT id,identity,origin,body,created,NULL,NULL FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::double precision[]) AS p(id,identity,origin,body,created)""",tuple([row[i] for row in added] for i in range(5)))
        status_changed(self.db)
        return [row for row in rows if row['id'] not in simple_ids]

    def recover_discover_problems(self) -> None:
        if self.db.execute("SELECT 1 FROM state WHERE key='discover-problems-recovered-v1'").fetchone():
            return
        cursor = None
        while True:
            rows = self.db.execute("SELECT * FROM jobs WHERE kind='discover' AND (error IS NOT NULL OR problem_code IS NOT NULL)" + (" AND id>%s" if cursor is not None else "") + " ORDER BY id LIMIT 100", (cursor,) if cursor is not None else ()).fetchall()
            if not rows:
                break
            with self.db:
                complex_rows=self.recover_simple_discover_batch(list(rows),self.store.wall_clock())
                for row in complex_rows:
                    self.sync_discover(row['id'],self.store.wall_clock())
            cursor=rows[-1]['id']
            self.store.fault('discover_problem_recovery_batch')
        record_cursor = None
        while True:
            rows=self.db.execute("""SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND identity LIKE 'job:%%'
                AND (body::jsonb->>'operation'='producer:discover' OR ((body::jsonb->>'operation' IS NULL OR body::jsonb->>'operation'='producer:status') AND identity LIKE '%%:discover%%'))""" + (' AND id>%s' if record_cursor is not None else ' AND id>=%s') + ' ORDER BY id LIMIT 100',(record_cursor if record_cursor is not None else '',)).fetchall()
            if not rows:
                break
            parsed: list[tuple[Any,str,set[str]]] = []
            all_feeds: set[str] = set()
            for record in rows:
                try:body=json.loads(record['body'])
                except (TypeError,ValueError):continue
                code=body.get('code') if isinstance(body,dict) else None
                objects=body.get('objects') if isinstance(body,dict) else None
                if not isinstance(code,str) or not isinstance(objects,list) or any(not isinstance(obj,dict) or obj.get('kind')!='feed' or not isinstance(obj.get('id'),str) for obj in objects):continue
                feeds={obj['id'] for obj in objects if obj['id']}
                if not feeds or legacy_discover_classification(record['identity'],body)!='discover':continue
                parsed.append((record,code,feeds))
                all_feeds.update(feeds)
            with self.db:
                aliases=sorted(all_feeds | ({''} if 'demo' in all_feeds else set()))
                active={(row['item'] or 'demo',row['code']) for row in self.db.execute("SELECT item,coalesce(problem_code,'work-discover') AS code FROM jobs WHERE kind='discover' AND (item=ANY(%s) OR (item IS NULL AND %s=1)) AND status IN ('pending','running','failed','unavailable','blocked') AND (error IS NOT NULL OR problem_code IS NOT NULL)",(aliases,'demo' in all_feeds))} if aliases else set()
                legacy_active: set[tuple[str,str]] = set()
                if all_feeds:
                    legacy_active.update((row['feed_id'],row['code']) for row in self.db.execute("""SELECT owner.feed_id,coalesce(j.problem_code,'work-discover') AS code FROM feed_publications owner
                        JOIN jobs j ON j.item=owner.publication_id AND j.kind='discover'
                        WHERE owner.feed_id=ANY(%s) AND owner.is_primary AND j.status IN ('pending','running','failed','unavailable','blocked')
                          AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL)""",(sorted(all_feeds),)))
                    legacy_active.update((row['feed_id'],row['code']) for row in self.db.execute("""SELECT owner.feed_id,coalesce(j.problem_code,'work-discover') AS code FROM feed_publications owner
                        JOIN publications root ON root.id=owner.publication_id AND root.viewed=0 JOIN publication_links link ON link.source_publication_id=root.id
                        JOIN jobs j ON j.item=link.target_publication_id AND j.kind='discover'
                        WHERE owner.feed_id=ANY(%s) AND owner.is_primary AND j.status IN ('pending','running','failed','unavailable','blocked')
                          AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL)
                          AND NOT EXISTS (SELECT 1 FROM feed_publications target WHERE target.publication_id=j.item AND target.is_primary)""",(sorted(all_feeds),)))
                present={row['id'] for row in self.db.execute("SELECT id FROM feeds WHERE id=ANY(%s) AND connection!='deleting'",(sorted(all_feeds),))} if all_feeds else set()
                profile_feeds={feed for record,code,feeds in parsed if code in ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch') for feed in feeds if (feed,code) in active}
                bindings={row['feed']:row['indexed_role'] for row in self.db.execute('SELECT feed,indexed_role FROM telegram_bindings WHERE feed=ANY(%s)',(sorted(profile_feeds),))} if profile_feeds else {}
                profile_ids={feed:'profile:'+(bindings.get(feed) or feed) for feed in profile_feeds}
                profiles={row['identity']:json.loads(row['body']) for row in self.db.execute('SELECT identity,body FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(list(profile_ids.values()),))} if profile_ids else {}
                suppressed={feed for feed,identity in profile_ids.items() if identity in profiles and any(obj.get('kind')=='feed' and obj.get('id')==feed for obj in profiles[identity]['objects'])}
                resolved: list[str]=[]
                irrelevant: list[str]=[]
                for record,code,feeds in parsed:
                    if any((feed,code) in legacy_active or ((feed,code) in active and feed not in suppressed) for feed in feeds):continue
                    (resolved if feeds & present else irrelevant).append(record['id'])
                for outcome,ids in (('resolved',resolved),('irrelevant',irrelevant)):
                    if ids:
                        self.db.execute('UPDATE reader_problems SET finished=%s,outcome=%s WHERE id=ANY(%s) AND finished IS NULL',(self.store.wall_clock(),outcome,ids))
                        status_changed(self.db)
            record_cursor=rows[-1]['id']
            self.store.fault('discover_problem_record_recovery_batch')
        with self.db:
            self.db.execute("INSERT INTO state(key,value) VALUES ('discover-problems-recovered-v1','true') ON CONFLICT DO NOTHING")

    def discover_legacy_active(self, owner: str, code: str, excluded_job: int) -> bool:
        params=(owner,excluded_job,code)
        query="""SELECT 1 FROM jobs j WHERE j.kind='discover' AND j.item=%s AND j.id!=%s AND j.status IN ('pending','running','failed','unavailable','blocked') AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL) AND coalesce(j.problem_code,'work-discover')=%s LIMIT 1"""
        if self.db.execute(query,params).fetchone():return True
        query="""SELECT 1 FROM feed_publications owner JOIN jobs j ON j.item=owner.publication_id AND j.kind='discover'
            WHERE owner.feed_id=%s AND owner.is_primary AND j.id!=%s AND j.status IN ('pending','running','failed','unavailable','blocked')
              AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL) AND coalesce(j.problem_code,'work-discover')=%s LIMIT 1"""
        if self.db.execute(query,params).fetchone():return True
        query="""SELECT 1 FROM feed_publications owner JOIN publications root ON root.id=owner.publication_id AND root.viewed=0
            JOIN publication_links link ON link.source_publication_id=root.id JOIN jobs j ON j.item=link.target_publication_id AND j.kind='discover'
            WHERE owner.feed_id=%s AND owner.is_primary AND j.id!=%s AND j.status IN ('pending','running','failed','unavailable','blocked')
              AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL) AND coalesce(j.problem_code,'work-discover')=%s
              AND NOT EXISTS (SELECT 1 FROM feed_publications target WHERE target.publication_id=j.item AND target.is_primary) LIMIT 1"""
        return self.db.execute(query,params).fetchone() is not None

    def discover_legacy_owners(self, row: Any) -> list[str]:
        rows=self.db.execute(f"""SELECT coalesce({owner_expr('items')},{owner_expr('linked_root')},%s) AS feed
            FROM (SELECT %s::text AS item) j LEFT JOIN publications items ON items.id=j.item
            LEFT JOIN publication_links link ON link.target_publication_id=items.id
            LEFT JOIN publications linked_root ON linked_root.id=link.source_publication_id AND linked_root.viewed=0""",(row['feed_id'],row['item'])).fetchall()
        return sorted({r['feed'] or row['item'] or 'demo' for r in rows})

    def sync_discover(self, job_id: int, now: float, *, previous_codes: tuple[str,...] = ()) -> None:
        changed = self.db.execute("SELECT * FROM jobs WHERE id=%s AND kind='discover'", (job_id,)).fetchone()
        if not changed:
            return
        feed = changed['item'] or 'demo'
        old = self.db.execute("SELECT identity,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:discover' AND (body::jsonb->'objects') @> %s::jsonb", (json.dumps([dict(kind='feed',id=feed)]),)).fetchall()
        jobs=self.db.execute("SELECT * FROM jobs WHERE kind='discover' AND (item=ANY(%s) OR (item IS NULL AND %s=1)) AND status IN ('pending','running','failed','unavailable','blocked') AND (error IS NOT NULL OR problem_code IS NOT NULL) ORDER BY id",(['','demo'] if feed=='demo' else [feed],feed=='demo')).fetchall()
        feed_row=self.db.execute('SELECT connection FROM feeds WHERE id=%s',(feed,)).fetchone()
        deleting=bool(feed_row and feed_row['connection']=='deleting')
        candidates: dict[str,dict[str,Any]]={}
        for row in (() if deleting else jobs):
            code=row['problem_code'] or 'work-discover'
            if code in ('codex-choice','ai-permission','youtube-admission','processor-config'):
                continue
            if self.discover_profile_suppresses(feed,code):
                continue
            identity='job:'+code+':'+feed+':discover'+('' if code.startswith('source-') else ':'+(row['item'] or ''))
            if identity in candidates:
                continue
            body=dict(id=identity,code=code,section='object',title='Не удалось обновить источник',consequence='Сохранённое содержимое остаётся доступным.',next_step='Включите обработку, чтобы повторить попытку.' if row['status']=='blocked' else 'Повторите загрузку вручную.' if row['status']=='failed' else 'Проверьте доступность источника.' if row['status']=='unavailable' else 'Око повторит попытку автоматически.',objects=[dict(kind='feed',id=feed)],action=None,operation='producer:discover',state='retrying' if row['status']=='running' or not row['error'] else 'current',next_at=None)
            if row['error']:body['reason']=row['error']
            if code=='source-reconnect':body.update(section='action',next_step='Проверьте привязку источника; доступ не изменяется автоматически.')
            candidates[identity]=body
        current={}
        for canonical,candidate in candidates.items():
            identity=canonical
            if not self.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(canonical,)).fetchone():
                for row in jobs:
                    code=row['problem_code'] or 'work-discover'
                    expected='job:'+code+':'+feed+':discover'+('' if code.startswith('source-') else ':'+(row['item'] or ''))
                    if expected!=canonical:
                        continue
                    for old_owner in self.discover_legacy_owners(row):
                        legacy='job:'+code+':'+old_owner+':discover'+('' if code.startswith('source-') else ':'+(row['item'] or ''))
                        if legacy==canonical:
                            continue
                        previous=self.db.execute('SELECT id,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(legacy,)).fetchone()
                        if not previous:
                            continue
                        saved=json.loads(previous['body'])
                        if not isinstance(saved,dict):continue
                        other_jobs=self.discover_legacy_active(old_owner,code,row['id']) if code.startswith('source-') else False
                        if not other_jobs and legacy_discover_classification(legacy,saved)=='discover' and saved.get('code')==code and any(obj.get('kind')=='feed' and obj.get('id')==old_owner for obj in saved.get('objects',[]) if isinstance(obj,dict)):
                            self.db.execute('UPDATE reader_problems SET identity=%s WHERE id=%s',(canonical,previous['id']))
                            status_changed(self.db)
                            self.sync_candidate(canonical,candidate,now)
                            break
                    if self.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(canonical,)).fetchone():
                        break
            previous=next((record for record in old if json.loads(record['body']).get('discover_job_identity')==canonical),None)
            if previous:
                identity=previous['identity']
                candidate['id']=identity
                candidate['discover_job_identity']=canonical
            else:
                occupied=self.db.execute('SELECT body FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
                legacy_match=False
                if occupied:
                    saved=json.loads(occupied['body'])
                    saved=saved if isinstance(saved,dict) else {}
                    kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
                    tail=identity[len('job:'+candidate['code']+':'):]
                    found=[kind for kind in kinds if tail.endswith(':'+kind) and len(tail)>len(kind)+1] if candidate['code'].startswith('source-') else [kind for kind in kinds if ':'+kind+':' in tail]
                    legacy_match=(saved.get('operation') in (None,'producer:status') and saved.get('code')==candidate['code'] and found==['discover'] and any(obj.get('kind')=='feed' and obj.get('id')==feed for obj in saved.get('objects',[]) if isinstance(obj,dict)))
                same_cause=False
                if occupied:
                    saved=json.loads(occupied['body'])
                    saved=saved if isinstance(saved,dict) else {}
                    same_cause=(saved.get('operation')=='producer:discover' and saved.get('code')==candidate['code'] and saved.get('discover_job_identity',canonical)==canonical and any(obj.get('kind')=='feed' and obj.get('id')==feed for obj in saved.get('objects',[]) if isinstance(obj,dict)))
                if occupied and not same_cause and not legacy_match:
                    digest=hashlib.sha256(canonical.encode()).hexdigest()
                    for _ in range(3):
                        alternate='job:discover-isolated:'+digest+':'+uuid.uuid4().hex
                        if not self.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(alternate,)).fetchone():
                            identity=alternate
                            candidate['id']=identity
                            candidate['discover_job_identity']=canonical
                            break
                    else:
                        raise RuntimeError('Unable to allocate discover problem identity')
            current[identity]=candidate
        completed_codes=set(previous_codes)
        for prior in old:
            if prior['identity'] not in current:
                body=json.loads(prior['body'])
                if isinstance(body.get('code'),str):completed_codes.add(body['code'])
        for code in completed_codes:
            if any(candidate['code']==code for candidate in current.values()):
                continue
            for previous_owner in self.discover_legacy_owners(changed):
                if previous_owner==feed:
                    continue
                legacy='job:'+code+':'+previous_owner+':discover'+('' if code.startswith('source-') else ':'+(changed['item'] or ''))
                shared=self.db.execute('SELECT body FROM reader_problems WHERE identity=%s AND finished IS NULL',(legacy,)).fetchone()
                if shared:
                    saved=json.loads(shared['body'])
                    if (isinstance(saved,dict) and saved.get('code')==code and legacy_discover_classification(legacy,saved)=='discover'
                        and any(obj.get('kind')=='feed' and obj.get('id')==previous_owner for obj in saved.get('objects',[]) if isinstance(obj,dict))
                        and (not code.startswith('source-') or not self.discover_legacy_active(previous_owner,code,changed['id']))):
                        self.sync_candidate(legacy,None,now,removed_outcome='irrelevant' if deleting else 'resolved')
        for prior in old:
            if prior['identity'] not in current:
                self.sync_candidate(prior['identity'],None,now,removed_outcome='irrelevant' if deleting else 'resolved')
        for identity,candidate in current.items():
            self.sync_candidate(identity,candidate,now)

    def sync_read_feed(self, feed: str | None, now: float, *, legacy_identities: tuple[str,...] = ()) -> None:
        """Reconcile only the read Job and current read problems of one feed."""
        from oko.storage.read_job_problems import ReadJobProblem, candidates as read_candidates, classify_legacy
        display_feed=feed or 'demo'
        aliases = ['', 'demo'] if display_feed=='demo' else [feed]
        alias_filter = '(item=ANY(%s) OR item IS NULL)' if display_feed=='demo' else 'item=ANY(%s)'
        rows=self.db.execute("SELECT id,item FROM jobs WHERE kind='read' AND "+alias_filter+" ORDER BY id",(aliases,)).fetchall()
        job_ids=[row['id'] for row in rows]
        candidates_by_identity: dict[str,ReadJobProblem] = {}
        for job in rows:
            for item in read_candidates(self.store,job['id'],now):
                previous_item = candidates_by_identity.get(item.body['id'])
                if previous_item is None:
                    candidates_by_identity[item.body['id']] = item
                else:
                    candidates_by_identity[item.body['id']] = ReadJobProblem(previous_item.body, previous_item.legacy_identities | item.legacy_identities)
        candidates=list(candidates_by_identity.values())
        needle=json.dumps([dict(kind='feed',id=display_feed)])
        matching_rows = self.db.execute("""WITH affected AS MATERIALIZED (
            SELECT id,identity,body FROM reader_problems WHERE finished IS NULL
            AND body::jsonb->>'operation'='producer:read' AND (body::jsonb->'objects') @> %s::jsonb)
            SELECT id,identity,body FROM affected WHERE identity LIKE 'job:%%' AND coalesce(body::jsonb->>'code','')!='processor-config'""",(needle,)).fetchall()
        existing={record['identity']:record for record in matching_rows
                  if self.valid_read_problem_body(record['body']) and classify_legacy(record['identity'],json.loads(record['body']))=='read'}
        for item in candidates:
            canonical=item.body['id']
            def same_cause(record: Any) -> bool:
                if not self.valid_read_problem_body(record['body']):
                    return False
                body=json.loads(record['body'])
                return (classify_legacy(record['identity'],body)=='read' and body.get('code')==item.body['code']
                        and any(obj['id']==display_feed for obj in body['objects'])
                        and body.get('read_job_identity',canonical)==canonical)
            prior=next((record for record in existing.values() if same_cause(record)
                        and json.loads(record['body']).get('read_job_identity')==canonical),None)
            if prior:
                item.body['id']=prior['identity']
                item.body['read_job_identity']=canonical
                continue
            record=self.db.execute('SELECT id,identity,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(canonical,)).fetchone()
            if record is None or same_cause(record):
                continue
            digest=hashlib.sha256(canonical.encode('utf-8')).hexdigest()
            for _ in range(3):
                alternative='job:read-isolated:'+digest+':'+new_read_problem_token()
                occupied=self.db.execute('SELECT id,identity,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(alternative,)).fetchone()
                if occupied is None:
                    item.body['id']=alternative
                    item.body['read_job_identity']=canonical
                    break
            else:
                raise RuntimeError('Unable to allocate read problem identity')
        current={item.body['id']:item for item in candidates}
        if legacy_identities:
            for record in self.db.execute('SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(list(legacy_identities),)):
                if self.valid_read_problem_body(record['body']) and classify_legacy(record['identity'],json.loads(record['body']))=='read':
                    existing[record['identity']]=record
        for item in candidates:
            for legacy in item.legacy_identities:
                if legacy in current or legacy in existing:
                    continue
                found_record=self.db.execute('SELECT id,identity,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(legacy,)).fetchone()
                if found_record:
                    existing[legacy]=found_record
        if current:
            for record in self.db.execute('SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(list(current),)):
                existing[record['identity']]=record
        protected: set[str] = {identity for identity,record in existing.items()
                               if not self.valid_read_problem_body(record['body'])
                               or classify_legacy(identity,json.loads(record['body']))!='read'}
        for identity,item in current.items():
            for legacy in sorted(item.legacy_identities):
                if legacy not in existing or legacy in protected or legacy in current:
                    continue
                old=existing[legacy]
                body=json.loads(old['body'])
                still_needed=False
                for obj in body['objects']:
                    other=self.db.execute("SELECT id FROM jobs WHERE kind='read' AND item=%s AND id<>ALL(%s)",(obj['id'],job_ids)).fetchone()
                    if not other:
                        continue
                    for other_candidate in read_candidates(self.store,other['id'],now):
                        if other_candidate.body['id']==legacy:
                            still_needed=True
                        elif legacy in other_candidate.legacy_identities and not self.db.execute(
                            'SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(other_candidate.body['id'],)).fetchone():
                            still_needed=True
                    if still_needed:
                        break
                if still_needed:
                    protected.add(legacy)
                    continue
                if identity not in existing:
                    self.db.execute('UPDATE reader_problems SET identity=%s WHERE id=%s',(identity,old['id']))
                    existing[identity]=old
                    del existing[legacy]
                    status_changed(self.db)
                    break
        feed_row=self.db.execute('SELECT connection FROM feeds WHERE id=%s',(display_feed,)).fetchone()
        outcome='irrelevant' if not feed_row or feed_row['connection']=='deleting' else 'resolved'
        for identity in existing:
            if identity not in current and identity not in protected:
                self.sync_candidate(identity,None,now,removed_outcome='irrelevant' if any(identity in item.legacy_identities for item in candidates) else outcome)
        for item in candidates:
            if item.body['id'] not in protected:
                self.sync_candidate(item.body['id'],item.body,now)

    def recover_read_problems(self) -> None:
        """One-time, restartable recovery of existing read Job problems."""
        if self.db.execute("SELECT 1 FROM state WHERE key='read-problems-recovered-v1'").fetchone():
            return
        job_cursor: int | None = None
        while True:
            job_lower_bound = '' if job_cursor is None else ' AND id>%s'
            job_params = () if job_cursor is None else (job_cursor,)
            rows=self.db.execute("""SELECT id,item FROM jobs WHERE kind='read'
                AND (error IS NOT NULL OR problem_code IS NOT NULL)"""+job_lower_bound+' ORDER BY id LIMIT 100',job_params).fetchall()
            if not rows:
                break
            with self.db:
                for row in rows:
                    self.sync_read_feed(row['item'],self.store.wall_clock())
            job_cursor=rows[-1]['id']
            self.store.fault('read_problem_recovery_batch')
        problem_cursor: str | None = None
        while True:
            problem_lower_bound = '' if problem_cursor is None else ' AND id>%s'
            problem_params = () if problem_cursor is None else (problem_cursor,)
            problem_query="""SELECT id,identity,body FROM reader_problems WHERE finished IS NULL
                AND identity LIKE 'job:%%' AND coalesce(body::jsonb->>'code','')!='processor-config'
                AND (body::jsonb->>'operation'='producer:read' OR ((body::jsonb->>'operation' IS NULL OR body::jsonb->>'operation'='producer:status')
                    AND (identity LIKE '%%:read' OR identity LIKE '%%:read:%%')))
                """+problem_lower_bound+' ORDER BY id LIMIT 100'
            if problem_cursor is None:
                # Without bound values psycopg leaves %% unchanged; the exact
                # LIKE predicate lets PostgreSQL use the partial recovery index.
                problem_query=problem_query.replace('%%','%')
            rows=self.db.execute(problem_query,problem_params).fetchall()
            if not rows:
                break
            with self.db:
                for row in rows:
                    if not self.valid_read_problem_body(row['body']):
                        continue
                    body=json.loads(row['body'])
                    from oko.storage.read_job_problems import classify_legacy
                    if classify_legacy(row['identity'],body)!='read':
                        continue
                    feeds=[obj['id'] for obj in body['objects'] if obj['kind']=='feed']
                    if not feeds and isinstance(body.get('code'),str):
                        prefix='job:'+body['code']+':'
                        identity=row['identity']
                        if identity.startswith(prefix) and identity.endswith(':read'):
                            feeds=[identity[len(prefix):-len(':read')]]
                        elif identity.startswith(prefix) and identity.count(':read:')==1:
                            feeds=[identity.rsplit(':read:',1)[1]]
                    if not any(feeds):
                        continue  # Preserve an unidentifiable legacy record as current.
                    for feed in set(feeds):
                        if feed:
                            self.sync_read_feed(feed,self.store.wall_clock(),legacy_identities=(row['identity'],))
            problem_cursor=rows[-1]['id']
            self.store.fault('read_problem_record_recovery_batch')
        with self.db:
            self.db.execute("INSERT INTO state(key,value) VALUES ('read-problems-recovered-v1','true') ON CONFLICT DO NOTHING")

    def recover_deletion_problems(self) -> None:
        if self.db.execute("SELECT 1 FROM state WHERE key='delete-problems-recovered-v1'").fetchone():
            return
        cursor: int | None = None
        while True:
            boundary = '' if cursor is None else ' AND id>%s'
            params = () if cursor is None else (cursor,)
            rows=self.db.execute("""SELECT id FROM jobs WHERE kind IN ('delete','feed-delete')
                AND (error IS NOT NULL OR problem_code IS NOT NULL)
                AND coalesce(nullif(problem_code,''),'work-'||kind) IN ('delete','feed-delete','work-delete','work-feed-delete')"""+boundary+' ORDER BY id LIMIT 100',params).fetchall()
            if not rows:
                break
            with self.db:
                for row in rows:
                    self.sync_deletion(row['id'],[],self.store.wall_clock())
            cursor=rows[-1]['id']
            self.store.fault('delete_problem_recovery_batch')
        problem_cursor: str | None = None
        while True:
            boundary = '' if problem_cursor is None else ' AND id>%s'
            problem_params = () if problem_cursor is None else (problem_cursor,)
            query="""SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND identity LIKE 'job:%%'
                AND body::jsonb->>'code' IN ('delete','feed-delete','work-delete','work-feed-delete')
                AND (body::jsonb->>'operation' IN ('producer:delete','producer:feed-delete') OR body::jsonb->>'operation' IS NULL OR body::jsonb->>'operation'='producer:status')"""+boundary+' ORDER BY id LIMIT 100'
            if problem_cursor is None:
                query=query.replace('%%','%')
            records=self.db.execute(query,problem_params).fetchall()
            if not records:
                break
            with self.db:
                for record in records:
                    try:
                        body=json.loads(record['body'])
                    except (TypeError,ValueError):
                        continue
                    if not isinstance(body,dict) or not isinstance(body.get('objects'),list):
                        continue
                    if any(not isinstance(obj,dict) for obj in body['objects']):
                        continue
                    code=body.get('code')
                    canonical=body.get('deletion_job_identity',record['identity'])
                    if not isinstance(code,str) or not isinstance(canonical,str) or not canonical.startswith('job:'+code+':'):
                        continue
                    tail=canonical[len('job:'+code+':'):]
                    kinds=[kind for kind in ('fetch','discover','download_media','filter','theses','read','delete','feed-delete') if ':'+kind+':' in tail]
                    if len(kinds)!=1 or kinds[0] not in ('delete','feed-delete'):
                        continue
                    kind=kinds[0]
                    delimiter=':'+kind+':'
                    if tail.count(delimiter)!=1:
                        continue
                    item=tail.split(delimiter,1)[1]
                    candidate=dict(id=record['identity'],code=code,operation='producer:'+kind,deletion_job_id=str(body.get('deletion_job_id','')))
                    if body.get('deletion_job_identity'):
                        candidate['id']=canonical
                    if not self.deletion_record_matches(record,candidate):
                        continue
                    job=self.db.execute('SELECT id,status,error,problem_code FROM jobs WHERE kind=%s AND item=%s',(kind,item)).fetchone()
                    job_code=(job['problem_code'] or 'work-'+kind) if job else None
                    if job and job_code==code and (job['error'] is not None or job['problem_code'] is not None) and job['status'] in ('pending','running','failed','unavailable','blocked'):
                        self.sync_deletion(job['id'],[candidate],self.store.wall_clock())
                    else:
                        self.sync_candidate(record['identity'],None,self.store.wall_clock())
            problem_cursor=records[-1]['id']
            self.store.fault('delete_problem_record_recovery_batch')
        with self.db:
            self.db.execute("INSERT INTO state(key,value) VALUES ('delete-problems-recovered-v1','true') ON CONFLICT DO NOTHING")

    def profile_candidates(self, now: float, feeds: dict[str, dict[str, Any]], bindings: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for saved_feed in feeds.values():
            if saved_feed['kind'] != 'telegram' or saved_feed['connection'] == 'deleting':
                continue
            binding = bindings.get(saved_feed['id'])
            if not binding or binding['kind'] != 'client':
                continue
            role = binding.get('role')
            profile = self.store.subscriptions.telegram.profile(role) if role else None
            if binding['kind'] == 'client' and profile and profile['state'] == 'ready':
                continue
            code = 'identity-unconfirmed' if not role or not profile else 'account_identity_mismatch' if profile['state'] == 'identity_mismatch' else 'profile_unavailable'
            identity = 'profile:' + (role or saved_feed['id'])
            if identity not in groups:
                groups[identity] = dict(id=identity,code=code,section='action',title='Требуется проверка профиля Telegram',
                    consequence='Сбор и отметки этого аккаунта приостановлены; локальные материалы доступны.',
                    next_step='Сопоставьте существующую конфигурацию и подтвердите прежнюю личность.',
                    reason=profile.get('message','') if profile else 'Прежний alias ещё не сопоставлен роли.',
                    objects=[],action=None,operation='producer:profile',created_at=now,state='current',finished_at=None,outcome=None,next_at=None)
            groups[identity]['objects'].append(dict(kind='feed',id=saved_feed['id']))
        return groups

    def sync_identity_profile_transition(self, before: dict[int,list[dict[str,Any]]], now: float) -> None:
        """Batch unambiguous per-job profile suppression; fall back for complex history."""
        from oko.storage.fetch_job_problems import managed_candidates_many
        ordered=sorted(before)
        for start in range(0,len(ordered),100):
            ids=ordered[start:start+100]
            after=managed_candidates_many(self.store,ids,now)
            simple={job_id:parts[0] for job_id in ids if len(parts:=before[job_id])==1 and not after.get(job_id)}
            keys=[candidate['id'] for candidate in simple.values()]
            canonical={row['identity']:row for row in self.db.execute('SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(keys,))} if keys else {}
            isolated: set[str]=set()
            if keys:
                for field in ('fetch_job_identity','status_job_identity'):
                    for row in self.db.execute(f"""SELECT r.canonical FROM unnest(%s::text[]) t(canonical) CROSS JOIN LATERAL
                        (SELECT body::jsonb->>'{field}' AS canonical FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:fetch' AND body::jsonb->>'{field}'=t.canonical LIMIT 1) r""",(keys,)):
                        isolated.add(row['canonical'])
            counts: dict[str,int]={}
            for candidate in simple.values():counts[candidate['id']]=counts.get(candidate['id'],0)+1
            closed=[];processed=set()
            for job_id,candidate in simple.items():
                saved_row=canonical.get(candidate['id'])
                if counts[candidate['id']]==1 and candidate['id'] not in isolated and saved_row is not None and self.fetch_record_matches(saved_row,candidate):
                    closed.append(saved_row['id']);processed.add(job_id)
            if closed:
                changed=self.db.execute("UPDATE reader_problems SET finished=%s,outcome='resolved' WHERE id=ANY(%s) AND finished IS NULL",(now,closed)).rowcount
                if changed:status_changed(self.db)
                self.db.execute("UPDATE jobs SET identity_event_owned=true WHERE id=ANY(%s) AND problem_code='identity-unconfirmed'",(sorted(processed),))
            opening=[{'id':job_id} for job_id in ids if not before[job_id] and after.get(job_id)]
            if opening:
                complex_rows=self.recover_fetch_batch(opening,now)
                complex_ids={row['id'] for row in complex_rows}
                simple_ids=[row['id'] for row in opening if row['id'] not in complex_ids]
                if simple_ids:self.db.execute("UPDATE jobs SET identity_event_owned=true WHERE id=ANY(%s) AND problem_code='identity-unconfirmed'",(simple_ids,))
                processed.update(simple_ids)
            for job_id in ids:
                if job_id not in processed:self.sync_fetch(job_id,before[job_id],now,allow_rekey=False)

    def sync_profiles(self, now: float, roles: set[str] | None = None) -> None:
        from oko.storage import memberships
        memberships.lock_graph(self.db)
        if roles is None:
            previous = {row['identity']:row['body'] for row in self.db.execute("SELECT identity,body FROM reader_problems WHERE identity LIKE 'profile:%%' AND finished IS NULL")}
            identities = set(previous)
        else:
            identities = {'profile:'+role for role in roles}
            previous = {row['identity']:row['body'] for row in self.db.execute('SELECT identity,body FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL', (sorted(identities),))}
        with reading_snapshot():
            if roles is None:
                feeds = {row['id']:dict(row) for row in self.db.execute('SELECT id,kind,connection FROM feeds ORDER BY sort_order')}
            else:
                role_list = sorted(roles)
                ids = set(roles)
                ids.update(row['feed'] for row in self.db.execute("SELECT feed FROM telegram_bindings WHERE indexed_role=ANY(%s) OR (indexed_role IS NULL AND feed=ANY(%s))", (role_list,role_list)))
                ids.update(obj['id'] for body in previous.values() for obj in json.loads(body)['objects'] if obj['kind']=='feed')
                feeds = {row['id']:dict(row) for row in self.db.execute(
                    'SELECT id,kind,connection FROM feeds WHERE id=ANY(%s) ORDER BY sort_order', (sorted(ids),))}
            bindings = self.store.subscriptions.telegram.bindings(list(feeds))
            if roles is None:
                indexed = [(feed, binding.get('role') if isinstance(binding,dict) else None) for feed,binding in bindings.items()]
                for start in range(0,len(indexed),400):
                    batch = indexed[start:start+400]
                    roles_for_batch = [role if isinstance(role,str) and '\x00' not in role else None for _,role in batch]
                    self.db.execute('UPDATE telegram_bindings SET indexed_role=source.role FROM unnest(%s::text[],%s::text[]) AS source(feed,role) WHERE telegram_bindings.feed=source.feed AND telegram_bindings.indexed_role IS DISTINCT FROM source.role',([feed for feed,_ in batch],roles_for_batch))
            candidates = self.profile_candidates(now,feeds,bindings)
        if roles is None:
            identities.update(candidates)
        new_profile_feeds = {obj['id'] for group in candidates.values() for obj in group['objects'] if obj['kind']=='feed'}
        old_profile_feeds = {obj['id'] for body in previous.values() for obj in json.loads(body)['objects'] if obj['kind']=='feed'}
        profile_visibility_changed=new_profile_feeds ^ old_profile_feeds
        if profile_visibility_changed:
            self.db.ensure_identity_profile_workset()
            self.db.execute('DELETE FROM oko_identity_profile_work')
            from oko.storage.fetch_job_problems import managed_candidates_many
            feeds_changed=sorted(profile_visibility_changed)
            for start in range(0,len(feeds_changed),100):
                batch=feeds_changed[start:start+100]
                for jobs in self.db.incomplete_row_pages("""SELECT * FROM (WITH roots AS MATERIALIZED (SELECT publication_id FROM feed_publications WHERE is_primary AND feed_id=ANY(%s)),
                    affected AS MATERIALIZED (SELECT publication_id AS item FROM roots UNION SELECT l.target_publication_id FROM roots r JOIN publication_links l ON l.source_publication_id=r.publication_id)
                    SELECT j.id FROM affected a CROSS JOIN LATERAL (SELECT id FROM jobs WHERE kind='fetch' AND item=a.item AND problem_code='identity-unconfirmed' OFFSET 0) j
                    UNION SELECT j.id FROM unnest(%s::text[]) AS f(feed) CROSS JOIN LATERAL (SELECT id FROM jobs WHERE kind='fetch' AND problem_code='identity-unconfirmed' AND feed_id IS NOT NULL AND md5(feed_id)=md5(f.feed) AND feed_id=f.feed OFFSET 0) j) AS matching_jobs""",(batch,batch)):
                    fetch_ids=[int(row['id']) for row in jobs]
                    previous_candidates=managed_candidates_many(self.store,fetch_ids,now)
                    self.db.execute("""INSERT INTO oko_identity_profile_work(job_id,previous)
                        SELECT item.id,item.previous::jsonb FROM unnest(%s::bigint[],%s::text[]) AS item(id,previous)
                        ON CONFLICT (job_id) DO NOTHING""",(fetch_ids,[json.dumps(previous_candidates.get(job_id,[])) for job_id in fetch_ids]))
        for identity in sorted(identities):
            candidate = candidates.get(identity)
            outcome = 'resolved'
            if candidate is None and identity in previous:
                if any(obj['kind']=='feed' and feeds.get(obj['id'],{}).get('connection','deleting')=='deleting' for obj in json.loads(previous[identity])['objects']):
                    outcome = 'irrelevant'
            self.sync_candidate(identity,candidate,now,removed_outcome=outcome)
        new_profile_feeds = {obj['id'] for group in candidates.values() for obj in group['objects'] if obj['kind']=='feed'}
        old_profile_feeds = {obj['id'] for body in previous.values() for obj in json.loads(body)['objects'] if obj['kind']=='feed'}
        affected = new_profile_feeds | old_profile_feeds
        profile_visibility_changed=new_profile_feeds ^ old_profile_feeds
        if profile_visibility_changed:
            for page in self.db.incomplete_row_pages('SELECT job_id,previous FROM oko_identity_profile_work ORDER BY job_id'):
                self.sync_identity_profile_transition({int(row['job_id']):row['previous'] for row in page},now)
            self.db.execute('DELETE FROM oko_identity_profile_work')
        manager=getattr(self.store,'incomplete_draft',None)
        if manager is not None and profile_visibility_changed:
            from oko.storage.transaction import begin_write
            begin_write(self.db)
            manager.mark_profile_feeds(profile_visibility_changed)
        if self.db.execute("SELECT 1 FROM state WHERE key='read-problems-recovered-v1'").fetchone():
            feed_ids = sorted(affected)
            for start in range(0,len(feed_ids),400):
                for job in self.db.execute("SELECT item FROM jobs WHERE kind='read' AND item=ANY(%s)",(feed_ids[start:start+400],)):
                    self.sync_read_feed(job['item'],now)
        if self.db.execute("SELECT 1 FROM state WHERE key='discover-problems-recovered-v1'").fetchone():
            for feed in sorted(affected):
                for job in self.db.execute("SELECT id FROM jobs WHERE kind='discover' AND (item=%s OR feed_id=%s) AND (problem_code IS NOT NULL OR error IS NOT NULL)",(feed,feed)):
                    self.sync_discover(job['id'],now)

    def processor_youtube_paused(self, now: float) -> bool:
        from oko.storage.reading import reader_wall
        blocked=self.store.youtube._limits()['blocked_until']
        return not _readable_timestamp(blocked) or blocked>reader_wall(now)

    def processor_configuration_rows(self, job_ids: list[int] | None = None) -> list[Row]:
        restriction=' AND id=ANY(%s)' if job_ids is not None else ''
        params=(job_ids,) if job_ids is not None else ()
        return list(self.db.execute(f"""SELECT jobs.id,jobs.kind,jobs.item,jobs.status,jobs.error,jobs.feed_id,
            CASE WHEN jobs.kind='read' THEN jobs.item ELSE coalesce({owner_expr('items')},{owner_expr('media_owner')},{owner_expr('linked_root')}) END AS resolved_feed_id,
            coalesce(items.viewed,media_owner.viewed) AS viewed, m.resource_key,
            CASE WHEN jobs.kind='read' OR (jobs.kind='download_media' AND m.id IS NULL) THEN NULL ELSE coalesce(linked_root.id,m.publication_id,jobs.item) END AS affected_item
            FROM (SELECT * FROM jobs WHERE processor_visible{restriction} ORDER BY kind,item OFFSET 0) jobs
            LEFT JOIN publications items ON jobs.kind!='download_media' AND items.id=jobs.item
            LEFT JOIN media_files m ON jobs.kind='download_media' AND m.id=jobs.item
            LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
            LEFT JOIN publication_links link ON link.target_publication_id=coalesce(items.id,media_owner.id)
            LEFT JOIN publications linked_root ON linked_root.id=link.source_publication_id AND linked_root.viewed=0
            WHERE (jobs.status IN ('pending','running','failed','unavailable','blocked')
                OR (jobs.status='done' AND jobs.kind='fetch' AND jobs.error IS NOT NULL)) ORDER BY jobs.kind,jobs.item,linked_root.id""",params).fetchall())
    def processor_configuration_candidate(self, now: float, job_ids: list[int] | None = None, *, overrides: dict[int,dict[str,Any]] | None = None) -> dict[str, Any] | None:
        rows=self.processor_configuration_rows(job_ids)
        return self.processor_candidate_from_rows(rows,now,self.processor_youtube_paused(now),overrides=overrides)

    @staticmethod
    def processor_candidate_from_rows(rows: list[Row], now: float, paused: bool, *, overrides: dict[int,dict[str,Any]] | None = None) -> dict[str,Any] | None:
        candidate: dict[str,Any] | None=None
        seen: set[tuple[str,Any]] = set()
        applied: set[int]=set()
        for row in rows:
            if overrides and row['id'] in overrides:
                if row['id'] in applied:
                    continue
                applied.add(row['id'])
                captured=overrides[row['id']]
                if candidate is None:
                    candidate=dict(captured,objects=[])
                for obj in captured['objects']:
                    key=(obj['kind'],obj['id'])
                    if key not in seen:
                        candidate['objects'].append(obj)
                        seen.add(key)
                continue
            kind=row['kind']
            if kind=='download_media' and row['resource_key']=='youtube:subtitles' and paused:
                continue
            if row['viewed'] and kind not in ('delete','feed-delete','discover','read'):
                continue
            feed=(row['item'] if kind in ('discover','read','feed-delete') else row['resolved_feed_id'] or row['feed_id']) or row['item'] or 'demo'
            if candidate is None:
                candidate=dict(id='processor:configuration',code='processor-config',section='action',title='Требуется настройка обработчика',
                    consequence='Око продолжает попытки удаления.' if kind in ('delete','feed-delete') else 'Сохранённое содержимое остаётся доступным.',
                    next_step='Включите обработку, чтобы повторить попытку.' if row['status']=='blocked' else 'Повторите загрузку вручную.' if row['status'] in ('done','failed') else 'Проверьте доступность источника.' if row['status']=='unavailable' else 'Откройте настройки обработчика.',
                    objects=[],action=None,operation='producer:'+kind,created_at=now,
                    state='retrying' if row['status']=='running' or not row['error'] else 'current',finished_at=None,outcome=None,next_at=None)
                if kind!='delete' and row['error']:
                    candidate['reason']=row['error']
            obj_kind='feed' if kind in ('discover','read','feed-delete') else 'material'
            obj_id=feed if obj_kind=='feed' else row['affected_item']
            if kind!='delete' and isinstance(obj_id,str) and obj_id and (obj_kind,obj_id) not in seen:
                candidate['objects'].append(dict(kind=obj_kind,id=obj_id))
                seen.add((obj_kind,obj_id))
        return candidate

    def refresh_processor_visibility(self, job_ids: list[int]) -> bool:
        """Prototype: refresh only explicitly affected Jobs; caller owns transaction."""
        changed=False
        for start in range(0,len(job_ids),400):
            rows=self.db.execute("""UPDATE jobs j SET processor_visible=requested.visible,processor_caption=requested.caption FROM (
                SELECT j.id,coalesce(j.problem_code='processor-config' AND EXISTS (SELECT 1 FROM media_files m WHERE j.kind='download_media' AND m.id=j.item AND m.resource_key='youtube:subtitles'),false) AS caption,coalesce(j.problem_code='processor-config' AND
                    (j.status IN ('pending','running','failed','unavailable','blocked')
                        OR (j.kind='fetch' AND j.status='done' AND j.error IS NOT NULL))
                    AND (j.kind IN ('delete','feed-delete','discover','read') OR CASE WHEN j.kind='download_media' THEN coalesce(
                        (SELECT p.viewed FROM media_files m JOIN publications p ON p.id=m.publication_id WHERE m.id=j.item),0)=0 ELSE coalesce(
                        (SELECT p.viewed FROM publications p WHERE p.id=j.item),0)=0 END),false) AS visible
                FROM jobs j WHERE j.id=ANY(%s) AND (j.processor_visible OR j.processor_caption OR j.problem_code='processor-config') OFFSET 0) AS requested
                WHERE j.id=requested.id AND (j.processor_visible IS DISTINCT FROM requested.visible OR j.processor_caption IS DISTINCT FROM requested.caption) RETURNING j.id""",(job_ids[start:start+400],)).fetchall()
            if rows:
                changed=True
        if changed:
            status_changed(self.db)
        return changed

    def processor_jobs_for_materials(self, items: list[str]) -> list[int]:
        ids: set[int]=set()
        for start in range(0,len(items),400):
            batch=items[start:start+400]
            rows=self.db.execute("""SELECT j.id FROM jobs j WHERE j.item=ANY(%s)
                    AND (j.processor_visible OR j.problem_code='processor-config')
                UNION SELECT j.id FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id
                    WHERE m.publication_id=ANY(%s) AND (j.processor_visible OR j.problem_code='processor-config')""",(batch,batch)).fetchall()
            ids.update(row['id'] for row in rows)
        return sorted(ids)

    def any_visible_processor(self, now: float, excluded: list[int] | None = None) -> bool:
        excluded=excluded or []
        ordinary=self.db.execute('SELECT 1 FROM jobs WHERE processor_visible AND NOT processor_caption AND NOT (id=ANY(%s)) LIMIT 1',(excluded,)).fetchone()
        if ordinary:
            return True
        if self.processor_youtube_paused(now):
            return False
        return self.db.execute('SELECT 1 FROM jobs WHERE processor_visible AND processor_caption AND NOT (id=ANY(%s)) LIMIT 1',(excluded,)).fetchone() is not None

    def capture_processor_history(self, job_id: int, now: float) -> None:
        self.capture_processor_histories([job_id],now)

    def capture_processor_histories(self, job_ids: list[int], now: float) -> None:
        """Keep only affected pre-graph-change facts inside the owning transaction."""
        if not self.db.transaction_depth or not job_ids:
            return
        self.prepare_processor_change([],now)
        paused=self.processor_youtube_paused(now)
        ordered=sorted(set(job_ids)-self._processor_transaction_captures.keys())
        for start in range(0,len(ordered),400):
            rows=self.processor_configuration_rows(ordered[start:start+400])
            groups: dict[int,list[Row]] = {}
            for row in rows:
                groups.setdefault(row['id'],[]).append(row)
            for job_id,job_rows in groups.items():
                previous=self.processor_candidate_from_rows(job_rows,now,paused)
                if previous is None:
                    continue
                self._processor_transaction_captures[job_id]=previous
                def clear(identity: int = job_id, expected: dict[str,Any] = previous) -> None:
                    if self._processor_transaction_captures.get(identity) is expected:
                        self._processor_transaction_captures.pop(identity,None)
                self.db.after_commit(clear)
                self.db.after_rollback(clear)

    def processor_history_before_finish(self, job_id: int, now: float) -> dict[str,Any] | None:
        return self.processor_history_before_batch([job_id],now)

    def processor_history_before_batch(self, job_ids: list[int], now: float) -> dict[str,Any] | None:
        captures: dict[int,dict[str,Any]] = {}
        for job_id in job_ids:
            old=self._processor_transaction_captures.pop(job_id,None)
            if old is not None:
                captures[job_id]=old
        previous=self.prepare_processor_change(job_ids,now)
        if not captures:
            return previous
        if previous is None and self.any_visible_processor(now,job_ids):
            return None
        return self.processor_configuration_candidate(now,overrides=captures)

    def prepare_processor_change(self, job_ids: list[int], now: float, *, source_lock: bool = False) -> dict[str,Any] | None:
        # Acquire the graph before any processor reservation row can be changed.
        # Reuse the reservation read so ordinary jobs do not gain a round trip.
        from oko.storage.transaction import begin_write
        begin_write(self.db)
        pointer=self.db.execute("""SELECT processor_reservation_id,
            CASE WHEN %s=1 OR EXISTS (SELECT 1 FROM jobs WHERE id=ANY(%s)
                AND kind IN ('fetch','download_media','filter','theses','delete','feed-delete'))
            THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text
            ELSE NULL END AS source_graph_lock FROM youtube_access WHERE id=1""",(int(source_lock),job_ids)).fetchone()
        if pointer and pointer['source_graph_lock'] is not None:
            pointer=self.db.execute('SELECT processor_reservation_id FROM youtube_access WHERE id=1').fetchone()
        if self.processor_resume_candidate(now,pointer=pointer):
            self.finish_processor_change([],None,now)
        if not job_ids or not self.db.execute('SELECT 1 FROM jobs WHERE id=ANY(%s) AND processor_visible LIMIT 1',(job_ids,)).fetchone():
            return None
        if self.any_visible_processor(now,job_ids):
            return None
        return self.processor_configuration_candidate(now)

    def prepare_processor_pause(self, now: float) -> dict[str,Any] | None:
        if self.db.execute('SELECT 1 FROM jobs WHERE processor_visible AND NOT processor_caption LIMIT 1').fetchone():
            return None
        return self.processor_configuration_candidate(now)

    @staticmethod
    def valid_processor_problem(raw: str) -> bool:
        try:
            body=json.loads(raw)
        except (TypeError,ValueError):
            return False
        if not isinstance(body,dict) or body.get('code')!='processor-config':
            return False
        if any(not isinstance(body.get(field),str) for field in ('title','consequence','next_step','operation')):
            return False
        if body.get('state') not in ('current','retrying') or body.get('next_at') is not None:
            return False
        if any(field in body and not isinstance(body[field],str) for field in ('reason','request_id')):
            return False
        if 'request_id' in body and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',body['request_id']):
            return False
        objects=body.get('objects')
        return isinstance(objects,list) and all(isinstance(obj,dict) and obj.get('kind') in ('settings','feed','folder','material') and isinstance(obj.get('id'),str) and bool(obj['id']) and not (set(obj)-{'kind','id','label'}) for obj in objects)

    def processor_current(self) -> Any:
        row=self.db.execute("SELECT id,identity,body,created FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        if row and _readable_timestamp(row['created']) and self.valid_processor_problem(row['body']):
            return row
        return self.db.execute("SELECT id,identity,body FROM reader_problems WHERE "+_PROCESSOR_VALID_SQL+" AND body::jsonb->>'processor_group_identity'='processor:configuration' ORDER BY created,id COLLATE \"C\" LIMIT 1").fetchone()

    def recover_processor_duplicates(self, now: float) -> None:
        canonical=self.db.execute("SELECT id,identity,body,created FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        if canonical and (not _readable_timestamp(canonical['created']) or not self.valid_processor_problem(canonical['body'])):
            canonical=None
        query="SELECT id,identity,created FROM reader_problems WHERE "+_PROCESSOR_VALID_SQL+" AND body::jsonb->>'processor_group_identity'='processor:configuration' AND identity!='processor:configuration'"
        first=self.db.execute(query+' ORDER BY created,id COLLATE "C" LIMIT 1').fetchone()
        winner: Row | None
        if canonical and first:
            winner=canonical if (canonical['created'],canonical['id']) <= (first['created'],first['id']) else first
        else:
            winner=canonical or first
        if not winner:
            return
        changed=False
        if canonical and canonical['id']!=winner['id']:
            changed=bool(self.db.execute("UPDATE reader_problems SET finished=%s,outcome='irrelevant' WHERE id=%s AND finished IS NULL",(now,canonical['id'])).rowcount)
        cursor: tuple[float,str] | None=None
        while True:
            boundary='' if cursor is None else ' AND (created,id COLLATE "C")>(%s,%s)'
            params=() if cursor is None else cursor
            rows=self.db.execute(query+boundary+' ORDER BY created,id COLLATE "C" LIMIT 100',params).fetchall()
            if not rows:
                break
            cursor=(rows[-1]['created'],rows[-1]['id'])
            duplicate_ids=[row['id'] for row in rows if row['id']!=winner['id']]
            if duplicate_ids and self.db.execute("UPDATE reader_problems SET finished=%s,outcome='irrelevant' WHERE id=ANY(%s) AND finished IS NULL",(now,duplicate_ids)).rowcount:
                changed=True
            self.store.fault('processor_duplicate_recovery_batch')
        if changed:
            status_changed(self.db)

    def processor_new_identity(self) -> str:
        if not self.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone():
            return 'processor:configuration'
        for _ in range(3):
            identity='processor:configuration-isolated:'+uuid.uuid4().hex
            if not self.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone():
                return identity
        raise RuntimeError('Unable to allocate processor group identity')

    @staticmethod
    def processor_reservation_body(deadline: float) -> dict[str,Any]:
        return dict(code='processor-config',section='action',title='Резерв идентификатора',
            consequence='Служебная запись.',next_step='Ожидание.',objects=[],action=None,
            operation='processor:reservation',state='current',next_at=None,
            processor_reservation=True,deadline=deadline)

    def processor_reservation(self, pointer: Any = None) -> dict[str,Any] | None:
        if pointer is None:pointer=self.db.execute('SELECT processor_reservation_id FROM youtube_access WHERE id=1').fetchone()
        if not pointer or pointer['processor_reservation_id'] is None:
            return None
        identity=pointer['processor_reservation_id']
        if not isinstance(identity,str) or not identity.startswith('processor-reserved-'):
            return None
        reserved=self.db.execute("SELECT body FROM reader_problems WHERE id=%s AND identity=%s AND finished IS NOT NULL",(identity,'processor:reservation:'+identity)).fetchone()
        if not reserved:
            return None
        try:
            body=json.loads(reserved['body'])
        except (TypeError,ValueError):
            return None
        if not isinstance(body,dict) or body.get('processor_reservation') is not True or not isinstance(body.get('deadline'),(int,float)):
            return None
        return dict(id=identity,deadline=body['deadline'])

    def prepare_processor_reservation(self, now: float) -> None:
        paused=self.processor_youtube_paused(now)
        caption=self.db.execute('SELECT 1 FROM jobs WHERE processor_visible AND processor_caption LIMIT 1').fetchone()
        normal=self.db.execute('SELECT 1 FROM jobs WHERE processor_visible AND NOT processor_caption LIMIT 1').fetchone()
        reservation=self.processor_reservation()
        if paused and caption and not normal:
            deadline=self.store.youtube._limits()['blocked_until']
            if not _readable_timestamp(deadline):return
            if reservation is None:
                pointer=expect_row(self.db.execute('SELECT processor_reservation_id FROM youtube_access WHERE id=1').fetchone())
                if pointer[0] is not None:
                    return  # Preserve an unrecognised pointer.
                for _ in range(3):
                    candidate='processor-reserved-'+uuid.uuid4().hex
                    inserted=self.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES (%s,%s,'background',%s,%s,%s,'irrelevant') ON CONFLICT(id) DO NOTHING",(candidate,'processor:reservation:'+candidate,json.dumps(self.processor_reservation_body(deadline)),now,now)).rowcount
                    if inserted:
                        reservation=dict(id=candidate,deadline=deadline)
                        self.db.execute('UPDATE youtube_access SET processor_reservation_id=%s WHERE id=1',(candidate,))
                        break
                else:
                    raise RuntimeError('Unable to reserve processor occurrence identity')
            if reservation['deadline']!=deadline:
                self.db.execute('UPDATE reader_problems SET body=%s,finished=%s WHERE id=%s AND identity=%s',(json.dumps(self.processor_reservation_body(deadline)),now,reservation['id'],'processor:reservation:'+reservation['id']))
        elif reservation is not None and ((paused and (normal or not caption)) or (not normal and not caption)):
            self.db.execute('UPDATE youtube_access SET processor_reservation_id=NULL WHERE id=1 AND processor_reservation_id=%s',(reservation['id'],))
            self.db.execute("DELETE FROM reader_problems WHERE id=%s AND identity=%s",(reservation['id'],'processor:reservation:'+reservation['id']))

    def processor_resume_candidate(self, now: float, *, pointer: Any = None) -> tuple[str,dict[str,Any],float] | None:
        reservation=self.processor_reservation(pointer)
        if not reservation or now<float(reservation['deadline']) or self.processor_youtube_paused(now):
            return None
        if self.processor_current():
            return None
        candidate=self.processor_configuration_candidate(now)
        if candidate is None:
            return None
        return reservation['id'],candidate,float(reservation['deadline'])

    def finish_processor_change(self, job_ids: list[int], previous: dict[str,Any] | None, now: float, *, removed_outcome: str = 'resolved') -> None:
        changed=self.refresh_processor_visibility(job_ids)
        if job_ids and not changed and previous is None:
            return
        self.prepare_processor_reservation(now)
        visible=self.any_visible_processor(now)
        existing=self.processor_current()
        if visible and not existing:
            identity=self.processor_new_identity()
            candidate: dict[str,Any]=dict(id='processor:configuration',code='processor-config',section='action',title='Требуется настройка обработчика',
                consequence='Сохранённое содержимое остаётся доступным.',next_step='Откройте настройки обработчика.',objects=[],action=None,
                operation='producer:processor-config',created_at=now,state='current',finished_at=None,outcome=None,next_at=None)
            candidate['id']=identity
            if identity!='processor:configuration':
                candidate['processor_group_identity']='processor:configuration'
            resumed=self.processor_resume_candidate(now)
            if resumed:
                body={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_ids')}
                activated=self.db.execute("UPDATE reader_problems SET identity=%s,body=%s,created=%s,finished=NULL,outcome=NULL WHERE id=%s AND identity=%s AND finished IS NOT NULL",(identity,json.dumps(body),resumed[2],resumed[0],'processor:reservation:'+resumed[0])).rowcount
                if activated!=1:
                    raise RuntimeError('Processor occurrence reservation disappeared')
                self.db.execute('UPDATE youtube_access SET processor_reservation_id=NULL WHERE id=1 AND processor_reservation_id=%s',(resumed[0],))
            else:
                self.sync_candidate(identity,candidate,now)
        elif not visible and existing:
            identity=existing['identity']
            if previous is not None:
                previous['id']=identity
                if identity!='processor:configuration':
                    previous['processor_group_identity']='processor:configuration'
                self.sync_candidate(identity,previous,now)
            self.sync_candidate(identity,None,now,removed_outcome=removed_outcome)

    def incomplete_generic_groups(self, now: float, scope_items: list[str] | None = None) -> tuple[Any,Any,Any]:
        direct_scope="item=ANY(%s) AND " if scope_items is not None else ""
        media_scope="j.item=ANY(%s) AND " if scope_items is not None else ""
        parameters=(scope_items,scope_items) if scope_items is not None else ()
        source_like="source-%%" if scope_items is not None else "source-%"
        feeds = ({feed['id']: feed for feed in self.store.subscriptions.feeds()} if scope_items is None else {})
        profile_feeds: set[str] = set()
        if scope_items is None:
            profile_feeds = {obj['id'] for row in self.db.execute("SELECT body FROM reader_problems WHERE identity LIKE 'profile:%%' AND finished IS NULL")
                             for obj in json.loads(row['body'])['objects'] if obj['kind'] == 'feed'}
        groups: dict[str | tuple[str,str] | tuple[str,str,str], dict[str, Any]] = {}
        youtube = self.store.youtube.status(now)
        source_owned_filter="AND NOT (kind IS NOT DISTINCT FROM 'fetch' AND problem_code='identity-unconfirmed' AND identity_event_owned) AND NOT (kind IS NOT DISTINCT FROM 'download_media' AND subtitle_event_owned) AND NOT (kind IS NOT DISTINCT FROM 'fetch' AND problem_code IS NULL AND error IS NOT NULL AND fetch_null_owned) AND NOT (kind IN ('fetch','download_media','filter','theses','delete','feed-delete') AND problem_code IS NOT NULL AND problem_code LIKE 'source-%' AND source_event_owned)" if scope_items is None else ''
        admission_filter="AND problem_code IS DISTINCT FROM 'youtube-admission'" if scope_items is None and getattr(self.store,'youtube_admission_ready',False) else ''
        subtitle_owned_filter='AND NOT j.subtitle_event_owned' if scope_items is None else ''
        item_join_filter=("(jobs.kind IS DISTINCT FROM 'download_media' OR left(jobs.problem_code,7)='source-' "
            "OR jobs.problem_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch','youtube-admission') "
            "OR (jobs.problem_code IS NULL AND NOT jobs.subtitle_event_owned) "
            + ("OR (m.resource_key='youtube:subtitles' AND jobs.problem_code IS DISTINCT FROM 'processor-config')" if youtube['paused'] else '') + ")") if scope_items is not None else 'TRUE'
        jobs = self.db.execute(f"SELECT jobs . * , coalesce ( {owner_expr('items')} , {owner_expr('media_owner')} , {owner_expr('linked_root')} ) AS resolved_feed_id , coalesce ( items . viewed , media_owner . viewed ) AS viewed , m . resource_key AS resource_key , m.publication_id AS scoped_media_publication_id , coalesce ( linked_root . id , m . publication_id , jobs . item ) AS affected_item FROM (SELECT * FROM (SELECT * FROM jobs WHERE {direct_scope}kind IS DISTINCT FROM 'read' AND kind IS DISTINCT FROM 'discover' AND ({'TRUE' if scope_items is not None else 'FALSE'} OR problem_code IS DISTINCT FROM 'processor-config') AND ({'TRUE' if scope_items is not None else 'FALSE'} OR NOT ((kind IS NOT DISTINCT FROM 'filter' OR kind IS NOT DISTINCT FROM 'theses') AND item IS NOT NULL AND item<>'' AND (((problem_code='' OR (problem_code IS NULL AND error IS NOT NULL))) OR (problem_code IS NOT NULL AND problem_code<>'' AND problem_code NOT LIKE '{source_like}' AND problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'))))) AND ({'TRUE' if scope_items is not None else 'FALSE'} OR NOT (kind IS NOT DISTINCT FROM 'fetch' AND item IS NOT NULL AND item<>'' AND problem_code IS NOT NULL AND problem_code NOT LIKE '{source_like}' AND problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch') AND NOT fetch_null_ambiguous)) AND NOT (kind IN ('delete','feed-delete') AND coalesce(nullif(problem_code,''),'work-'||kind) IN ('delete','feed-delete','work-delete','work-feed-delete')) AND (problem_code IS NOT NULL OR error IS NOT NULL) {source_owned_filter} {admission_filter} AND ({'TRUE' if scope_items is not None else 'FALSE'} OR NOT (kind IS NOT DISTINCT FROM 'download_media' AND item IS NOT NULL AND item<>'' AND problem_code IS NOT NULL AND problem_code NOT LIKE '{source_like}' AND problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'))) UNION ALL SELECT j.* FROM media_files subtitle JOIN jobs j ON j.kind='download_media' AND j.item=subtitle.id WHERE {media_scope}{'FALSE' if scope_items is not None else 'TRUE'} AND subtitle.resource_key='youtube:subtitles' AND j.kind='download_media' {subtitle_owned_filter} AND j.item IS NOT NULL AND j.item<>'' AND j.problem_code IS NOT NULL AND j.problem_code NOT LIKE '{source_like}' AND j.problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')) status_jobs ORDER BY kind,item OFFSET 0) jobs LEFT JOIN media_files m ON jobs . kind = 'download_media' AND m . id = jobs . item LEFT JOIN publications items ON items . id = jobs . item AND ({item_join_filter}) LEFT JOIN publications media_owner ON media_owner . id = m . publication_id LEFT JOIN publication_links link ON link . target_publication_id = coalesce ( items . id , media_owner . id ) LEFT JOIN publications linked_root ON linked_root . id = link . source_publication_id AND linked_root . viewed = 0 WHERE ( jobs . status IN ( 'pending' , 'running' , 'failed' , 'unavailable' , 'blocked' ) OR ( jobs . status = 'done' AND jobs . kind = 'fetch' AND jobs . problem_code IS NOT NULL AND jobs . error IS NOT NULL ) ) AND ( jobs . problem_code IS NOT NULL OR jobs . error IS NOT NULL ) AND jobs.kind IS DISTINCT FROM 'read' AND ({'TRUE' if scope_items is not None else 'FALSE'} OR jobs.problem_code IS DISTINCT FROM 'processor-config') AND NOT (jobs.kind IN ('delete','feed-delete') AND coalesce(nullif(jobs.problem_code,''),'work-'||jobs.kind) IN ('delete','feed-delete','work-delete','work-feed-delete'))",parameters).fetchall()
        if scope_items is not None:
            source_ids=sorted({job['resolved_feed_id'] or job['feed_id'] for job in jobs if job['resolved_feed_id'] or job['feed_id']})
            for start in range(0,len(source_ids),400):
                batch=source_ids[start:start+400]
                feeds.update({row['id']:dict(row) for row in self.db.execute('SELECT id,kind FROM feeds WHERE id=ANY(%s)',(batch,))})
            profile_codes=('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')
            job_feeds={job['resolved_feed_id'] or job['feed_id'] or job['item'] or 'demo' for job in jobs if job['problem_code'] in profile_codes}
            if job_feeds:
                needles=[json.dumps([dict(kind='feed',id=feed)]) for feed in sorted(job_feeds)]
                for row in self.db.execute("SELECT body FROM reader_problems WHERE identity LIKE 'profile:%%' AND finished IS NULL AND (body::jsonb->'objects') @> ANY(%s::jsonb[])",(needles,)):
                    profile_feeds.update(obj['id'] for obj in json.loads(row['body'])['objects'] if obj['kind']=='feed')
        source_owned: set[str]=set()
        if scope_items is None:
            source_keys={
                'job:'+job['problem_code']+':'+(job['resolved_feed_id'] or job['feed_id'] or job['item'] or 'demo')+':'+job['kind']
                for job in jobs if isinstance(job['problem_code'],str) and job['problem_code'].startswith('source-')
                and job['kind'] in ('fetch','download_media','filter','theses','delete','feed-delete')
            }
            ordered_source_keys=sorted(source_keys)
            for start in range(0,len(ordered_source_keys),400):
                rows=self.db.execute("""SELECT requested.canonical,min(found.body) AS body
                    FROM unnest(%s::text[]) requested(canonical)
                    CROSS JOIN LATERAL (SELECT body FROM reader_problems
                        WHERE finished IS NULL AND octet_length(body::jsonb->>'source_group_identity')<=1000 AND body::jsonb->>'source_group_identity'=requested.canonical LIMIT 2) found
                    GROUP BY requested.canonical HAVING count(*)=1""",(ordered_source_keys[start:start+400],))
                from oko.storage import source_groups
                for row in rows:
                    canonical=row['canonical'];kind=canonical.rsplit(':',1)[-1]
                    if not source_groups.manageable(canonical,kind):continue
                    try:body=json.loads(row['body'])
                    except (TypeError,ValueError):continue
                    if not isinstance(body,dict) or body.get('operation')!='producer:'+kind or body.get('code') not in {code for code,_ in source_groups.possible_pairs(canonical,kind)} or any(not isinstance(body.get(field),str) for field in ('title','section','consequence','next_step')) or body.get('state') not in ('current','retrying') or body.get('action') is not None:continue
                    candidate,_,_=source_groups.group_snapshot(self.store,canonical,kind,now)
                    if candidate is None or len(candidate.get('_source_codes',set()))!=1 or body.get('code') not in candidate.get('_source_codes',set()) or not isinstance(body.get('objects'),list):continue
                    allowed={(obj.get('kind'),obj.get('id')) for obj in candidate['objects']}
                    if (body['objects'] or kind=='delete') and all(isinstance(obj,dict) and set(obj)=={'kind','id'} and (obj.get('kind'),obj.get('id')) in allowed for obj in body['objects']) and len({(obj['kind'],obj['id']) for obj in body['objects']})==len(body['objects']):source_owned.add(canonical)
        for job in jobs:
            resolved_feed = job['item'] if job['kind']=='read' else job['resolved_feed_id'] or job['feed_id']
            code = job['problem_code']
            if scope_items is not None and job['kind']=='download_media' and code=='processor-config' and job['scoped_media_publication_id'] is None:
                continue
            if code=='processor-config' and (scope_items is None or not job['processor_visible']):
                continue
            if job['kind']=='discover' and code!='processor-config':
                continue
            if job['kind']=='read' and code!='processor-config':
                continue
            if code in ('codex-choice', 'ai-permission'):
                continue
            if job['kind']=='download_media' and job['resource_key']=='youtube:subtitles':
                if youtube['paused'] or code is None and youtube['next_at']>now:
                    continue
            if code is None and job['kind'] == 'fetch' and feeds.get(resolved_feed, {}).get('kind') == 'youtube' and youtube['next_at'] > now:
                continue
            if code == 'youtube-admission':
                continue
            code = code or 'work-' + job['kind']
            feed = resolved_feed or job['item'] or 'demo'
            if code in ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch') and feed in profile_feeds:
                continue
            if job['viewed'] and job['kind'] not in ('delete','feed-delete'):
                continue
            shared = code in ('processor-config', 'ai-permission') or code.startswith('source-')
            identity = 'job:' + code + ':' + feed + ':' + job['kind'] + ('' if shared else ':' + job['item'])
            if code.startswith('source-') and identity in source_owned:continue
            if code == 'processor-config':
                identity = 'processor:configuration'
            settings_action = code in ('processor-config', 'ai-permission')
            obj = dict(kind='feed' if job['kind'] in ('discover', 'read', 'feed-delete') else 'material', id=feed if job['kind'] in ('discover', 'read', 'feed-delete') else job['affected_item'])
            group_key: str | tuple[str,str] | tuple[str,str,str] = identity if code=='processor-config' else (identity,job['kind']) if shared else (identity,job['kind'],job['item'])
            if group_key not in groups:
                kind = job['kind']
                title = {'feed-delete':'Удаление фида не завершено', 'fetch':'Не удалось загрузить материал', 'download_media':'Не удалось загрузить вложение', 'discover':'Не удалось обновить источник',
                         'read':'Не удалось подтвердить чтение в Telegram', 'delete':'Удаление не завершено'}.get(kind, 'Не удалось обработать материал')
                if settings_action:
                    title = 'Требуется настройка обработчика'
                action = None
                groups[group_key] = dict(id=identity,code=code,section='action' if settings_action else 'object',title=title,
                    consequence='Сохранённое содержимое остаётся доступным.' if kind not in ('delete','feed-delete') else 'Око продолжает попытки удаления.',
                    next_step='Включите обработку, чтобы повторить попытку.' if job['status']=='blocked' else 'Повторите загрузку вручную.' if job['status'] in ('done','failed') else 'Проверьте доступность источника.' if job['status']=='unavailable' else 'Око повторит попытку автоматически.' if not settings_action else 'Откройте настройки обработчика.',
                    objects=[],action=action,operation='producer:'+kind,created_at=now,state='retrying' if job['status'] == 'running' or not job['error'] else 'current',finished_at=None,outcome=None,next_at=None)
                if not shared:
                    groups[group_key]['_status_job_target'] = job['item']
                if kind != 'delete' and job['error']:
                    groups[group_key]['reason'] = job['error']
                if code == 'source-reconnect':
                    groups[group_key].update(section='action', next_step='Проверьте привязку источника; доступ не изменяется автоматически.', action=None)
            legacy_feed = job['feed_id'] or job['item'] or 'demo'
            legacy_identity = 'job:' + code + ':' + legacy_feed + ':' + job['kind'] + ('' if shared else ':' + job['item'])
            if identity.startswith('job:') and legacy_identity != identity:
                groups[group_key].setdefault('_legacy_ids',set()).add(legacy_identity)
            if job['kind'] != 'delete' and obj not in groups[group_key]['objects']:
                groups[group_key]['objects'].append(obj)
        if youtube['paused'] and (scope_items is not None or not getattr(self.store,'youtube_admission_ready',False)):
            owners={job['affected_item'] for job in jobs if job['status'] in ('pending','running')
                and feeds.get(job['resolved_feed_id'] or job['feed_id'],{}).get('kind')=='youtube'
                and (job['kind']=='fetch' or job['resource_key']=='youtube:subtitles')}
            youtube_feeds=[feed_id for feed_id,feed in feeds.items() if feed.get('kind')=='youtube']
            if youtube_feeds and scope_items is None:
                rows=self.db.execute(f"""WITH affected(id) AS (
                    SELECT publication_id FROM feed_publications WHERE is_primary AND feed_id=ANY(%s)
                    UNION SELECT l.target_publication_id FROM feed_publications m JOIN publication_links l ON l.source_publication_id=m.publication_id WHERE m.is_primary AND m.feed_id=ANY(%s)
                    UNION SELECT item FROM jobs WHERE kind='fetch' AND feed_id=ANY(%s) AND status IN ('pending','running') AND problem_code IN ('','work-fetch')
                ) SELECT DISTINCT coalesce(root.id,j.item) AS affected FROM affected a JOIN jobs j ON j.kind='fetch' AND j.item=a.id
                LEFT JOIN publications items ON items.id=j.item LEFT JOIN publication_links l ON l.target_publication_id=items.id
                LEFT JOIN publications root ON root.id=l.source_publication_id AND root.viewed=0
                WHERE j.status IN ('pending','running') AND j.problem_code IN ('','work-fetch')
                AND coalesce({owner_expr('items')},{owner_expr('root')},j.feed_id)=ANY(%s)""",(youtube_feeds,youtube_feeds,youtube_feeds,youtube_feeds))
                owners.update(row['affected'] for row in rows)
            objects=[dict(kind='material',id=owner) for owner in sorted(owners)]
            groups['youtube:admission'] = dict(id='youtube:admission',code='youtube-admission',section='wait',title='YouTube ограничил запросы',
                consequence=f'Ожидают субтитров: {len(objects)}.',next_step='Око повторит получение после паузы.',
                objects=objects,action=None,created_at=now,state='retrying',finished_at=None,outcome=None,next_at=youtube['next_at'])
        return groups,feeds,youtube

    def candidates(self, now: float) -> list[dict[str, Any]]:
        current: list[dict[str, Any]] = []
        groups,feeds,youtube=self.incomplete_generic_groups(now)
        if self.db.execute("SELECT 1 FROM state WHERE key='experimental-incomplete-problems-v1'").fetchone():
            unknown=self.db.execute('SELECT 1 FROM publications WHERE incomplete_group_unknown AND incomplete_group_owner IS NOT NULL AND viewed=0 AND complete=1 AND NOT source_deleted LIMIT 1').fetchone()
            if unknown:raise ValueError('Unrecognized publication completeness value')
            conflict=self.db.execute('SELECT 1 FROM publications WHERE incomplete_group_conflict AND incomplete_group_owner IS NOT NULL AND viewed=0 AND complete=1 AND NOT source_deleted LIMIT 1').fetchone()
            if conflict:raise ValueError('Unrecognized incomplete reservation pointer')
            current.extend(groups.values())
            return current
        affected = {obj['id'] for group in groups.values() for obj in group['objects'] if obj['kind'] == 'material'}
        known_partial = {obj['id'] for record in self.db.execute("SELECT body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code' = 'material-incomplete'") for obj in json.loads(record['body'])['objects']}
        saved = self.db.execute("SELECT i . id , i . feed_id AS feed_id , i.content AS content_raw , f . status AS fetch_status , f . problem_code AS fetch_code , EXISTS ( SELECT 1 FROM media_files m WHERE m . publication_id = i . id AND EXISTS ( SELECT 1 FROM jobs j WHERE j . item = m . id AND j . kind = 'download_media' AND j . status IN ( 'pending' , 'running' ) AND j . error IS NULL AND j . problem_code IS NULL ) ) AS media_waiting , CASE WHEN i . platform = 'youtube' THEN EXISTS ( SELECT 1 FROM media_files m JOIN jobs j ON j . item = m . id AND j . kind = 'download_media' WHERE m . publication_id = i . id AND m . resource_key = 'youtube:subtitles' AND j . status = 'pending' AND j . problem_code IS NULL ) ELSE FALSE END AS caption_waiting FROM publications i LEFT JOIN jobs f ON f . item = i . id AND f . kind = 'fetch' WHERE i . viewed = 0 AND i . feed_id IS NOT NULL AND i . complete = 1 AND NOT i . source_deleted AND i.incomplete_class IS DISTINCT FROM 1".replace('i . feed_id', owner_expr('i'))).fetchall()
        for row in saved:
            from oko.storage.incomplete_index import content_class
            raw_content=json.loads(row['content_raw'])
            classification=content_class(row['content_raw'])
            if classification==1:
                continue
            if classification==2:
                raise ValueError('Unrecognized publication completeness value')
            if row['id'] in affected:
                continue
            raw_header=raw_content.get('extra',{}).get('_reader')
            header = self.header(json.dumps(raw_header) if raw_header is not None else None)
            if header.get('full', True) or (youtube['paused'] and feeds.get(row['feed_id'], {}).get('kind') == 'youtube'):
                continue
            if header.get('expected_incomplete'):
                continue
            if row['caption_waiting'] and self.store.youtube.ready_at()>now:
                continue
            cause = header.get('captions')
            active=row['fetch_status'] in ('pending','running')
            waiting=active and row['fetch_code'] is None
            media_waiting=bool(row['media_waiting'])
            if (waiting or media_waiting) and row['id'] not in known_partial:
                continue
            identity = 'incomplete:' + row['feed_id'] + ':' + (cause if cause in ('missing','error') else row['id'])
            if identity not in groups:
                groups[identity] = dict(id=identity,code='material-incomplete',section='object',title='Материал сохранён не полностью',
                    consequence='Доступная часть остаётся пригодной для чтения.',next_step='Повторите загрузку или откройте доступную часть.',
                    objects=[],action=None,created_at=now,state='current',finished_at=None,outcome=None,next_at=None)
                if header.get('reason'):
                    groups[identity]['reason'] = header['reason']
            groups[identity]['objects'].append(dict(kind='material',id=row['id']))
            if active:
                groups[identity]['state'] = 'retrying'
        current.extend(groups.values())
        return current

    def command_record(self, identity: str) -> CommandRecord | None:
        row = self.db.execute('SELECT * FROM reader_action_results WHERE id = %s', (identity,)).fetchone()
        if row is None:
            return None
        body = _action_receipt_context(row['body'],row['identity'])
        return CommandRecord(row['identity'], json.dumps(body['command_context']) if body and 'command_context' in body else None,
                             body.get('command_error') if body else None, 'unknown' if row['result']=='pending' and identity in self._startup_pending else row['result'], row['completed'] or row['started'])

    def command_error(self, identity: str, error_json: str) -> None:
        row = self.db.execute('SELECT identity,body FROM reader_action_results WHERE id = %s', (identity,)).fetchone()
        if row:
            body = _action_receipt_context(row['body'],row['identity'])
            if body is None:
                return
            body['command_error'] = error_json
            with self.db:
                self.db.execute('UPDATE reader_action_results SET body = %s WHERE id = %s', (json.dumps(body), identity))
                self.sync_action(row['identity'], self.store.wall_clock())

    def action_started(self, identity: str, context: dict[str, Any], now: float, *, restart: bool = False, expected_identity: str | None = None) -> None:
        if restart:
            context = dict(context, request_attempt=uuid.uuid4().hex)
        with self.db:
            prior = self.db.execute('SELECT identity,result FROM reader_action_results WHERE id = %s', (identity,)).fetchone()
            if restart and prior and prior['identity'] != (expected_identity or context['identity']):
                raise CommandContextMismatch('Контекст команды не совпадает')
            was_startup_pending = identity in self._startup_pending
            if restart:
                self._startup_pending.discard(identity)
            inserted = self.db.execute('INSERT INTO reader_action_results ( id , identity , body , started , result ) VALUES ( %s , %s , %s , %s , %s ) ON CONFLICT DO NOTHING',
                            (identity, context['identity'], json.dumps(context), now, 'pending')).rowcount
            if restart and was_startup_pending:
                self.db.after_rollback(lambda: self._startup_pending.add(identity))
            if inserted or restart and prior and prior['result'] != 'pending':
                status_changed(self.db)
            if restart:
                self.db.execute("UPDATE reader_action_results SET body = %s , started = %s , completed = NULL , result = 'pending' WHERE id = %s", (json.dumps(context), now, identity))
            self.sync_action(context['identity'],now)

    def action_finished(self, identity: str, succeeded: bool, now: float) -> None:
        with self.db:
            result = 'succeeded' if succeeded else 'failed'
            prior = self.db.execute('SELECT result,identity FROM reader_action_results WHERE id = %s', (identity,)).fetchone()
            self.db.execute("UPDATE reader_action_results SET completed = %s , result = %s WHERE id = %s AND result != 'abandoned'", (now, result, identity))
            if prior and prior['result'] not in ('abandoned', result):
                status_changed(self.db)
            if prior and prior['result'] != 'abandoned':
                self.sync_action(prior['identity'],now)

    def report_action(self, identity: str, context: dict[str, Any], result: str, now: float) -> str:
        existed = self.command_record(identity)
        self.action_started(identity, context, now)
        row = expect_row(self.db.execute('SELECT * FROM reader_action_results WHERE id = %s', (identity,)).fetchone())
        if row['identity'] != context['identity']:
            raise CommandContextMismatch('Контекст действия не совпадает')
        if existed and existed.result == 'pending' and existed.context_json:
            return 'pending'
        if row['result'] in ('pending', 'unknown'):
            with self.db:
                self.db.execute('UPDATE reader_action_results SET result = %s WHERE id = %s', (result, identity))
                if result != row['result']:
                    status_changed(self.db)
                self.sync_action(row['identity'],now)
            return result
        return str(row['result'])

    def abandon(self, identity: str, now: float, validate_context: Callable[[Any,str,Any],bool] | None = None) -> bool:
        if '\x00' in identity:
            return False
        row = self.db.execute("SELECT * FROM reader_problems WHERE id = %s AND origin = 'action' AND finished IS NULL", (identity,)).fetchone()
        if not row or not row['identity'].startswith('action:') or _action_problem_body(row['body']) is None:
            return False
        receipt = self.db.execute('SELECT * FROM reader_action_results WHERE identity = %s ORDER BY sequence DESC LIMIT 1', (row['identity'][7:],)).fetchone()
        context = _action_context(receipt['body']) if receipt else None
        if not receipt or receipt['result'] != 'failed' or context is None or context.get('discardable') is not True:
            return False
        if validate_context is not None and not validate_context(context.get('command_context'),receipt['identity'],context.get('command_error')):
            return False
        with self.db:
            self.db.execute("UPDATE reader_action_results SET result = 'abandoned' WHERE id = %s", (receipt['id'],))
            status_changed(self.db)
            self.db.execute("UPDATE reader_problems SET finished = %s , outcome = 'irrelevant' WHERE id = %s", (now, identity))
        return True

    def forget_materials(self, members: set[str], now: float) -> None:
        """Deletion wins over keeping an expanded/history row; retain only service facts."""
        records=problem_links.material_records(self.db, 'problem', members)
        from oko.storage.subtitle_problems import valid as valid_subtitle_reservation
        reserved_rows={row['identity']:valid_subtitle_reservation(row) for row in records if row['subtitle_due'] is not None}
        reserved_keys=list(reserved_rows)
        pointers={}
        for start in range(0,len(reserved_keys),400):
            pointers.update({row['key']:row['value'] for row in self.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',(reserved_keys[start:start+400],))})
        for row in records:
            reserved=reserved_rows.get(row['identity'])
            if reserved and pointers.get(row['identity'])==json.dumps(['oko-subtitle-v1',reserved['media_job_id'],reserved['subtitle_canonical']]):continue
            if row['identity']=='processor:configuration':
                continue
            if row['origin']=='action' or row['identity'].startswith('action:'):
                receipt = self.db.execute('SELECT identity,body FROM reader_action_results WHERE identity=%s ORDER BY sequence DESC LIMIT 1',(row['identity'][7:],)).fetchone() if row['identity'].startswith('action:') else None
                if _action_problem_body(row['body']) is None or not receipt or _action_receipt_context(receipt['body'],receipt['identity']) is None:
                    continue
            body = json.loads(row['body'])
            before = body['objects']
            body['objects'] = [obj for obj in before if not (obj['kind'] == 'material' and obj['id'] in members)]
            if len(before) == len(body['objects']):
                continue
            body['action'] = None if (body.get('action') or {}).get('id') in members else body.get('action')
            if not body['objects']:
                body['action'] = None
                self.db.execute("UPDATE reader_problems SET body = %s , finished = COALESCE ( finished , %s ) , outcome = 'irrelevant', incomplete_due=NULL, incomplete_future=NULL, incomplete_closes=false WHERE id = %s",
                                (json.dumps(body), now, row['id']))
            else:
                self.db.execute('UPDATE reader_problems SET body = %s WHERE id = %s', (json.dumps(body), row['id']))
            status_changed(self.db)
        for row in problem_links.material_records(self.db, 'action', members):
            if _action_receipt_context(row['body'],row['identity']) is None:
                continue
            body = json.loads(row['body'])
            before = body['objects']
            body['objects'] = [obj for obj in before if not (obj['kind'] == 'material' and obj['id'] in members)]
            if len(before) != len(body['objects']):
                if not body['objects']:
                    body['action'] = None
                # Typed receipts retain confirmed execution facts after content cleanup.
                result = row['result'] if body.get('command_context') else 'abandoned'
                self.db.execute('UPDATE reader_action_results SET body = %s , result = %s WHERE id = %s', (json.dumps(body), result, row['id']))
                if result != row['result']:
                    status_changed(self.db)

    def _rekey_regrouped_jobs(self, existing: dict[str,Any], candidates: list[dict[str,Any]], now: float) -> tuple[set[str],set[str]]:
        """Move exact legacy Job keys to corrected owner groups, preserving history."""
        target_ids = {candidate['id'] for candidate in candidates}
        old_targets: dict[str,set[str]] = {}
        for candidate in candidates:
            for legacy in candidate.get('_legacy_ids',set()):
                if legacy not in target_ids and legacy in existing:
                    old_targets.setdefault(legacy,set()).add(candidate['id'])
        old_for_target: dict[str,list[Any]] = {}
        for legacy,targets in old_targets.items():
            if targets:
                old_for_target.setdefault(min(targets),[]).append(existing[legacy])
        remapped: set[str] = set()
        merged: set[str] = set()
        for identity in sorted(old_for_target):
            matches = old_for_target[identity]
            matches.sort(key=lambda row:(row['created'],row['id']))
            if identity not in existing:
                chosen=matches.pop(0)
                self.db.execute('UPDATE reader_problems SET identity=%s WHERE id=%s AND finished IS NULL',(identity,chosen['id']))
                existing[identity]=chosen
                remapped.add(chosen['id'])
            for row in matches:
                self.db.execute("UPDATE reader_problems SET finished=%s,outcome='irrelevant' WHERE id=%s AND finished IS NULL",(now,row['id']))
                merged.add(row['id'])
        return remapped,merged

    def _finished_foreign_legacy_jobs(self, records: dict[str,dict[str,Any]]) -> set[str]:
        """Release only records with one proven finished owner, in bounded batches."""
        possible: list[tuple[str,str,str,str]] = []
        for identity,body in records.items():
            code = body.get('code')
            if not isinstance(code,str) or not identity.startswith('job:'+code+':'):
                continue
            tail = identity[len('job:'+code+':'):]
            per_record: list[tuple[str,str,str,str]] = []
            for kind in ('fetch','discover','download_media','filter','theses','read','delete','feed-delete'):
                delimiter = ':'+kind+':'
                start = 0
                while (position := tail.find(delimiter,start)) >= 0:
                    per_record.append((identity,kind,tail[position+len(delimiter):],tail[:position]))
                    if len(per_record)>16:
                        break
                    start = position+1
                if len(per_record)>16:
                    break
            if len(per_record)<=16:
                possible.extend(per_record)
        matches: dict[str,list[Any]] = {}
        for start in range(0,len(possible),400):
            batch=possible[start:start+400]
            rows=self.db.execute('''SELECT j.kind,j.item,j.feed_id,j.status,j.error,j.problem_code,p.identity,p.feed
                FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[]) AS p(identity,kind,item,feed)
                JOIN jobs j ON j.kind=p.kind AND j.item=p.item''',tuple([row[i] for row in batch] for i in range(4))).fetchall()
            for row in rows:
                matches.setdefault(row['identity'],[]).append(row)
        result: set[str] = set()
        for identity,rows in matches.items():
            if len(rows)!=1:
                continue
            job=rows[0]
            objects=records[identity].get('objects')
            if (job['kind'] not in ('delete','feed-delete','read')
                and job['feed'] == (job['feed_id'] or job['item'] or 'demo')
                and isinstance(objects,list) and any(isinstance(obj,dict) and obj.get('kind')=='material' and obj.get('id')==job['item'] for obj in objects)
                and job['status']=='done' and job['error'] is None and job['problem_code'] is None):
                result.add(identity)
        return result

    def reconcile(self, candidates: list[dict[str, Any]], now: float, feeds: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Preserve occurrence identity; completed records never become current on restart."""
        existing = {row['identity']: row for row in self.db.execute("""SELECT *,EXISTS (
            SELECT 1 FROM state WHERE key COLLATE "C">='fetch-null-protected:' AND key COLLATE "C"<'fetch-null-protected;' LIMIT 1
        ) AS has_fetch_null_protection FROM reader_problems WHERE finished IS NULL""")}
        protected_fetch_null: set[str]=set()
        saved_fetch_rows=[]
        for row in existing.values():
            if not row['has_fetch_null_protection']:continue
            if row['identity'].startswith('job:'):saved_fetch_rows.append(row)
        for offset in range(0,len(saved_fetch_rows),400):
            batch_fetch_rows=saved_fetch_rows[offset:offset+400]
            keys={'fetch-null-protected:'+hashlib.sha256(row['id'].encode()).hexdigest():(row['id'],row['identity']) for row in batch_fetch_rows}
            for marker in self.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',(list(keys),)):
                expected=keys[marker['key']]
                # An occupied key with corrupt or foreign contents is not proof
                # of ownership.  Keep the corresponding record untouched.
                try: value=json.loads(marker['value'])
                except (TypeError,ValueError): value=None
                if (isinstance(value,list) and len(value)==3 and value[0]=='oko-fetch-null-v2'
                    and value[1]==expected[0] and isinstance(value[2],str) and value[2]!=expected[1]):
                    continue  # The ID was reused for a different identity.
                protected_fetch_null.add(expected[0])
        admission_current=None
        if getattr(self.store,'youtube_admission_ready',False):
            from oko.storage.youtube_admission import current as current_admission
            admission_current=current_admission(self.store)
        from oko.storage.subtitle_problems import valid as valid_subtitle_reservation
        for future in self.db.execute('SELECT * FROM reader_problems WHERE subtitle_due IS NOT NULL AND subtitle_reserved_owned AND subtitle_due<=%s',(now,)):
            body=valid_subtitle_reservation(future)
            occupied=existing.get(body['subtitle_canonical']) if body else None
            if occupied is not None:protected_fetch_null.add(occupied['id'])
        owned_source_identities: set[str]=set()
        source_legacy: dict[str,str]={}
        for row in existing.values():
            try:body=json.loads(row['body'])
            except (TypeError,ValueError):continue
            if not isinstance(body,dict):continue
            if isinstance(body.get('source_group_identity'),str):owned_source_identities.add(body['source_group_identity'])
            elif isinstance(body.get('code'),str) and body['code'].startswith('source-'):source_legacy[row['identity']]=row['id']
        protected_source_legacy: set[str]=set()
        ordered_legacy=sorted(source_legacy)
        for start in range(0,len(ordered_legacy),400):
            legacy_id_batch=ordered_legacy[start:start+400]
            markers={'source-legacy-protected:'+hashlib.sha256(identity.encode()).hexdigest():(identity,source_legacy[identity]) for identity in legacy_id_batch}
            for marker in self.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',(list(markers),)):
                expected=markers[marker['key']]
                try:value=json.loads(marker['value'])
                except (TypeError,ValueError):value=None
                if not isinstance(value,list) or len(value)!=3 or value[0]!='oko-source-legacy-v1' or value[1]!=expected[0] or not isinstance(value[2],str):
                    protected_source_legacy.add(expected[1])
                elif value[2]==expected[1]:protected_source_legacy.add(value[2])
        managed_incomplete=bool(self.db.execute("SELECT 1 FROM state WHERE key='experimental-incomplete-problems-v1'").fetchone())
        uncertain_materials: set[str]=set()
        uncertain_refs: list[tuple[str,str,set[str]]]=[]
        invalid_fetch_objects: set[str]=set()
        for saved_fetch in existing.values():
            try:body=json.loads(saved_fetch['body'])
            except (TypeError,ValueError):continue
            if not isinstance(body,dict) or body.get('operation') not in (None,'producer:status') or ':fetch:' not in saved_fetch['identity']:continue
            code=body.get('code')
            if not isinstance(code,str) or not code or code.startswith('source-') or code in ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch','work-fetch'):continue
            objects=body.get('objects')
            if not isinstance(objects,list) or not objects or any(not isinstance(obj,dict) or obj.get('kind')!='material' or not isinstance(obj.get('id'),str) or not obj['id'] for obj in objects):
                invalid_fetch_objects.add(saved_fetch['id'])
            if isinstance(objects,list):
                object_ids={obj['id'] for obj in objects if isinstance(obj,dict) and obj.get('kind')=='material' and isinstance(obj.get('id'),str) and obj['id']}
                uncertain_materials.update(object_ids)
                prefix='job:'+code+':'
                if saved_fetch['identity'].startswith(prefix):
                    tail=saved_fetch['identity'][len(prefix):]
                    if tail.count(':fetch:')==1:
                        item=tail.split(':fetch:',1)[1]
                        if item and object_ids:uncertain_refs.append((saved_fetch['id'],item,object_ids))
        possible_active_fetch=self.active_fetch_roots(uncertain_materials)
        processing_legacy: list[tuple[Any,dict[str,Any],str]]=[]
        protected_processing: set[str]=set()
        for saved_processing in existing.values():
            try:body=json.loads(saved_processing['body'])
            except (TypeError,ValueError):continue
            if not isinstance(body,dict) or body.get('operation') not in (None,'producer:status','producer:filter','producer:theses'):continue
            if body.get('operation') in ('producer:filter','producer:theses') and isinstance(body.get('processing_job_id'),str):continue
            code=body.get('code')
            if not isinstance(code,str) or not code or code.startswith('source-') or code in ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'):continue
            identity=body.get('processing_job_identity',saved_processing['identity']);prefix='job:'+code+':'
            if not isinstance(identity,str) or not identity.startswith(prefix):
                if body.get('operation') in ('producer:filter','producer:theses'):protected_processing.add(saved_processing['id'])
                continue
            tail=identity[len(prefix):];kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
            found=[kind for kind in kinds if ':'+kind+':' in tail]
            if len(found)!=1 or found[0] not in ('filter','theses') or tail.count(':'+found[0]+':')!=1:
                if any(kind in ('filter','theses') for kind in found) or ':filter' in tail or ':theses' in tail or body.get('operation') in ('producer:filter','producer:theses'):protected_processing.add(saved_processing['id'])
                continue
            processing_legacy.append((saved_processing,body,tail.split(':'+found[0]+':',1)[1]))
        processing_materials={obj['id'] for _,body,_ in processing_legacy for obj in (body['objects'] if isinstance(body.get('objects'),list) else []) if isinstance(obj,dict) and obj.get('kind')=='material' and isinstance(obj.get('id'),str) and obj['id']}
        processing_active=self.active_processing_roots(processing_materials)
        processing_links=self.fetch_linked_pairs({item for _,_,item in processing_legacy if item},processing_materials)
        for saved_processing,body,item in processing_legacy:
            objects=body.get('objects')
            invalid=not isinstance(objects,list) or not objects or any(not isinstance(obj,dict) or obj.get('kind')!='material' or not isinstance(obj.get('id'),str) or not obj['id'] for obj in objects)
            checked=objects if isinstance(objects,list) else []
            if (body.get('operation') in ('producer:filter','producer:theses') or invalid or
                any(obj['id'] in processing_active or (obj['id']!=item and (obj['id'],item) not in processing_links) for obj in checked if isinstance(obj,dict) and isinstance(obj.get('id'),str))):
                protected_processing.add(saved_processing['id'])
        targets={item for _,item,objects in uncertain_refs if any(obj!=item for obj in objects)}
        roots={obj for _,item,objects in uncertain_refs for obj in objects if obj!=item}
        linked=self.fetch_linked_pairs(targets,roots)
        unverified_fetch={identity for identity,item,objects in uncertain_refs if any(obj!=item and (obj,item) not in linked for obj in objects)}
        media_legacy: list[tuple[Any,dict[str,Any],str]]=[]
        protected_media: set[str]=set()
        for saved_media in existing.values():
            try:media_body=json.loads(saved_media['body'])
            except (TypeError,ValueError):continue
            if not isinstance(media_body,dict) or media_body.get('operation') not in (None,'producer:status','producer:download_media'):continue
            if media_body.get('operation')=='producer:download_media' and isinstance(media_body.get('media_job_id'),str):continue
            code=media_body.get('code')
            if not isinstance(code,str) or not code or code.startswith('source-') or code in ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch','work-download_media'):continue
            identity=media_body.get('media_job_identity',saved_media['identity'])
            prefix='job:'+code+':'
            if not isinstance(identity,str) or not identity.startswith(prefix):
                if media_body.get('operation')=='producer:download_media':protected_media.add(saved_media['id'])
                continue
            tail=identity[len(prefix):]
            kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
            found=[kind for kind in kinds if ':'+kind+':' in tail]
            if found!=['download_media'] or tail.count(':download_media:')!=1:
                if 'download_media' in found or media_body.get('operation')=='producer:download_media':protected_media.add(saved_media['id'])
                continue
            media_legacy.append((saved_media,media_body,tail.split(':download_media:',1)[1]))
        file_ids=sorted({item for _,_,item in media_legacy if item})
        media_files={row['id']:row for row in self.db.execute('SELECT id,publication_id,resource_key FROM media_files WHERE id=ANY(%s)',(file_ids,))} if file_ids else {}
        media_materials={obj['id'] for _,body,_ in media_legacy for obj in (body['objects'] if isinstance(body.get('objects'),list) else []) if isinstance(obj,dict) and obj.get('kind')=='material' and isinstance(obj.get('id'),str) and obj['id']}
        media_active=self.active_media_roots(media_materials)
        media_targets={row['publication_id'] for row in media_files.values()}
        media_links=self.fetch_linked_pairs(media_targets,media_materials)
        for saved_media,media_body,item in media_legacy:
            file=media_files.get(item)
            if file and file['resource_key']=='youtube:subtitles':continue
            objects=media_body.get('objects')
            invalid=not isinstance(objects,list) or not objects or any(not isinstance(obj,dict) or obj.get('kind')!='material' or not isinstance(obj.get('id'),str) or not obj['id'] for obj in objects)
            owner=file['publication_id'] if file else item
            checked_objects=objects if isinstance(objects,list) else []
            if (media_body.get('operation')=='producer:download_media' or invalid or
                any(obj['id'] in media_active or (obj['id']!=owner and (obj['id'],owner) not in media_links) for obj in checked_objects if isinstance(obj,dict) and isinstance(obj.get('id'),str))):
                protected_media.add(saved_media['id'])
        isolated: dict[tuple[str,str,str | None],str] = {}
        for saved_identity,saved_row in sorted(existing.items()):
            if 'status_job_identity' not in saved_row['body']:
                continue
            saved_body = json.loads(saved_row['body'])
            if isinstance(saved_body,dict) and isinstance(saved_body.get('status_job_identity'),str) and isinstance(saved_body.get('operation'),str):
                target=saved_body.get('status_job_target')
                if isinstance(target,str) or target is None:
                    isolated.setdefault((saved_body['status_job_identity'],saved_body['operation'],target),saved_identity)
        separated = []
        candidate_ids = {candidate['id'] for candidate in candidates}
        reserved = set(existing) | candidate_ids
        claimed: dict[str,tuple[str,str | None]] = {}
        claimed_alternates: set[str] = set()
        for original in candidates:
            candidate = dict(original)
            isolated_aliases: list[str] = []
            target=candidate.get('_status_job_target')
            target=target if isinstance(target,str) else None
            if candidate.get('_legacy_ids'):
                safe_aliases = set()
                for alias in candidate['_legacy_ids']:
                    alias_operation = candidate.get('operation')
                    isolated_alias = (isolated.get((alias,alias_operation,target)) or isolated.get((alias,alias_operation,None))) if isinstance(alias_operation,str) else None
                    if isolated_alias and isolated_alias not in claimed_alternates:
                        safe_aliases.add(isolated_alias)
                        isolated_aliases.append(isolated_alias)
                        claimed_alternates.add(isolated_alias)
                    previous = existing.get(alias)
                    old_body = json.loads(previous['body']) if previous else None
                    if (isinstance(old_body,dict) and not isinstance(old_body.get('source_group_identity'),str) and old_body.get('operation')==alias_operation
                        and old_body.get('code')==candidate.get('code') and alias not in candidate_ids and alias not in claimed_alternates):
                        isolated_aliases.append(alias)
                        claimed_alternates.add(alias)
                    protected = previous is not None and previous['id'] in protected_fetch_null or (isinstance(old_body,dict) and old_body.get('code') in ('delete','feed-delete','work-delete','work-feed-delete')
                                 and (old_body.get('operation') in ('producer:delete','producer:feed-delete')
                                      or (old_body.get('operation') in (None,'producer:status') and (':delete:' in alias or ':feed-delete:' in alias))))
                    if isinstance(old_body,dict) and isinstance(old_body.get('source_group_identity'),str):protected=True
                    if not protected:
                        safe_aliases.add(alias)
                candidate['_legacy_ids'] = safe_aliases
            identity = candidate['id']
            saved = existing.get(identity)
            body = json.loads(saved['body']) if saved else None
            operation = candidate.get('operation')
            prior_alternate = isolated.get((identity,operation,target)) if isinstance(operation,str) else None
            if prior_alternate is None and isinstance(operation,str) and not (isinstance(body,dict) and body.get('operation')==operation and target is not None and (body.get('status_job_target')==target or body.get('status_job_target') is None and body.get('objects')==candidate.get('objects'))):
                prior_alternate = isolated.get((identity,operation,None))
            if prior_alternate in existing and existing[prior_alternate]['id'] in protected_fetch_null:prior_alternate=None
            if prior_alternate in claimed_alternates:
                prior_alternate = None
            if prior_alternate:
                claimed_alternates.add(prior_alternate)
                candidate['id'] = prior_alternate
                candidate['status_job_identity'] = identity
                if target is not None:
                    candidate['status_job_target'] = target
                candidate.pop('_legacy_ids',None)
            elif (isinstance(operation,str) and operation.startswith('producer:') and (
                (saved is not None and saved['id'] in protected_fetch_null)
                or (isinstance(body,dict) and isinstance(body.get('source_group_identity'),str) and isinstance(candidate.get('code'),str) and candidate['code'].startswith('source-'))
                or (isinstance(body,dict) and isinstance(body.get('operation'),str) and body['operation'].startswith('producer:') and body['operation'] not in ('producer:status',operation))
                or (identity in claimed and claimed[identity]!=(operation,target))
                or (isinstance(body,dict) and body.get('operation')==operation and isinstance(body.get('status_job_target'),str) and body['status_job_target']!=target)
                or (isinstance(body,dict) and body.get('code') in ('delete','feed-delete','work-delete','work-feed-delete')
                    and body.get('operation') in (None,'producer:status')
                    and operation not in ('producer:status','producer:delete','producer:feed-delete')
                    and (':delete:' in identity or ':feed-delete:' in identity)))):
                if isolated_aliases:
                    alternate=min(isolated_aliases)
                    claimed_alternates.add(alternate)
                else:
                    digest = hashlib.sha256((identity+'\0'+operation).encode()).hexdigest()
                    for _ in range(3):
                        alternate = 'job:status-isolated:'+digest+':'+new_read_problem_token()
                        if alternate not in reserved:
                            reserved.add(alternate)
                            break
                    else:
                        raise RuntimeError('Unable to allocate status problem identity')
                candidate['id'] = alternate
                candidate['status_job_identity'] = identity
                if target is not None:
                    candidate['status_job_target'] = target
                candidate.pop('_legacy_ids',None)
            if isinstance(operation,str):
                claimed.setdefault(identity,(operation,target))
            separated.append(candidate)
        candidates = separated
        by_identity = {candidate['id']: candidate for candidate in candidates}
        identities = set(by_identity)
        feeds = feeds if feeds is not None else {f['id']: f for f in self.store.subscriptions.feeds()}
        foreign_records: dict[str,dict[str,Any]] = {}
        for identity,row in existing.items():
            if identity in identities or not identity.startswith('job:') or (':delete:' not in identity and ':feed-delete:' not in identity):
                continue
            body=json.loads(row['body'])
            if isinstance(body,dict) and body.get('code') in ('delete','feed-delete','work-delete','work-feed-delete') and body.get('operation') in (None,'producer:status'):
                foreign_records[identity]=body
        finished_foreign=self._finished_foreign_legacy_jobs(foreign_records)
        with self.db:
            added: list[tuple[str,str,str,str,float]] = []
            remapped,merged = self._rekey_regrouped_jobs(existing,candidates,now)
            changed = bool(remapped or merged)
            for row in existing.values():
                if row['id'] in remapped or row['id'] in merged:
                    continue
                if row['identity'] not in identities and row['identity'] not in ('processor:codex-choice','processor:configuration') and not row['identity'].startswith(('action:', 'profile:')):
                    body = json.loads(row['body'])
                    if getattr(self.store,'youtube_admission_ready',False) and (row['identity']=='youtube:admission' or isinstance(body,dict) and body.get('youtube_admission_owner')=='youtube:admission') and (admission_current is None or row['id']!=admission_current['id']):continue
                    if isinstance(body,dict) and body.get('code')=='processor-config' and body.get('processor_group_identity')=='processor:configuration':
                        continue
                    if isinstance(body.get('source_group_identity'),str):continue
                    if row['identity'] in owned_source_identities and isinstance(body.get('code'),str) and body['code'].startswith('source-'):continue
                    if row['id'] in protected_source_legacy:continue
                    code=body.get('code')
                    if row['id'] in protected_fetch_null:continue
                    if getattr(self.store,'youtube_admission_ready',False) and code=='youtube-admission':
                        from oko.storage.youtube_admission import valid as valid_admission
                        if admission_current is not None and row['id']==admission_current['id'] and valid_admission(row):continue
                        if row['identity']=='youtube:admission' or body.get('youtube_admission_owner')=='youtube:admission':continue
                    if code=='material-incomplete' and managed_incomplete:
                        continue
                    eligible_fetch=(isinstance(code,str) and bool(code) and not code.startswith('source-') and code not in ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch','work-fetch'))
                    if body.get('operation')=='producer:fetch' and (eligible_fetch or isinstance(body.get('fetch_job_id'),str)):
                        continue
                    if row['id'] in protected_media or (body.get('operation')=='producer:download_media' and isinstance(body.get('media_job_id'),str)):
                        continue
                    if row['id'] in protected_processing or (body.get('operation') in ('producer:filter','producer:theses') and isinstance(body.get('processing_job_id'),str)):
                        continue
                    if eligible_fetch and body.get('operation') in (None,'producer:status') and row['identity'].startswith('job:'+code+':'):
                        tail=row['identity'][len('job:'+code+':'):]
                        kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
                        found=[kind for kind in kinds if ':'+kind+':' in tail]
                        if 'fetch' in found and (len(found)!=1 or tail.count(':fetch:')!=1 or not body.get('objects') or row['id'] in unverified_fetch or row['id'] in invalid_fetch_objects or any(isinstance(obj,dict) and obj.get('id') in possible_active_fetch for obj in body.get('objects',[]))):continue
                    if legacy_discover_classification(row['identity'],body) in ('discover','ambiguous'):
                        continue
                    if row['identity'].startswith('job:') and body.get('code') in ('delete','feed-delete','work-delete','work-feed-delete'):
                        operation=body.get('operation')
                        if operation in ('producer:delete','producer:feed-delete') or (operation in (None,'producer:status') and (':delete:' in row['identity'] or ':feed-delete:' in row['identity']) and row['identity'] not in finished_foreign):
                            continue
                    if row['identity'].startswith('job:') and body.get('code')!='processor-config':
                        from oko.storage.read_job_problems import classify_legacy
                        if classify_legacy(row['identity'],body) in ('read','ambiguous'):
                            continue
                    irrelevant = body['code'] == 'codex-choice' and self.store.codex_config() is None
                    irrelevant = irrelevant or any(obj['kind'] == 'material' and self.store.publications.source_deleted(obj['id']) for obj in body['objects'])
                    irrelevant = irrelevant or any(obj['kind'] == 'feed' and feeds.get(obj['id'], {}).get('connection', 'deleting') == 'deleting' for obj in body['objects'])
                    self.db.execute('UPDATE reader_problems SET finished = %s , outcome = %s WHERE id = %s', (now, 'irrelevant' if irrelevant else 'resolved', row['id']))
                    changed = True
            for candidate in candidates:
                if not candidate['id'].startswith('action:'):
                    candidate.setdefault('operation','producer:status')
                identity = candidate['id']
                body = {key: value for key, value in candidate.items() if key not in ('id', 'created_at', 'finished_at', 'outcome', '_legacy_ids', '_status_job_target')}
                # v3 carries resource identities; it never archives object titles.
                body['objects'] = [{key: value for key, value in obj.items() if key != 'label'} for obj in candidate['objects']]
                encoded = json.dumps(body)
                prior_problem = existing.get(identity)
                if prior_problem:
                    if prior_problem['body'] != encoded:
                        self.db.execute('UPDATE reader_problems SET body = %s WHERE id = %s', (encoded, prior_problem['id']))
                        changed = True
                else:
                    added.append((uuid.uuid4().hex, identity, 'action' if identity.startswith('action:') else 'background', encoded, now))
            if added:
                changed = True
            for start in range(0, len(added), 400):
                batch = added[start:start+400]
                self.db.execute('''INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome)
                    SELECT id,identity,origin,body,created,NULL,NULL FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::double precision[])
                    AS p(id,identity,origin,body,created)''', tuple([row[i] for row in batch] for i in range(5)))
            if changed:
                status_changed(self.db)
        current: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        rows = self.db.execute('SELECT * FROM reader_problems WHERE finished IS NULL OR finished > %s OR (subtitle_due IS NOT NULL AND subtitle_reserved_owned AND subtitle_due<=%s) ORDER BY created DESC', (now - 86400,now)).fetchall()
        reservation=self.processor_reservation()
        processor_current=self.processor_current()
        action_identities=sorted({row['identity'][7:] for row in rows if row['identity'].startswith('action:')})
        receipts: dict[str,Any] = {}
        for start in range(0,len(action_identities),400):
            for saved_receipt in self.db.execute('SELECT r.identity,r.id,r.body,r.result,r.started FROM unnest(%s::text[]) target(identity) CROSS JOIN LATERAL (SELECT identity,id,body,result,started FROM reader_action_results WHERE identity=target.identity ORDER BY sequence DESC LIMIT 1) r',(action_identities[start:start+400],)):
                receipts[saved_receipt['identity']]=saved_receipt
        from oko.storage.incomplete_group_draft import verified_reservation_rows
        incomplete_reservations=verified_reservation_rows(self.store,[row for row in rows if row['incomplete_due'] is not None])
        from oko.storage.subtitle_problems import verified as verified_subtitles
        subtitle_reservations=verified_subtitles(self.store,[row for row in rows if row['subtitle_due'] is not None],now)
        for row in rows:
            virtual_subtitle=subtitle_reservations.get(row['id'])
            if virtual_subtitle is not None and row['subtitle_due']>now:continue
            damaged_subtitle=(row['subtitle_due'] is not None and virtual_subtitle is None and row['subtitle_reserved_owned']
                and row['finished'] is not None and row['finished']<=now-86400)
            if row['subtitle_due'] is not None and virtual_subtitle is None and not damaged_subtitle and row['finished'] is not None and row['finished']<=now-86400:continue
            if row['id'] in incomplete_reservations:
                continue
            if reservation and row['id']==reservation['id'] and row['identity']=='processor:reservation:'+reservation['id']:
                continue
            current_receipt=receipts.get(row['identity'][7:]) if row['identity'].startswith('action:') else None
            bad_receipt=bool(row['finished'] is None and (current_receipt is None or current_receipt['result'] in ('failed','unknown','pending') and _action_receipt_context(current_receipt['body'],current_receipt['identity']) is None))
            is_action=row['origin']=='action' or row['identity'].startswith('action:')
            parsed=_action_problem_body(row['body']) if is_action else None
            bad_created=not _readable_timestamp(row['created'])
            bad_finished=(row['finished'] is not None and not _readable_timestamp(row['finished']))
            bad_outcome=(row['finished'] is None and row['outcome'] is not None or
                         row['finished'] is not None and row['outcome'] not in ('resolved','irrelevant'))
            generic=is_action and (parsed is None or bad_receipt or bad_created or bad_finished or bad_outcome)
            if generic:
                unknown=not current_receipt or current_receipt['result']!='failed' or bad_created or bad_finished or bad_outcome
                problem=_generic_action(unknown=unknown,request_id=current_receipt['id'] if current_receipt else None)
                if bad_created:
                    problem['consequence']='Исходные сведения сохранены; время создания неизвестно, показано время проверки.'
            else:
                problem=parsed if parsed is not None else json.loads(row['body'])
                if is_action and row['finished'] is None and current_receipt:
                    receipt_context = _action_receipt_context(current_receipt['body'],current_receipt['identity'])
                    if receipt_context and receipt_context.get('command_error') is not None:
                        latest = self.action_candidate(current_receipt)
                        if latest is not None:
                            problem=latest
            processor_legacy=(row['identity']=='processor:configuration' and (not isinstance(problem,dict) or problem.get('code') in (None,'processor-config')) or isinstance(problem,dict) and problem.get('code')=='processor-config' and problem.get('processor_group_identity')=='processor:configuration')
            if processor_legacy and (bad_created or bad_finished or bad_outcome or not self.valid_processor_problem(row['body'])):
                problem=dict(code='processor-config',section='action',title='Сохранённое состояние обработчика требует проверки',consequence='Исходная запись сохранена.',next_step='Проверьте настройки обработчика.',objects=[],action=None,operation='producer:processor-config-legacy',state='current',next_at=None)
            incomplete_legacy=managed_incomplete and (isinstance(problem,dict) and problem.get('code')=='material-incomplete' or not isinstance(problem,dict) and row['identity'].startswith('incomplete:'))
            if incomplete_legacy:
                from oko.storage.incomplete_group_draft import valid_incomplete_projection
                if bad_created or bad_finished or bad_outcome or not valid_incomplete_projection(problem):
                    problem=dict(code='material-incomplete',section='object',title='Сведения о неполном материале недоступны',
                        consequence='Исходная запись сохранена.',next_step='Проверьте журнал диагностики.',objects=[],action=None,
                        operation='producer:incomplete-legacy',state='current',next_at=None)
            youtube_legacy=(getattr(self.store,'youtube_admission_ready',False) and (row['identity']=='youtube:admission' and (not isinstance(problem,dict) or problem.get('code')=='youtube-admission') or isinstance(problem,dict) and problem.get('code')=='youtube-admission' and problem.get('youtube_admission_owner')=='youtube:admission'))
            if youtube_legacy:
                from oko.storage.youtube_admission import valid as valid_admission
                unowned_marker=isinstance(problem,dict) and problem.get('youtube_admission_owner')=='youtube:admission' and (admission_current is None or row['id']!=admission_current['id'])
                if bad_created or bad_finished or bad_outcome or unowned_marker or not valid_admission(row):
                    problem=dict(code='youtube-admission',section='wait',title='Сведения об ограничении YouTube недоступны',
                        consequence='Исходная запись сохранена.',next_step='Проверьте журнал диагностики.',objects=[],action=None,
                        operation='producer:youtube-admission-legacy',state='retrying',next_at=None)
            virtual_finished=None
            if admission_current is not None and row['id']==admission_current['id'] and row['finished'] is None and row['youtube_admission_due'] is not None and row['youtube_admission_due']<=now:
                from oko.storage.youtube_admission import valid as valid_admission,project as project_admission
                if valid_admission(row):
                    problem=project_admission(row,now)
                    virtual_finished=row['youtube_admission_due']
            if managed_incomplete and row['finished'] is None and row['incomplete_due'] is not None and row['incomplete_due']<=now:
                from oko.storage.incomplete_group_draft import current_schedule
                scheduled=current_schedule(row)
                if scheduled and row['body']==scheduled['before']:
                    if scheduled['mode']=='update':problem=dict(scheduled['future'])
                    else:
                        problem=json.loads(scheduled['before'])
                        virtual_finished=scheduled['deadline']
                else:
                    problem=dict(code='material-incomplete',section='object',title='Сведения о неполном материале недоступны',
                        consequence='Исходная запись сохранена.',next_step='Проверьте журнал диагностики.',objects=[],action=None,
                        operation='producer:incomplete-legacy',state='current',next_at=None)
            if virtual_subtitle is not None:
                problem=dict(virtual_subtitle)
                problem.pop('subtitle_reservation',None);problem.pop('subtitle_canonical',None)
            elif damaged_subtitle:
                problem=dict(code='work-download_media',section='object',title='Состояние субтитров требует проверки',
                    consequence='Исходная запись сохранена.',next_step='Проверьте журнал диагностики.',objects=[],action=None,
                    operation='producer:subtitle-legacy',state='current',next_at=None)
            if row['finished'] is None and processor_current and row['id']==processor_current['id']:
                projected=self.processor_configuration_candidate(now)
                if projected is not None:
                    problem=projected
            if row['finished'] is None:
                candidate = by_identity.get(row['identity'], {})
                if candidate.get('reason'):
                    problem['reason'] = candidate['reason']
            for obj in problem['objects']:
                obj.pop('label',None)
            visible_id = row['id']
            if not visible_id:
                visible_id = _legacy_problem_id(row['identity'])
            finished = None if (generic or processor_legacy or incomplete_legacy or youtube_legacy) and (bad_finished or bad_outcome) else row['finished']
            if virtual_subtitle is not None or damaged_subtitle:finished=None
            if virtual_finished is not None:finished=virtual_finished
            if virtual_finished is not None and virtual_finished<=now-86400:continue
            created = now if (generic or processor_legacy or incomplete_legacy or youtube_legacy) and bad_created else row['created']
            if virtual_subtitle is not None or damaged_subtitle:created=row['subtitle_due']
            problem.update(id=visible_id, created_at=created, finished_at=finished,
                           outcome=None if finished is None else 'resolved' if virtual_finished is not None else row['outcome'], state='finished' if finished is not None else problem['state'])
            (recent if finished is not None else current).append(problem)
        if managed_incomplete:
            from oko.storage.incomplete_group_draft import virtual_due_groups
            current.extend(virtual_due_groups(self.store,now))
        resumed=self.processor_resume_candidate(now)
        if resumed:
            identity,problem,created=resumed
            problem.update(id=identity,created_at=created,finished_at=None,outcome=None)
            current.append(problem)
        if managed_incomplete:
            current.sort(key=lambda problem:(-float(problem['created_at']),str(problem['id'])))
            recent.sort(key=lambda problem:(-float(problem['created_at']),str(problem['id'])))
        return dict(current=current, recent=recent, count=len(current), updated_at=now)

    @staticmethod
    def header(raw: str | None) -> dict[str,Any]:
        if not raw:
            return {}
        value=json.loads(raw)
        manifest=SourceManifest.model_validate_json(json.dumps(value['source_manifest'])) if value.get('source_manifest') else None
        value['expected_incomplete']=expected_incomplete(manifest)
        return {field:value[field] for field in ('title','full','captions','reason','expected_incomplete') if field in value}
