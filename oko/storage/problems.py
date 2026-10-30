"""Current reader obstacles; identity and recovery are independent of message text."""
from oko.storage.publication_owners import owner_expr
from oko.storage.database import expect_row
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


class Problems:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        self.db = store.db
        self._receipt_cleanup_cursor: tuple[float, int] | None = None
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

    def sync_profiles(self, now: float, roles: set[str] | None = None) -> None:
        if roles is None:
            previous = {row['identity']:row['body'] for row in self.db.execute("SELECT identity,body FROM reader_problems WHERE identity LIKE 'profile:%%' AND finished IS NULL")}
            identities = set(previous)
        else:
            identities = {'profile:'+role for role in roles}
            previous = {row['identity']:row['body'] for row in self.db.execute('SELECT identity,body FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL', (sorted(identities),))}
        with reading_snapshot():
            if roles is None:
                feeds = {row['id']:dict(row) for row in self.db.execute('SELECT id,kind,connection FROM feeds ORDER BY rowid')}
            else:
                role_list = sorted(roles)
                ids = set(roles)
                ids.update(row['feed'] for row in self.db.execute("SELECT feed FROM telegram_bindings WHERE indexed_role=ANY(%s) OR (indexed_role IS NULL AND feed=ANY(%s))", (role_list,role_list)))
                ids.update(obj['id'] for body in previous.values() for obj in json.loads(body)['objects'] if obj['kind']=='feed')
                feeds = {row['id']:dict(row) for row in self.db.execute(
                    'SELECT id,kind,connection FROM feeds WHERE id=ANY(%s) ORDER BY rowid', (sorted(ids),))}
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
        for identity in sorted(identities):
            candidate = candidates.get(identity)
            outcome = 'resolved'
            if candidate is None and identity in previous:
                if any(obj['kind']=='feed' and feeds.get(obj['id'],{}).get('connection','deleting')=='deleting' for obj in json.loads(previous[identity])['objects']):
                    outcome = 'irrelevant'
            self.sync_candidate(identity,candidate,now,removed_outcome=outcome)
        affected = {obj['id'] for group in candidates.values() for obj in group['objects'] if obj['kind']=='feed'}
        affected.update(obj['id'] for body in previous.values() for obj in json.loads(body)['objects'] if obj['kind']=='feed')
        if self.db.execute("SELECT 1 FROM state WHERE key='read-problems-recovered-v1'").fetchone():
            feed_ids = sorted(affected)
            for start in range(0,len(feed_ids),400):
                for job in self.db.execute("SELECT item FROM jobs WHERE kind='read' AND item=ANY(%s)",(feed_ids[start:start+400],)):
                    self.sync_read_feed(job['item'],now)

    def candidates(self, now: float) -> list[dict[str, Any]]:
        current: list[dict[str, Any]] = []
        feeds = {feed['id']: feed for feed in self.store.subscriptions.feeds()}
        profile_feeds = {obj['id'] for row in self.db.execute("SELECT body FROM reader_problems WHERE identity LIKE 'profile:%%' AND finished IS NULL")
                         for obj in json.loads(row['body'])['objects'] if obj['kind'] == 'feed'}
        groups: dict[str | tuple[str,str] | tuple[str,str,str], dict[str, Any]] = {}
        youtube = self.store.youtube.status(now)
        jobs = self.db.execute(f"SELECT jobs . * , coalesce ( {owner_expr('items')} , {owner_expr('media_owner')} , {owner_expr('linked_root')} ) AS resolved_feed_id , coalesce ( items . viewed , media_owner . viewed ) AS viewed , m . resource_key AS resource_key , coalesce ( linked_root . id , m . publication_id , jobs . item ) AS affected_item FROM (SELECT * FROM jobs WHERE (kind IS DISTINCT FROM 'read' OR problem_code='processor-config') AND NOT (kind IN ('delete','feed-delete') AND coalesce(nullif(problem_code,''),'work-'||kind) IN ('delete','feed-delete','work-delete','work-feed-delete')) AND (problem_code IS NOT NULL OR error IS NOT NULL) ORDER BY kind,item OFFSET 0) jobs LEFT JOIN publications items ON items . id = jobs . item LEFT JOIN media_files m ON jobs . kind = 'download_media' AND m . id = jobs . item LEFT JOIN publications media_owner ON media_owner . id = m . publication_id LEFT JOIN publication_links link ON link . target_publication_id = coalesce ( items . id , media_owner . id ) LEFT JOIN publications linked_root ON linked_root . id = link . source_publication_id AND linked_root . viewed = 0 WHERE ( jobs . status IN ( 'pending' , 'running' , 'failed' , 'unavailable' , 'blocked' ) OR ( jobs . status = 'done' AND jobs . kind = 'fetch' AND jobs . problem_code IS NOT NULL AND jobs . error IS NOT NULL ) ) AND ( jobs . problem_code IS NOT NULL OR jobs . error IS NOT NULL ) AND (jobs.kind IS DISTINCT FROM 'read' OR jobs.problem_code='processor-config') AND NOT (jobs.kind IN ('delete','feed-delete') AND coalesce(nullif(jobs.problem_code,''),'work-'||jobs.kind) IN ('delete','feed-delete','work-delete','work-feed-delete'))").fetchall()
        for job in jobs:
            resolved_feed = job['item'] if job['kind']=='read' else job['resolved_feed_id'] or job['feed_id']
            code = job['problem_code']
            if job['kind']=='read' and code!='processor-config':
                continue
            if code in ('codex-choice', 'ai-permission'):
                continue
            if job['kind']=='download_media' and job['resource_key']=='youtube:subtitles':
                if youtube['paused'] or code is None and self.store.youtube.ready_at()>now:
                    continue
            if code is None and job['kind'] == 'fetch' and feeds.get(resolved_feed, {}).get('kind') == 'youtube' and self.store.youtube.ready_at() > now:
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
        if youtube['paused']:
            owners={job['affected_item'] for job in jobs if job['status'] in ('pending','running')
                and feeds.get(job['resolved_feed_id'] or job['feed_id'],{}).get('kind')=='youtube'
                and (job['kind']=='fetch' or job['resource_key']=='youtube:subtitles')}
            objects=[dict(kind='material',id=owner) for owner in sorted(owners)]
            groups['youtube:admission'] = dict(id='youtube:admission',code='youtube-admission',section='wait',title='YouTube ограничил запросы',
                consequence=f'Ожидают субтитров: {len(objects)}.',next_step='Око повторит получение после паузы.',
                objects=objects,action=None,created_at=now,state='retrying',finished_at=None,outcome=None,next_at=youtube['next_at'])
        affected = {obj['id'] for group in groups.values() for obj in group['objects'] if obj['kind'] == 'material'}
        known_partial = {obj['id'] for record in self.db.execute("SELECT body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code' = 'material-incomplete'") for obj in json.loads(record['body'])['objects']}
        saved = self.db.execute("SELECT i . id , i . feed_id AS feed_id , i.content::jsonb#>>'{extra,_reader}' AS header , f . status AS fetch_status , f . problem_code AS fetch_code , EXISTS ( SELECT 1 FROM media_files m WHERE m . publication_id = i . id AND EXISTS ( SELECT 1 FROM jobs j WHERE j . item = m . id AND j . kind = 'download_media' AND j . status IN ( 'pending' , 'running' ) AND j . error IS NULL AND j . problem_code IS NULL ) ) AS media_waiting , CASE WHEN i . platform = 'youtube' THEN EXISTS ( SELECT 1 FROM media_files m JOIN jobs j ON j . item = m . id AND j . kind = 'download_media' WHERE m . publication_id = i . id AND m . resource_key = 'youtube:subtitles' AND j . status = 'pending' AND j . problem_code IS NULL ) ELSE FALSE END AS caption_waiting FROM publications i LEFT JOIN jobs f ON f . item = i . id AND f . kind = 'fetch' WHERE i . viewed = 0 AND i . feed_id IS NOT NULL AND i . complete = 1 AND NOT i . source_deleted AND CASE WHEN jsonb_typeof(i.content::jsonb#>'{extra,_reader,full}')='boolean' THEN NOT (i.content::jsonb#>>'{extra,_reader,full}')::boolean ELSE (i.content::jsonb#>>'{extra,_reader,full}')::integer=0 END".replace('i . feed_id', owner_expr('i'))).fetchall()
        for row in saved:
            if row['id'] in affected:
                continue
            header = self.header(row['header'])
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
        for row in problem_links.material_records(self.db, 'problem', members):
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
                self.db.execute("UPDATE reader_problems SET body = %s , finished = COALESCE ( finished , %s ) , outcome = 'irrelevant' WHERE id = %s",
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
        existing = {row['identity']: row for row in self.db.execute("SELECT * FROM reader_problems WHERE finished IS NULL")}
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
                    if (isinstance(old_body,dict) and old_body.get('operation')==alias_operation
                        and old_body.get('code')==candidate.get('code') and alias not in candidate_ids and alias not in claimed_alternates):
                        isolated_aliases.append(alias)
                        claimed_alternates.add(alias)
                    protected = (isinstance(old_body,dict) and old_body.get('code') in ('delete','feed-delete','work-delete','work-feed-delete')
                                 and (old_body.get('operation') in ('producer:delete','producer:feed-delete')
                                      or (old_body.get('operation') in (None,'producer:status') and (':delete:' in alias or ':feed-delete:' in alias))))
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
                (isinstance(body,dict) and isinstance(body.get('operation'),str) and body['operation'].startswith('producer:') and body['operation'] not in ('producer:status',operation))
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
                if row['identity'] not in identities and row['identity'] != 'processor:codex-choice' and not row['identity'].startswith(('action:', 'profile:')):
                    body = json.loads(row['body'])
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
        rows = self.db.execute('SELECT * FROM reader_problems WHERE finished IS NULL OR finished > %s ORDER BY created DESC', (now - 86400,)).fetchall()
        action_identities=sorted({row['identity'][7:] for row in rows if row['identity'].startswith('action:')})
        receipts: dict[str,Any] = {}
        for start in range(0,len(action_identities),400):
            for saved_receipt in self.db.execute('SELECT r.identity,r.id,r.body,r.result,r.started FROM unnest(%s::text[]) target(identity) CROSS JOIN LATERAL (SELECT identity,id,body,result,started FROM reader_action_results WHERE identity=target.identity ORDER BY sequence DESC LIMIT 1) r',(action_identities[start:start+400],)):
                receipts[saved_receipt['identity']]=saved_receipt
        for row in rows:
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
            if row['finished'] is None:
                candidate = by_identity.get(row['identity'], {})
                if candidate.get('reason'):
                    problem['reason'] = candidate['reason']
            for obj in problem['objects']:
                obj.pop('label',None)
            visible_id = row['id']
            if not visible_id:
                visible_id = _legacy_problem_id(row['identity'])
            finished = None if generic and (bad_finished or bad_outcome) else row['finished']
            created = now if generic and bad_created else row['created']
            problem.update(id=visible_id, created_at=created, finished_at=finished,
                           outcome=None if finished is None else row['outcome'], state='finished' if finished is not None else problem['state'])
            (recent if finished is not None else current).append(problem)
        return dict(current=current, recent=recent, count=len(current), updated_at=now)

    @staticmethod
    def header(raw: str | None) -> dict[str,Any]:
        if not raw:
            return {}
        value=json.loads(raw)
        manifest=SourceManifest.model_validate_json(json.dumps(value['source_manifest'])) if value.get('source_manifest') else None
        value['expected_incomplete']=expected_incomplete(manifest)
        return {field:value[field] for field in ('title','full','captions','reason','expected_incomplete') if field in value}
