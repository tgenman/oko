"""Current reader obstacles; identity and recovery are independent of message text."""
from oko.storage.reading import saved_content
from oko.storage.database import expect_row, Row
import json
import hashlib
import math
import re
from datetime import datetime, timezone
from oko.storage.reading import reading_snapshot
from oko.storage import problem_links
from oko.storage.reader_status import changed as status_changed
from dataclasses import dataclass
import uuid
from typing import TYPE_CHECKING, Any, Callable, Literal

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






class Problems:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        self.db = store.db
        self._receipt_cleanup_cursor: tuple[float, int] | None = None
        self._history_replay_cursor: str | None = None
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
        self._recovering_actions = False

    def replay_history_batch(self, feeds: set[str]) -> tuple[bool, bool]:
        """Try at most one deferred-history feed in round-robin order."""
        if not feeds:
            return False, False
        from bisect import bisect_right
        ordered = sorted(feeds)
        previous = self._history_replay_cursor
        index = bisect_right(ordered, previous) if previous is not None else 0
        selected = ordered[index % len(ordered)]
        self._history_replay_cursor = selected
        self.db.after_rollback(lambda: setattr(self, '_history_replay_cursor', previous))
        return self.store.subscriptions.history.replay_deferred(selected)

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
                    if obj['kind'] == 'feed':
                        feed = self.db.execute('SELECT connection FROM feeds WHERE id=%s', (obj['id'],)).fetchone()
                        if not feed or feed['connection'] == 'deleting':
                            outcome = 'irrelevant'
        self.sync_candidate('action:'+identity, candidate, now, removed_outcome=outcome)





    def protect_fetch_null_collisions(self, job_ids: list[int]) -> None:
        for job_id in job_ids:
            cursor: str | None = None
            while True:
                query = """SELECT id,identity FROM reader_problems WHERE finished IS NULL
                    AND body::jsonb->>'operation'='producer:fetch' AND body::jsonb->>'code'='work-fetch'
                    AND body::jsonb->>'fetch_job_identity' IS NOT NULL
                    AND (body::jsonb->>'fetch_job_id') COLLATE "C"=%s"""
                params: tuple[Any,...] = (str(job_id),)
                if cursor is not None:
                    query += ' AND id COLLATE "C">%s'
                    params += (cursor,)
                rows = self.db.execute(query+' ORDER BY id COLLATE "C" LIMIT 400',params).fetchall()
                if not rows:
                    break
                for row in rows:
                    self.protect_foreign_identity_fetch(row)
                cursor = rows[-1]['id']
                self.store.fault('fetch_null_protection_batch')

    def recover_jobs_without_reason(self, kind: str) -> None:
        """Recover unsuccessful Jobs whose optional reason fields are both empty."""
        after=0
        while True:
            rows=self.db.execute("SELECT id FROM jobs WHERE kind=%s AND status IN ('failed','unavailable') AND reason_code IS NULL AND reason_message IS NULL AND id>%s ORDER BY id LIMIT 100",(kind,after)).fetchall()
            if not rows:
                return
            with self.db:
                from oko.storage import memberships
                memberships.lock_graph(self.db)
                for row in rows:
                    if kind=='fetch':self.sync_fetch(row['id'],[],self.store.wall_clock())
                    elif kind=='download_media':self.sync_media_problem(row['id'],[],self.store.wall_clock())
                    else:self.sync_processing_problem(row['id'],[],self.store.wall_clock())
            after=rows[-1]['id']


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
        if operation != 'producer:fetch':
            return False
        identity=record['identity']
        prefix='job:'+candidate['code']+':'
        if not identity.startswith(prefix):return False
        tail=identity[len(prefix):]
        kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
        found=[kind for kind in kinds if ':'+kind+':' in tail]
        if found!=['fetch'] or tail.count(':fetch:')!=1:return False
        return True


    def protect_foreign_identity_fetch(self, row: Any) -> None:
        """Protect an occupied foreign occurrence without changing its contents."""
        key='fetch-null-protected:'+hashlib.sha256(row['id'].encode()).hexdigest()
        value=json.dumps(['oko-fetch-null-v2',row['id'],row['identity']])
        self.db.execute('INSERT INTO state(key,value) VALUES (%s,%s) ON CONFLICT DO NOTHING',(key,value))
        saved=self.db.execute('SELECT value FROM state WHERE key=%s',(key,)).fetchone()
        if not saved or saved['value']!=value:raise RuntimeError('Conflicting fetch protection marker')

    def sync_fetch(self, job_id: int, previous: list[dict[str,Any]], now: float, *, allow_rekey: bool = True) -> None:
        previous_youtube=[dict(candidate) for candidate in previous if candidate.get('fetch_youtube_event')]
        if getattr(self.store,'youtube_admission_ready',False):
            from oko.storage import memberships
            from oko.storage.youtube_admission_jobs import capture_fetch_flag_changes
            memberships.lock_graph(self.db)
            capture_fetch_flag_changes(self.store,[job_id])
        from oko.storage.fetch_job_problems import managed_candidates
        candidates=managed_candidates(self.store,job_id,now)
        desired_youtube=[dict(candidate) for candidate in candidates if candidate.get('fetch_youtube_event')]
        if previous_youtube or desired_youtube:
            self.db.execute("UPDATE jobs SET fetch_youtube_sensitive=(%s=1) WHERE id=%s AND kind='fetch' AND fetch_youtube_sensitive IS DISTINCT FROM (%s=1)",(bool(desired_youtube),job_id,bool(desired_youtube)))
        self.db.execute('UPDATE jobs SET fetch_null_owned=(%s=1) WHERE id=%s AND reason_code IS NULL AND reason_message IS NOT NULL AND fetch_null_owned IS DISTINCT FROM (%s=1)',(bool(candidates),job_id,bool(candidates)))
        if previous and not candidates:
            from oko.storage.fetch_job_problems import candidates as all_candidates
            handoff=all_candidates(self.store,job_id,now)
            job=self.db.execute("SELECT item FROM jobs WHERE id=%s",(job_id,)).fetchone()
            candidates=[dict({k:v for k,v in candidate.items() if k!='_code_present'},fetch_job_id=str(job_id),_handoff_to_get=True,_handoff_target=job['item'])
                        for candidate in handoff if job and not candidate['_code_present'] and candidate['code']=='work-fetch']
        keys={candidate['id'] for candidate in previous+candidates}
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
                self.protect_fetch_null_collisions([job_id])
                self.db.execute('UPDATE jobs SET fetch_null_owned=false,fetch_null_ambiguous=true,identity_event_owned=false,fetch_youtube_sensitive=false WHERE id=%s',(job_id,))
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
                if occupied and (candidate['code'] in ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch') or candidate.get('fetch_youtube_event')):self.protect_foreign_identity_fetch(occupied)
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
        removed={row['id'] for row in self.db.execute("SELECT item AS id FROM jobs WHERE kind='delete' AND item=ANY(%s)",(materials,))} if materials else set()
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
        self.db.execute("UPDATE jobs SET identity_event_owned=(COALESCE(reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'),false) AND item IS NOT NULL AND item<>'' AND NOT fetch_null_ambiguous) WHERE id=%s AND identity_event_owned IS DISTINCT FROM (COALESCE(reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'),false) AND item IS NOT NULL AND item<>'' AND NOT fetch_null_ambiguous)",(job_id,))
        if previous_youtube or desired_youtube:
            from oko.storage.youtube_fetch_problems import transfer_reservation,cancel_reservations,sync_timer
            transferred=transfer_reservation(self.store,job_id,previous_youtube,desired_youtube,{row[0] for row in added}) if previous_youtube and desired_youtube else set()
            current_ids={candidate['id'] for candidate in desired_youtube}
            removed_youtube=[candidate for candidate in previous_youtube if candidate['id'] not in current_ids and candidate['id'] not in transferred]
            if removed_youtube:cancel_reservations(self.store,job_id,removed_youtube)
            if desired_youtube:sync_timer(self.store,now,[job_id],new_ids={row[0] for row in added},previous=previous_youtube)







    def media_record_matches(self, record: Any, candidate: dict[str, Any]) -> bool:
        try:body=json.loads(record['body'])
        except (TypeError,ValueError):return False
        if not isinstance(body,dict) or body.get('code')!=candidate['code']:
            return False
        if candidate.get('subtitle_event') or candidate.get('media_null_event') or candidate.get('media_profile_event'):
            allowed_fields={'code','section','title','consequence','next_step','objects','action','operation','state','next_at','reason',
                'media_owner','media_file_id','media_orphan','media_job_id','media_job_identity','media_implicit_code','media_null_event','media_profile_event',
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
        if operation != 'producer:download_media':
            return False
        identity=record['identity']
        prefix='job:'+candidate['code']+':'
        if not identity.startswith(prefix):return False
        tail=identity[len(prefix):]
        kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
        found=[kind for kind in kinds if ':'+kind+':' in tail]
        if found!=['download_media'] or tail.count(':download_media:')!=1:return False
        return True


    def sync_media_problem(self, job_id: int, previous: list[dict[str,Any]], now: float, *, successful: bool = False, allow_rekey: bool = True) -> None:
        from oko.storage.media_job_problems import managed_candidates
        previous_subtitle=[dict(candidate) for candidate in previous if candidate.get('subtitle_event')]
        candidates=[] if successful else managed_candidates(self.store,job_id,now)
        has_null=any(candidate.get('media_null_event') for candidate in candidates)
        self.db.execute("UPDATE jobs SET media_null_owned=(%s=1) WHERE id=%s AND kind='download_media' AND media_null_owned IS DISTINCT FROM (%s=1)",(has_null,job_id,has_null))
        profile_row=self.db.execute("""SELECT j.id FROM jobs j LEFT JOIN media_files m ON m.id=j.item
            WHERE j.id=%s AND j.kind='download_media' AND j.item IS NOT NULL AND j.item<>''
              AND j.status IN ('pending','running','failed','unavailable','blocked')
              AND j.reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')
              AND m.resource_key IS DISTINCT FROM 'youtube:subtitles'""",(job_id,)).fetchone() if not successful else None
        profile_owned=bool(profile_row)
        self.db.execute("UPDATE jobs SET identity_event_owned=(%s=1) WHERE id=%s AND kind='download_media' AND identity_event_owned IS DISTINCT FROM (%s=1)",(profile_owned,job_id,profile_owned))
        current_subtitle_ids={candidate['id'] for candidate in candidates if candidate.get('subtitle_event')}
        desired_subtitle=[dict(candidate) for candidate in candidates if candidate.get('subtitle_event')]
        profile_subtitle=self.db.execute("SELECT 1 FROM jobs j JOIN media_files m ON m.id=j.item WHERE j.id=%s AND j.kind='download_media' AND m.resource_key='youtube:subtitles' AND j.status IN ('pending','running','failed','unavailable','blocked') AND j.reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')",(job_id,)).fetchone() is not None
        owned_subtitle=bool(candidates) or profile_subtitle
        ownership_changed=self.db.execute("UPDATE jobs SET subtitle_event_owned=(%s=1) WHERE id=%s AND kind='download_media' AND EXISTS (SELECT 1 FROM media_files WHERE id=jobs.item AND resource_key='youtube:subtitles') AND subtitle_event_owned IS DISTINCT FROM (%s=1)",(owned_subtitle,job_id,owned_subtitle)).rowcount
        if previous_subtitle and not desired_subtitle and not profile_subtitle:
            ownership_changed+=self.db.execute("UPDATE jobs SET subtitle_event_owned=false WHERE id=%s AND kind='download_media' AND subtitle_event_owned",(job_id,)).rowcount
        if ownership_changed:status_changed(self.db)
        if previous and not candidates and not successful:
            subtitle=self.db.execute("SELECT 1 FROM jobs j JOIN media_files m ON m.id=j.item WHERE j.id=%s AND j.kind='download_media' AND m.resource_key='youtube:subtitles'",(job_id,)).fetchone()
            if not subtitle or not self.store.youtube.status(now)['paused']:
                from oko.storage.media_job_problems import candidates_many
                handoff=candidates_many(self.store,[job_id],now,include_subtitles=bool(subtitle)).get(job_id,[])
                candidates=[dict({k:v for k,v in candidate.items() if k not in ('_code_present','_subtitle')},media_job_id=str(job_id),_handoff_to_get=True)
                    for candidate in handoff if (subtitle or not candidate['_code_present']) and not (subtitle and not candidate['_code_present'] and self.store.youtube.status(now)['next_at']>now) and candidate['code'] not in ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch') and not candidate['code'].startswith('source-')]
        keys={candidate['id'] for candidate in previous+candidates}
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
        protected_ids: set[str]=set()
        if any(candidate.get('media_null_event') or candidate.get('media_profile_event') for candidate in previous+candidates):
            protection_candidates={row['id'] for row in by_identity.values()}
            protection_candidates.update(row['id'] for group in isolated.values() for row in group)
            ordered_protection=sorted(protection_candidates)
            for start in range(0,len(ordered_protection),400):
                marker_ids={'fetch-null-protected:'+hashlib.sha256(identity.encode()).hexdigest():identity for identity in ordered_protection[start:start+400]}
                for row in self.db.execute('SELECT key FROM state WHERE key=ANY(%s)',(list(marker_ids),)):
                    protected_ids.add(marker_ids[row['key']])
        # Only a unique, unprotected previous snapshot proves that a saved
        # occurrence belongs to this Job when its set of roots changes.
        trusted_previous: dict[str,str]={}
        for old_candidate in previous:
            if not (old_candidate.get('media_profile_event') or old_candidate.get('media_null_event')):continue
            possible=([by_identity[old_candidate['id']]] if old_candidate['id'] in by_identity else [])+isolated.get(old_candidate['id'],[])
            matches={row['id']:row for row in possible if row['id'] not in protected_ids and self.media_record_matches(row,old_candidate)}
            if len(matches)==1:trusted_previous[old_candidate['id']]=next(iter(matches))
        def proven(saved: Any, candidate: dict[str,Any]) -> bool:
            return bool(any(candidate is part for part in candidates) and (candidate.get('media_profile_event') or candidate.get('media_null_event')) and trusted_previous.get(candidate['id'])==saved['id'])
        ambiguous_null: dict[str,Any]={}
        for candidate in candidates:
            if not (candidate.get('media_null_event') or candidate.get('media_profile_event')):continue
            possible=([by_identity[candidate['id']]] if candidate['id'] in by_identity else [])+isolated.get(candidate['id'],[])
            matches={row['id']:row for row in possible if self.media_record_matches(row,candidate) or proven(row,candidate)}
            if len(matches)>1:ambiguous_null.update(matches)
        if ambiguous_null:
            from oko.storage.subtitle_problems import protect_rows
            protect_rows(self.store,list(ambiguous_null.values()))
            self.db.execute('UPDATE jobs SET media_null_owned=false,identity_event_owned=false WHERE id=%s',(job_id,))
            return
        subtitle_foreign: dict[str,Any]={}
        for candidate in previous+candidates:
            is_previous=any(candidate is part for part in previous)
            if is_previous and not (candidate.get('media_null_event') or candidate.get('media_profile_event')):continue
            if not (candidate.get('subtitle_event') or candidate.get('media_null_event') or candidate.get('media_profile_event')):continue
            for saved in ([by_identity[candidate['id']]] if candidate['id'] in by_identity else [])+isolated.get(candidate['id'],[]):
                if not self.media_record_matches(saved,candidate) and not proven(saved,candidate):subtitle_foreign[saved['id']]=saved
        if subtitle_foreign:
            from oko.storage.subtitle_problems import protect_rows
            protect_rows(self.store,list(subtitle_foreign.values()))
            protected_ids.update(subtitle_foreign)
        def record(candidate: dict[str,Any]) -> Any:
            def matches(saved: Any) -> bool:
                if saved['id'] in protected_ids:return False
                return self.media_record_matches(saved,candidate) or proven(saved,candidate)
            for saved in isolated.get(candidate['id'],[]):
                if matches(saved):return saved
            saved=by_identity.get(candidate['id'])
            return saved if saved and matches(saved) else None
        for candidate in previous:
            if candidate.get('media_null_event') or candidate.get('media_profile_event'):
                possible=([by_identity[candidate['id']]] if candidate['id'] in by_identity else [])+isolated.get(candidate['id'],[])
                matches={row['id']:row for row in possible if self.media_record_matches(row,candidate)}
                if len(matches)>1:
                    from oko.storage.subtitle_problems import protect_rows
                    protect_rows(self.store,list(matches.values()))
                    protected_ids.update(matches)
                    candidate['id']=''
                    continue
            matched=record(candidate)
            candidate['id']=matched['identity'] if matched else ''
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
        if current and old_ids and allow_rekey:
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
        removed={row['id'] for row in self.db.execute("SELECT item AS id FROM jobs WHERE kind='delete' AND item=ANY(%s)",(materials,))} if materials else set()
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






    def processing_record_matches(self, record: Any, candidate: dict[str, Any]) -> bool:
        try:body=json.loads(record['body'])
        except (TypeError,ValueError):return False
        if not isinstance(body,dict) or body.get('code')!=candidate['code']:
            return False
        if (candidate.get('filter_profile_event') or candidate.get('theses_profile_event')):
            allowed_fields={'code','section','title','consequence','next_step','objects','action','operation','state','next_at','reason','processing_kind','processing_owner','processing_job_id','processing_job_identity','status_job_identity','status_job_target',candidate['processing_kind']+'_profile_event'}
            if set(body)-allowed_fields or body.get('action') is not None:return False
        operation=body.get('operation')
        objects=body.get('objects')
        if not isinstance(objects,list) or not objects or any(not isinstance(obj,dict) or obj.get('kind')!='material' or not isinstance(obj.get('id'),str) for obj in objects):return False
        if (candidate.get('filter_profile_event') or candidate.get('theses_profile_event')):
            if body.get('processing_kind',candidate['processing_kind'])!=candidate['processing_kind'] or any(set(obj)!={'kind','id'} for obj in objects):return False
            if len({obj['id'] for obj in objects})!=len(objects):return False
        allowed={(obj.get('kind'),obj.get('id')) for obj in candidate['objects'] if isinstance(obj,dict)}
        if not all((obj.get('kind'),obj.get('id')) in allowed for obj in objects):return False
        status_identity=body.get('status_job_identity')
        if (candidate.get('filter_profile_event') or candidate.get('theses_profile_event')) and status_identity is not None:
            prefix='job:'+candidate['code']+':'
            tail=candidate['id'][len(prefix):] if candidate['id'].startswith(prefix) else ''
            owner=body.get('processing_job_id')
            kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
            found=[kind for kind in kinds if ':'+kind+':' in tail]
            return bool(operation == candidate['operation'] and (owner is None or owner==candidate['processing_job_id']) and body.get('processing_kind',candidate['processing_kind'])==candidate['processing_kind'] and body.get('processing_job_identity',candidate['id'])==candidate['id'] and status_identity==candidate['id'] and found==[candidate['processing_kind']] and tail.count(':'+candidate['processing_kind']+':')==1 and body.get('status_job_target')==tail.split(':'+candidate['processing_kind']+':',1)[1])
        marker=body.get('processing_job_id')
        if marker is not None:
            return bool(operation==candidate['operation'] and marker==candidate['processing_job_id'] and body.get('processing_job_identity',candidate['id'])==candidate['id'])
        if operation != candidate['operation']:
            return False
        identity=record['identity']
        prefix='job:'+candidate['code']+':'
        if not identity.startswith(prefix):return False
        tail=identity[len(prefix):]
        kinds=('fetch','discover','download_media','filter','theses','read','delete','feed-delete')
        found=[kind for kind in kinds if ':'+kind+':' in tail]
        if found!=[candidate['processing_kind']] or tail.count(':'+candidate['processing_kind']+':')!=1:return False
        return True


    def saved_job_candidates(self, job_ids: list[int], kind: Literal['media','processing']) -> dict[int,list[dict[str,Any]]]:
        if not job_ids:return {}
        if kind=='media':
            query="SELECT identity,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:download_media' AND body::jsonb->>'media_job_id'=ANY(%s)"
        else:
            query="SELECT identity,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation' IN ('producer:filter','producer:theses') AND body::jsonb->>'processing_job_id'=ANY(%s)"
        rows=self.db.execute(query,([str(job) for job in job_ids],)).fetchall()
        groups: dict[int,list[dict[str,Any]]]={}
        requested={str(job):job for job in job_ids}
        for row in rows:
            body=json.loads(row['body'])
            marker=body.get(kind+'_job_id') if isinstance(body,dict) else None
            if not isinstance(marker,str) or marker not in requested or not isinstance(body.get('objects'),list):continue
            body['id']=body.get(kind+'_job_identity',row['identity'])
            groups.setdefault(requested[marker],[]).append(body)
        return groups

    def sync_processing_problems(self, previous_by_job: dict[int,list[dict[str,Any]]], now: float) -> None:
        """Batch unchanged-owner single-occurrence transitions; isolate complex groups."""
        from oko.storage.processing_job_problems import managed_candidates_many
        ids=sorted(previous_by_job)
        for start in range(0,len(ids),400):
            batch=ids[start:start+400]
            current=managed_candidates_many(self.store,batch,now)
            profile_ids={row['id'] for row in self.db.execute("SELECT id FROM jobs WHERE id=ANY(%s) AND kind IN ('filter','theses') AND reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')",(batch,))}
            profile_ids.update(job for job in batch if any((part.get('filter_profile_event') or part.get('theses_profile_event')) for part in previous_by_job[job]))
            for job in sorted(profile_ids):self.sync_processing_problem(job,previous_by_job[job],now)
            batch=[job for job in batch if job not in profile_ids]
            if not batch:continue
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
                desired={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome')}
                if record['identity']!=canonical:desired['processing_job_identity']=canonical
                encoded=json.dumps(desired)
                if record['body']!=encoded:updates.append((record['id'],encoded))
            if updates:
                self.db.execute('UPDATE reader_problems p SET body=source.body FROM unnest(%s::text[],%s::text[]) AS source(id,body) WHERE p.id=source.id AND p.finished IS NULL',([r[0] for r in updates],[r[1] for r in updates]))
                status_changed(self.db)
            owners=sorted({candidate['processing_owner'] for _,candidate in closing if candidate.get('processing_owner')})
            materials=sorted({obj['id'] for _,candidate in closing for obj in candidate['objects'] if obj.get('kind')=='material'})
            alive={row['id'] for row in self.db.execute("SELECT id FROM feeds WHERE id=ANY(%s) AND connection!='deleting'",(owners,))} if owners else set()
            removed={row['id'] for row in self.db.execute("SELECT item AS id FROM jobs WHERE kind='delete' AND item=ANY(%s)",(materials,))} if materials else set()
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

    def sync_processing_problem(self, job_id: int, previous: list[dict[str,Any]], now: float, *, allow_rekey: bool = True) -> None:
        from oko.storage.processing_job_problems import managed_candidates
        candidates=managed_candidates(self.store,job_id,now)
        profile=self.db.execute("SELECT 1 FROM jobs WHERE id=%s AND kind IN ('filter','theses') AND item IS NOT NULL AND item<>'' AND status IN ('pending','running','failed','unavailable','blocked') AND reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')",(job_id,)).fetchone()
        self.db.execute("UPDATE jobs SET identity_event_owned=(%s=1) WHERE id=%s AND kind IN ('filter','theses') AND identity_event_owned IS DISTINCT FROM (%s=1)",(bool(profile),job_id,bool(profile)))
        keys={candidate['id'] for candidate in previous+candidates}
        rows=self.db.execute('SELECT * FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(sorted(keys),)).fetchall() if keys else []
        by_identity={row['identity']:row for row in rows}
        isolated: dict[str,list[Any]]={}
        if keys:
            for row in self.db.execute("SELECT *,body::jsonb->>'processing_job_identity' AS canonical FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation' IN ('producer:filter','producer:theses') AND body::jsonb->>'processing_job_identity'=ANY(%s)",(sorted(keys),)):
                isolated.setdefault(row['canonical'],[]).append(row)
            if any((candidate.get('filter_profile_event') or candidate.get('theses_profile_event')) for candidate in previous+candidates):
                for row in self.db.execute("SELECT *,body::jsonb->>'status_job_identity' AS canonical FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation' IN ('producer:filter','producer:theses','producer:status') AND body::jsonb->>'status_job_identity'=ANY(%s)",(sorted(keys),)):
                    isolated.setdefault(row['canonical'],[]).append(row)
        profile_related=any((candidate.get('filter_profile_event') or candidate.get('theses_profile_event')) for candidate in previous+candidates)
        protected: set[str]=set()
        if profile_related:
            possible={row['id'] for row in by_identity.values()}
            possible.update(row['id'] for group in isolated.values() for row in group)
            marker_ids={'fetch-null-protected:'+hashlib.sha256(identity.encode()).hexdigest():identity for identity in possible}
            if marker_ids:protected={marker_ids[row['key']] for row in self.db.execute('SELECT key FROM state WHERE key=ANY(%s)',(list(marker_ids),))}
        trusted_previous: dict[str,str]={}
        for old_candidate in previous:
            if not (old_candidate.get('filter_profile_event') or old_candidate.get('theses_profile_event')):continue
            possible_old=([by_identity[old_candidate['id']]] if old_candidate['id'] in by_identity else [])+isolated.get(old_candidate['id'],[])
            matches_old={row['id']:row for row in possible_old if row['id'] not in protected and self.processing_record_matches(row,old_candidate)}
            if len(matches_old)==1:trusted_previous[old_candidate['id']]=next(iter(matches_old))
        def record(candidate: dict[str,Any]) -> Any:
            def matches(saved: Any) -> bool:
                if saved['id'] in protected:return False
                if self.processing_record_matches(saved,candidate):return True
                return bool(any(candidate is part for part in candidates) and (candidate.get('filter_profile_event') or candidate.get('theses_profile_event')) and trusted_previous.get(candidate['id'])==saved['id'])
            for saved in isolated.get(candidate['id'],[]):
                if matches(saved):return saved
            saved=by_identity.get(candidate['id'])
            return saved if saved and matches(saved) else None
        for candidate in previous+candidates:
            if not (candidate.get('filter_profile_event') or candidate.get('theses_profile_event')):continue
            possible_rows=([by_identity[candidate['id']]] if candidate['id'] in by_identity else [])+isolated.get(candidate['id'],[])
            matches={row['id']:row for row in possible_rows if self.processing_record_matches(row,candidate)}
            if len(matches)>1:
                from oko.storage.subtitle_problems import protect_rows
                protect_rows(self.store,list(matches.values()))
                protected.update(matches)
                if any(candidate is current_part for current_part in candidates):
                    self.db.execute('UPDATE jobs SET identity_event_owned=false WHERE id=%s',(job_id,))
                    return
                candidate['id']=''
                continue
            is_current=any(candidate is current_part for current_part in candidates)
            unknown={row['id']:row for row in possible_rows if not self.processing_record_matches(row,candidate) and not (is_current and trusted_previous.get(candidate['id'])==row['id'])}
            if unknown:
                from oko.storage.subtitle_problems import protect_rows
                protect_rows(self.store,list(unknown.values()))
                protected.update(unknown)
        for candidate in previous:
            matched=record(candidate)
            candidate['id']=matched['identity'] if matched else ''
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
        if current and old_ids and allow_rekey:
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
                if own and (current[target].get('filter_profile_event') or current[target].get('theses_profile_event')):
                    close_ids.extend(row['id'] for row in prior_rows)
                    continue
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
        removed={row['id'] for row in self.db.execute("SELECT item AS id FROM jobs WHERE kind='delete' AND item=ANY(%s)",(materials,))} if materials else set()
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


    def discover_profile_suppresses(self, feed: str, code: str) -> bool:
        if code not in ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'):
            return False
        binding=self.db.execute('SELECT indexed_role FROM telegram_bindings WHERE feed=%s',(feed,)).fetchone()
        profile_identity='profile:'+(binding['indexed_role'] if binding and binding['indexed_role'] else feed)
        profile=self.db.execute("SELECT body FROM reader_problems WHERE identity=%s AND finished IS NULL",(profile_identity,)).fetchone()
        return bool(profile and any(obj.get('kind')=='feed' and obj.get('id')==feed for obj in json.loads(profile['body'])['objects']))





    def sync_discover(self, job_id: int, now: float, *, previous_codes: tuple[str,...] = ()) -> None:
        changed = self.db.execute("SELECT * FROM jobs WHERE id=%s AND kind='discover'", (job_id,)).fetchone()
        if not changed:
            return
        feed = changed['item'] or ''
        if not feed:
            return
        old = self.db.execute("SELECT identity,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:discover' AND (body::jsonb->'objects') @> %s::jsonb", (json.dumps([dict(kind='feed',id=feed)]),)).fetchall()
        jobs=self.db.execute("SELECT * FROM jobs WHERE kind='discover' AND item=ANY(%s) AND status IN ('pending','running','failed','unavailable','blocked') AND (reason_message IS NOT NULL OR reason_code IS NOT NULL) ORDER BY id",([feed],)).fetchall()
        feed_row=self.db.execute('SELECT connection FROM feeds WHERE id=%s',(feed,)).fetchone()
        deleting=bool(feed_row and feed_row['connection']=='deleting')
        candidates: dict[str,dict[str,Any]]={}
        for row in (() if deleting else jobs):
            code=row['reason_code'] or 'work-discover'
            if code in ('codex-choice','ai-permission','youtube-admission','processor-config'):
                continue
            if self.discover_profile_suppresses(feed,code):
                continue
            identity='job:'+code+':'+feed+':discover'+('' if code.startswith('source-') else ':'+(row['item'] or ''))
            if identity in candidates:
                continue
            body=dict(id=identity,code=code,section='object',title='Не удалось обновить источник',consequence='Сохранённое содержимое остаётся доступным.',next_step='Включите обработку, чтобы повторить попытку.' if row['status']=='blocked' else 'Повторите загрузку вручную.' if row['status']=='failed' else 'Проверьте доступность источника.' if row['status']=='unavailable' else 'Око повторит попытку автоматически.',objects=[dict(kind='feed',id=feed)],action=None,operation='producer:discover',state='retrying' if row['status']=='running' or not row['reason_message'] else 'current',next_at=None)
            if row['reason_message']:body['reason']=row['reason_message']
            if code=='source-reconnect':body.update(section='action',next_step='Проверьте привязку источника; доступ не изменяется автоматически.')
            candidates[identity]=body
        current={}
        for canonical,candidate in candidates.items():
            identity=canonical
            previous=next((record for record in old if json.loads(record['body']).get('discover_job_identity')==canonical),None)
            if previous:
                identity=previous['identity']
                candidate['id']=identity
                candidate['discover_job_identity']=canonical
            else:
                occupied=self.db.execute('SELECT body FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
                same_cause=False
                if occupied:
                    saved=json.loads(occupied['body'])
                    saved=saved if isinstance(saved,dict) else {}
                    same_cause=(saved.get('operation')=='producer:discover' and saved.get('code')==candidate['code'] and saved.get('discover_job_identity',canonical)==canonical and any(obj.get('kind')=='feed' and obj.get('id')==feed for obj in saved.get('objects',[]) if isinstance(obj,dict)))
                if occupied and not same_cause:
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
        for prior in old:
            if prior['identity'] not in current:
                self.sync_candidate(prior['identity'],None,now,removed_outcome='irrelevant' if deleting else 'resolved')
        for identity,candidate in current.items():
            self.sync_candidate(identity,candidate,now)





    def processor_youtube_paused(self, now: float) -> bool:
        from oko.storage.reading import reader_wall
        blocked=self.store.youtube._limits()['blocked_until']
        return not _readable_timestamp(blocked) or blocked>reader_wall(now)

    def processor_configuration_rows(self, job_ids: list[int] | None = None) -> list[Row]:
        restriction=' AND id=ANY(%s)' if job_ids is not None else ''
        params=(job_ids,) if job_ids is not None else ()
        return list(self.db.execute(f"""SELECT jobs.id,jobs.kind,jobs.item,jobs.status,jobs.reason_message,jobs.feed_id,
            CASE WHEN jobs.kind='read' THEN jobs.item ELSE coalesce(items.feed_id,media_owner.feed_id,linked_root.feed_id) END AS resolved_feed_id,
            m.resource_key,
            CASE WHEN jobs.kind='read' OR (jobs.kind='download_media' AND m.id IS NULL) THEN NULL ELSE coalesce(linked_root.id,m.publication_id,jobs.item) END AS affected_item
            FROM (SELECT * FROM jobs WHERE processor_visible{restriction} ORDER BY kind,item OFFSET 0) jobs
            LEFT JOIN publications items ON jobs.kind!='download_media' AND items.id=jobs.item
            LEFT JOIN media_files m ON jobs.kind='download_media' AND m.id=jobs.item
            LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
            LEFT JOIN publication_links link ON link.target_publication_id=coalesce(items.id,media_owner.id)
            LEFT JOIN publications linked_root ON linked_root.id=link.source_publication_id
            WHERE (jobs.status IN ('pending','running','failed','unavailable','blocked')
                OR (jobs.status='done' AND jobs.kind='fetch' AND jobs.reason_message IS NOT NULL)) ORDER BY jobs.kind,jobs.item,linked_root.id""",params).fetchall())
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
            feed=(row['item'] if kind in ('discover','read','feed-delete') else row['resolved_feed_id'] or row['feed_id']) or row['item'] or ''
            if candidate is None:
                candidate=dict(id='processor:configuration',code='processor-config',section='action',title='Требуется настройка обработчика',
                    consequence='Око продолжает попытки удаления.' if kind in ('delete','feed-delete') else 'Сохранённое содержимое остаётся доступным.',
                    next_step='Включите обработку, чтобы повторить попытку.' if row['status']=='blocked' else 'Повторите загрузку вручную.' if row['status'] in ('done','failed') else 'Проверьте доступность источника.' if row['status']=='unavailable' else 'Откройте настройки обработчика.',
                    objects=[],action=None,operation='producer:'+kind,created_at=now,
                    state='retrying' if row['status']=='running' or not row['reason_message'] else 'current',finished_at=None,outcome=None,next_at=None)
                if kind!='delete' and row['reason_message']:
                    candidate['reason']=row['reason_message']
            obj_kind='feed' if kind in ('discover','read','feed-delete') else 'material'
            obj_id=feed if obj_kind=='feed' else row['affected_item']
            if kind!='delete' and isinstance(obj_id,str) and obj_id and (obj_kind,obj_id) not in seen:
                candidate['objects'].append(dict(kind=obj_kind,id=obj_id))
                seen.add((obj_kind,obj_id))
        return candidate

    def refresh_processor_visibility(self, job_ids: list[int]) -> bool:
        """Refresh only explicitly affected jobs; the caller owns the transaction."""
        changed=False
        for start in range(0,len(job_ids),400):
            rows=self.db.execute("""UPDATE jobs j SET processor_visible=requested.visible,processor_caption=requested.caption FROM (
                SELECT j.id,coalesce(j.reason_code='processor-config' AND EXISTS (SELECT 1 FROM media_files m WHERE j.kind='download_media' AND m.id=j.item AND m.resource_key='youtube:subtitles'),false) AS caption,coalesce(j.reason_code='processor-config' AND
                    (j.status IN ('pending','running','failed','unavailable','blocked')
                        OR (j.kind='fetch' AND j.status='done' AND j.reason_message IS NOT NULL))
                    ,false) AS visible
                FROM jobs j WHERE j.id=ANY(%s) AND (j.processor_visible OR j.processor_caption OR j.reason_code='processor-config') OFFSET 0) AS requested
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
                    AND (j.processor_visible OR j.reason_code='processor-config')
                UNION SELECT j.id FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id
                    WHERE m.publication_id=ANY(%s) AND (j.processor_visible OR j.reason_code='processor-config')""",(batch,batch)).fetchall()
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
                body={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome')}
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
        source_owned_filter="AND NOT ((kind IS NOT DISTINCT FROM 'filter' OR kind IS NOT DISTINCT FROM 'theses') AND identity_event_owned AND reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')) AND NOT (kind IS NOT DISTINCT FROM 'download_media' AND identity_event_owned AND reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')) AND NOT (kind IN ('delete','feed-delete') AND deletion_extra_owned) AND NOT (kind IS NOT DISTINCT FROM 'download_media' AND reason_code IS NULL AND reason_message IS NOT NULL AND media_null_owned) AND NOT (kind IS NOT DISTINCT FROM 'fetch' AND reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch') AND identity_event_owned) AND NOT (kind IS NOT DISTINCT FROM 'download_media' AND subtitle_event_owned) AND NOT (kind IS NOT DISTINCT FROM 'fetch' AND reason_code IS NULL AND reason_message IS NOT NULL AND fetch_null_owned) AND NOT (kind IN ('fetch','download_media','filter','theses','delete','feed-delete') AND reason_code IS NOT NULL AND reason_code LIKE 'source-%' AND source_event_owned)" if scope_items is None else ''
        admission_filter="AND reason_code IS DISTINCT FROM 'youtube-admission'" if scope_items is None and getattr(self.store,'youtube_admission_ready',False) else ''
        subtitle_owned_filter='AND NOT j.subtitle_event_owned' if scope_items is None else ''
        item_join_filter=("(jobs.kind IS DISTINCT FROM 'download_media' OR left(jobs.reason_code,7)='source-' "
            "OR jobs.reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch','youtube-admission') "
            "OR (jobs.reason_code IS NULL AND NOT jobs.subtitle_event_owned) "
            + ("OR (m.resource_key='youtube:subtitles' AND jobs.reason_code IS DISTINCT FROM 'processor-config')" if youtube['paused'] else '') + ")") if scope_items is not None else 'TRUE'
        jobs = self.db.execute(f"SELECT jobs . * , coalesce ( items.feed_id , media_owner.feed_id , linked_root.feed_id ) AS resolved_feed_id , m . resource_key AS resource_key , m.publication_id AS scoped_media_publication_id , coalesce ( linked_root . id , m . publication_id , jobs . item ) AS affected_item FROM (SELECT * FROM (SELECT * FROM jobs WHERE {direct_scope}kind IS DISTINCT FROM 'read' AND kind IS DISTINCT FROM 'discover' AND ({'TRUE' if scope_items is not None else 'FALSE'} OR reason_code IS DISTINCT FROM 'processor-config') AND ({'TRUE' if scope_items is not None else 'FALSE'} OR NOT ((kind IS NOT DISTINCT FROM 'filter' OR kind IS NOT DISTINCT FROM 'theses') AND item IS NOT NULL AND item<>'' AND (((reason_code='' OR (reason_code IS NULL AND reason_message IS NOT NULL))) OR (reason_code IS NOT NULL AND reason_code<>'' AND reason_code NOT LIKE '{source_like}' AND reason_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'))))) AND ({'TRUE' if scope_items is not None else 'FALSE'} OR NOT (kind IS NOT DISTINCT FROM 'fetch' AND item IS NOT NULL AND item<>'' AND reason_code IS NOT NULL AND reason_code NOT LIKE '{source_like}' AND reason_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch') AND NOT fetch_null_ambiguous)) AND NOT (kind IN ('delete','feed-delete') AND coalesce(nullif(reason_code,''),'work-'||kind) IN ('delete','feed-delete','work-delete','work-feed-delete')) AND (reason_code IS NOT NULL OR reason_message IS NOT NULL) {source_owned_filter} {admission_filter} AND ({'TRUE' if scope_items is not None else 'FALSE'} OR NOT (kind IS NOT DISTINCT FROM 'download_media' AND item IS NOT NULL AND item<>'' AND reason_code IS NOT NULL AND reason_code NOT LIKE '{source_like}' AND reason_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'))) UNION ALL SELECT j.* FROM media_files subtitle JOIN jobs j ON j.kind='download_media' AND j.item=subtitle.id WHERE {media_scope}{'FALSE' if scope_items is not None else 'TRUE'} AND subtitle.resource_key='youtube:subtitles' AND j.kind='download_media' {subtitle_owned_filter} AND j.item IS NOT NULL AND j.item<>'' AND j.reason_code IS NOT NULL AND j.reason_code NOT LIKE '{source_like}' AND j.reason_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')) status_jobs ORDER BY kind,item OFFSET 0) jobs LEFT JOIN media_files m ON jobs . kind = 'download_media' AND m . id = jobs . item LEFT JOIN publications items ON items . id = jobs . item AND ({item_join_filter}) LEFT JOIN publications media_owner ON media_owner . id = m . publication_id LEFT JOIN publication_links link ON link . target_publication_id = coalesce ( items . id , media_owner . id ) LEFT JOIN publications linked_root ON linked_root . id = link . source_publication_id WHERE ( jobs . status IN ( 'pending' , 'running' , 'failed' , 'unavailable' , 'blocked' ) OR ( jobs . status = 'done' AND jobs . kind = 'fetch' AND jobs . reason_code IS NOT NULL AND jobs . reason_message IS NOT NULL ) ) AND ( jobs . reason_code IS NOT NULL OR jobs . reason_message IS NOT NULL ) AND jobs.kind IS DISTINCT FROM 'read' AND ({'TRUE' if scope_items is not None else 'FALSE'} OR jobs.reason_code IS DISTINCT FROM 'processor-config') AND NOT (jobs.kind IN ('delete','feed-delete') AND coalesce(nullif(jobs.reason_code,''),'work-'||jobs.kind) IN ('delete','feed-delete','work-delete','work-feed-delete'))",parameters).fetchall()
        if scope_items is not None:
            source_ids=sorted({job['resolved_feed_id'] or job['feed_id'] for job in jobs if job['resolved_feed_id'] or job['feed_id']})
            for start in range(0,len(source_ids),400):
                batch=source_ids[start:start+400]
                feeds.update({row['id']:dict(row) for row in self.db.execute('SELECT id,kind FROM feeds WHERE id=ANY(%s)',(batch,))})
            profile_codes=('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')
            job_feeds={job['resolved_feed_id'] or job['feed_id'] or job['item'] or '' for job in jobs if job['reason_code'] in profile_codes}
            if job_feeds:
                needles=[json.dumps([dict(kind='feed',id=feed)]) for feed in sorted(job_feeds)]
                for row in self.db.execute("SELECT body FROM reader_problems WHERE identity LIKE 'profile:%%' AND finished IS NULL AND (body::jsonb->'objects') @> ANY(%s::jsonb[])",(needles,)):
                    profile_feeds.update(obj['id'] for obj in json.loads(row['body'])['objects'] if obj['kind']=='feed')
        source_owned: set[str]=set()
        if scope_items is None:
            source_keys={
                'job:'+job['reason_code']+':'+(job['resolved_feed_id'] or job['feed_id'] or job['item'] or '')+':'+job['kind']
                for job in jobs if isinstance(job['reason_code'],str) and job['reason_code'].startswith('source-')
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
            code = job['reason_code']
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
            if job['item'] in (None,'') and job['kind'] in ('fetch','filter','theses','download_media','delete','feed-delete') and code not in ('youtube-admission','processor-config'):
                continue
            if job['kind']=='download_media' and job['resource_key']=='youtube:subtitles':
                if youtube['paused'] or code is None and youtube['next_at']>now:
                    continue
            if code is None and job['kind'] == 'fetch' and feeds.get(resolved_feed, {}).get('kind') == 'youtube' and youtube['next_at'] > now:
                continue
            if code == 'youtube-admission':
                continue
            code = code or 'work-' + job['kind']
            feed = resolved_feed or job['item'] or ''
            if code in ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch') and feed in profile_feeds:
                continue
            shared = code in ('processor-config', 'ai-permission') or code.startswith('source-')
            identity = 'job:' + code + ':' + feed + ':' + job['kind'] + ('' if shared else ':' + job['item'])
            if code.startswith('source-') and identity in source_owned:continue
            if code == 'processor-config':
                identity = 'processor:configuration'
            settings_action = code in ('processor-config', 'ai-permission')
            obj = None if job['affected_item'] in (None,'') and job['kind'] not in ('discover','read','feed-delete') else dict(kind='feed' if job['kind'] in ('discover', 'read', 'feed-delete') else 'material', id=feed if job['kind'] in ('discover', 'read', 'feed-delete') else job['affected_item'])
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
                    objects=[],action=action,operation='producer:'+kind,created_at=now,state='retrying' if job['status'] == 'running' or not job['reason_message'] else 'current',finished_at=None,outcome=None,next_at=None)
                if not shared:
                    groups[group_key]['_status_job_target'] = job['item']
                if kind != 'delete' and job['reason_message']:
                    groups[group_key]['reason'] = job['reason_message']
                if code == 'source-reconnect':
                    groups[group_key].update(section='action', next_step='Проверьте привязку источника; доступ не изменяется автоматически.', action=None)
            if job['kind'] != 'delete' and obj is not None and obj not in groups[group_key]['objects']:
                groups[group_key]['objects'].append(obj)
        if youtube['paused'] and (scope_items is not None or not getattr(self.store,'youtube_admission_ready',False)):
            owners={job['affected_item'] for job in jobs if job['status'] in ('pending','running')
                and feeds.get(job['resolved_feed_id'] or job['feed_id'],{}).get('kind')=='youtube'
                and (job['kind']=='fetch' or job['resource_key']=='youtube:subtitles')}
            youtube_feeds=[feed_id for feed_id,feed in feeds.items() if feed.get('kind')=='youtube']
            if youtube_feeds and scope_items is None:
                rows=self.db.execute(f"""WITH affected(id) AS (
                    SELECT id FROM publications WHERE feed_id=ANY(%s)
                    UNION SELECT l.target_publication_id FROM (SELECT id,feed_id FROM publications WHERE feed_id IS NOT NULL) m JOIN publication_links l ON l.source_publication_id=m.id WHERE m.feed_id=ANY(%s)
                    UNION SELECT item FROM jobs WHERE kind='fetch' AND feed_id=ANY(%s) AND status IN ('pending','running') AND reason_code IN ('','work-fetch')
                ) SELECT DISTINCT coalesce(root.id,j.item) AS affected FROM affected a JOIN jobs j ON j.kind='fetch' AND j.item=a.id
                LEFT JOIN publications items ON items.id=j.item LEFT JOIN publication_links l ON l.target_publication_id=items.id
                LEFT JOIN publications root ON root.id=l.source_publication_id
                WHERE j.status IN ('pending','running') AND j.reason_code IN ('','work-fetch')
                AND coalesce(items.feed_id,root.feed_id,j.feed_id)=ANY(%s)""",(youtube_feeds,youtube_feeds,youtube_feeds,youtube_feeds))
                owners.update(row['affected'] for row in rows)
            objects=[dict(kind='material',id=owner) for owner in sorted(owners)]
            groups['youtube:admission'] = dict(id='youtube:admission',code='youtube-admission',section='wait',title='YouTube ограничил запросы',
                consequence=f'Ожидают субтитров: {len(objects)}.',next_step='Око повторит получение после паузы.',
                objects=objects,action=None,created_at=now,state='retrying',finished_at=None,outcome=None,next_at=youtube['next_at'])
        return groups,feeds,youtube

    def candidates(self, now: float) -> list[dict[str, Any]]:
        current: list[dict[str, Any]] = [
            dict(id='history-invalid:'+feed['id'], code='history-invalid', section='action',
                 title='Повреждена история фида',
                 consequence='Новые публикации этого фида не собираются; сохранённые данные остаются доступными.',
                 next_step='Проверьте сохранённую историю фида.',
                 objects=[dict(kind='feed', id=feed['id'])], action=None, created_at=now,
                 state='current', finished_at=None, outcome=None, next_at=None)
            for feed in self.store.subscriptions.feeds_with_history() if feed.get('initial_history_invalid')
        ]
        groups,feeds,youtube=self.incomplete_generic_groups(now)
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
        from oko.storage.youtube_fetch_problems import valid as valid_youtube_fetch_reservation
        fetch_reserved_rows={row['identity']:valid_youtube_fetch_reservation(row) for row in records if row['youtube_fetch_due'] is not None}
        reserved_keys=list(set(reserved_rows)|set(fetch_reserved_rows))
        pointers={}
        for start in range(0,len(reserved_keys),400):
            pointers.update({row['key']:row['value'] for row in self.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',(reserved_keys[start:start+400],))})
        for row in records:
            reserved=reserved_rows.get(row['identity'])
            if reserved and pointers.get(row['identity'])==json.dumps(['oko-subtitle-v1',reserved['media_job_id'],reserved['subtitle_canonical']]):continue
            fetch_reserved=fetch_reserved_rows.get(row['identity'])
            if fetch_reserved and pointers.get(row['identity'])==json.dumps(['oko-youtube-fetch-v1',fetch_reserved['fetch_job_id'],fetch_reserved['fetch_youtube_canonical']]):continue
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
        from oko.storage.youtube_fetch_problems import verified as verified_youtube_fetch_reservations
        future_fetch=self.db.execute('SELECT * FROM reader_problems WHERE youtube_fetch_due IS NOT NULL AND youtube_fetch_reserved_owned AND youtube_fetch_due<=%s',(now,)).fetchall()
        proven_fetch=verified_youtube_fetch_reservations(self.store,list(future_fetch),now)
        for future in future_fetch:
            body=proven_fetch.get(future['id'])
            occupied=existing.get(body['fetch_youtube_canonical']) if body else None
            if occupied is not None:protected_fetch_null.add(occupied['id'])
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
            target=candidate.get('_status_job_target')
            target=target if isinstance(target,str) else None
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
            if isinstance(operation,str):
                claimed.setdefault(identity,(operation,target))
            separated.append(candidate)
        candidates = separated
        by_identity = {candidate['id']: candidate for candidate in candidates}
        identities = set(by_identity)
        feeds = feeds if feeds is not None else {f['id']: f for f in self.store.subscriptions.feeds()}
        with self.db:
            history_feed_ids = {obj['id'] for candidate in candidates if candidate.get('code') == 'history-invalid'
                             for obj in candidate.get('objects', []) if obj.get('kind') == 'feed'}
            # Recheck all formerly invalid feeds together, including feeds that
            # became damaged after the candidates were read.
            for row in existing.values():
                if row['identity'] in identities:
                    continue
                body = json.loads(row['body'])
                if body.get('code') == 'history-invalid':
                    history_feed_ids.update(obj['id'] for obj in body.get('objects', []) if obj.get('kind') == 'feed')
            history_feeds = sorted(history_feed_ids)
            live_feeds: set[str] = set()
            history_check: dict[str, Any] = {}
            if history_feeds:
                # A repair may have committed after the candidate snapshot.
                from oko.storage.transaction import begin_write
                from oko.storage import memberships
                from oko.subscriptions.history import InvalidHistory
                begin_write(self.db)
                memberships.lock_graph(self.db)
                pending: set[str] = set()
                for start in range(0, len(history_feeds), 400):
                    pending.update(row[0] for row in self.db.execute(
                        'SELECT DISTINCT feed FROM history_deferred_events WHERE feed=ANY(%s)',
                        (history_feeds[start:start+400],)))
                self.replay_history_batch(pending)
                for start in range(0, len(history_feeds), 400):
                    chunk = history_feeds[start:start+400]
                    live_feeds.update(row[0] for row in self.db.execute(
                        'SELECT id FROM feeds WHERE NOT deleted AND id=ANY(%s) FOR KEY SHARE', (chunk,)))
                    history_check.update(self.store.subscriptions.history.statuses(chunk, lock=True))
                candidates = [candidate for candidate in candidates if candidate.get('code') != 'history-invalid'
                    or any(obj.get('id') in live_feeds and (obj['id'] not in history_check
                        or isinstance(history_check[obj['id']], InvalidHistory))
                        for obj in candidate.get('objects', []) if obj.get('kind') == 'feed')]
                by_identity = {candidate['id']: candidate for candidate in candidates}
                identities = set(by_identity)
            added: list[tuple[str,str,str,str,float]] = []
            changed = False
            for row in existing.values():
                if row['identity'] not in identities and row['identity'] not in ('processor:codex-choice','processor:configuration') and not row['identity'].startswith(('action:', 'profile:')):
                    body = json.loads(row['body'])
                    if getattr(self.store,'youtube_admission_ready',False) and (row['identity']=='youtube:admission' or isinstance(body,dict) and body.get('youtube_admission_owner')=='youtube:admission') and (admission_current is None or row['id']!=admission_current['id']):continue
                    if isinstance(body,dict) and body.get('code')=='processor-config' and body.get('processor_group_identity')=='processor:configuration':
                        continue
                    if isinstance(body.get('source_group_identity'),str):continue
                    code=body.get('code')
                    if code == 'history-invalid':
                        damaged_feed = next((obj.get('id') for obj in body.get('objects', [])
                                             if obj.get('kind') == 'feed'), None)
                        if damaged_feed and damaged_feed in live_feeds and (
                            damaged_feed not in history_check or isinstance(history_check[damaged_feed], InvalidHistory)
                        ):
                            continue
                    if row['id'] in protected_fetch_null:continue
                    if getattr(self.store,'youtube_admission_ready',False) and code=='youtube-admission':
                        from oko.storage.youtube_admission import valid as valid_admission
                        if admission_current is not None and row['id']==admission_current['id'] and valid_admission(row):continue
                        if row['identity']=='youtube:admission' or body.get('youtube_admission_owner')=='youtube:admission':continue
                    if code=='material-incomplete':
                        continue
                    eligible_fetch=(isinstance(code,str) and bool(code) and not code.startswith('source-') and code not in ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch','work-fetch'))
                    if body.get('operation')=='producer:fetch' and (eligible_fetch or isinstance(body.get('fetch_job_id'),str)):
                        continue
                    if body.get('operation')=='producer:download_media' and isinstance(body.get('media_job_id'),str):
                        continue
                    if body.get('operation') in ('producer:filter','producer:theses') and isinstance(body.get('processing_job_id'),str):
                        continue
                    if body.get('operation') in ('producer:discover','producer:read'):
                        continue
                    if body.get('operation') in ('producer:delete','producer:feed-delete') and isinstance(body.get('deletion_job_id'),str):
                        continue
                    irrelevant = body['code'] == 'codex-choice' and self.store.codex_config() is None
                    irrelevant = irrelevant or any(obj['kind'] == 'feed' and feeds.get(obj['id'], {}).get('connection', 'deleting') == 'deleting' for obj in body['objects'])
                    self.db.execute('UPDATE reader_problems SET finished = %s , outcome = %s WHERE id = %s', (now, 'irrelevant' if irrelevant else 'resolved', row['id']))
                    changed = True
            for candidate in candidates:
                if not candidate['id'].startswith('action:'):
                    candidate.setdefault('operation','producer:status')
                identity = candidate['id']
                body = {key: value for key, value in candidate.items() if key not in ('id', 'created_at', 'finished_at', 'outcome', '_status_job_target')}
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
        rows = self.db.execute('SELECT * FROM reader_problems WHERE finished IS NULL OR finished > %s OR (subtitle_due IS NOT NULL AND subtitle_reserved_owned AND subtitle_due<=%s) OR (youtube_fetch_due IS NOT NULL AND youtube_fetch_reserved_owned AND youtube_fetch_due<=%s) ORDER BY created DESC', (now - 86400,now,now)).fetchall()
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
        youtube_fetch_reservations=proven_fetch
        for row in rows:
            virtual_fetch=youtube_fetch_reservations.get(row['id'])
            damaged_fetch=(row['youtube_fetch_due'] is not None and virtual_fetch is None and row['youtube_fetch_reserved_owned'] and row['finished'] is not None and row['finished']<=now-86400)
            if row['youtube_fetch_due'] is not None and virtual_fetch is None and not damaged_fetch and row['finished'] is not None and row['finished']<=now-86400:continue
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
            incomplete_legacy=(isinstance(problem,dict) and problem.get('code')=='material-incomplete' or not isinstance(problem,dict) and row['identity'].startswith('incomplete:'))
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
            if row['finished'] is None and row['incomplete_due'] is not None and row['incomplete_due']<=now:
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
            if virtual_fetch is not None:
                problem=dict(virtual_fetch)
                problem.pop('fetch_youtube_reservation',None);problem.pop('fetch_youtube_canonical',None)
            elif damaged_fetch:
                problem=dict(code='work-fetch',section='object',title='Состояние загрузки требует проверки',consequence='Исходная запись сохранена.',next_step='Проверьте журнал диагностики.',objects=[],action=None,operation='producer:fetch-legacy',state='current',next_at=None)
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
            finished = None if (generic or processor_legacy or incomplete_legacy or youtube_legacy) and (bad_finished or bad_outcome) else row['finished']
            if virtual_subtitle is not None or damaged_subtitle or virtual_fetch is not None or damaged_fetch:finished=None
            if virtual_finished is not None:finished=virtual_finished
            if virtual_finished is not None and virtual_finished<=now-86400:continue
            created = now if (generic or processor_legacy or incomplete_legacy or youtube_legacy) and bad_created else row['created']
            if virtual_subtitle is not None or damaged_subtitle:created=row['subtitle_due']
            if virtual_fetch is not None or damaged_fetch:created=row['youtube_fetch_due']
            problem.update(id=row['id'], created_at=created, finished_at=finished,
                           outcome=None if finished is None else 'resolved' if virtual_finished is not None else row['outcome'], state='finished' if finished is not None else problem['state'])
            (recent if finished is not None else current).append(problem)
        from oko.storage.incomplete_group_draft import virtual_due_groups
        current.extend(virtual_due_groups(self.store,now))
        resumed=self.processor_resume_candidate(now)
        if resumed:
            identity,problem,created=resumed
            problem.update(id=identity,created_at=created,finished_at=None,outcome=None)
            current.append(problem)
        current.sort(key=lambda problem:(-float(problem['created_at']),str(problem['id'])))
        recent.sort(key=lambda problem:(-float(problem['created_at']),str(problem['id'])))
        return dict(current=current, recent=recent, count=len(current), updated_at=now)
