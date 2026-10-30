"""Current reader obstacles; identity and recovery are independent of message text."""
from oko.storage.database import expect_row
import json
from oko.collection.contracts import SourceManifest
from oko.collection.external_media import expected_incomplete
from oko.storage.reading import reading_snapshot
from oko.storage import problem_links
from oko.storage.reader_status import changed as status_changed
from dataclasses import dataclass
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oko.storage.store import Store


@dataclass(frozen=True)
class CommandRecord:
    identity: str
    context_json: str | None
    error_json: str | None
    result: str
    updated: float


class CommandContextMismatch(ValueError):
    pass


class Problems:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        self.db = store.db
        self._receipt_cleanup_cursor: tuple[float, int] | None = None
        # Recovery projection only: preserve the original service records for rollback.
        self._startup_pending = {row[0] for row in self.db.execute("SELECT id FROM reader_action_results WHERE result = 'pending'")}

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

    def candidates(self, now: float) -> list[dict[str, Any]]:
        current: list[dict[str, Any]] = []
        configurations=[self.store.subscriptions.effective('oko','global'),*self.store.subscriptions.effective_feeds().values()]
        needs_codex = any((values := configuration['effective'])['processor']['value'] == 'codex' and
                         any(values[key]['value'] for key in ('theses', 'prompt_filters')) for configuration in configurations)
        if needs_codex and self.store.codex_config() is None:
            current.append(dict(id='processor:codex-choice', code='codex-choice', section='action',
                                title='Не выбрана модель Codex', consequence='Тезисы пока не создаются.',
                                next_step='Выберите и сохраните модель для каждого действия.',
                                objects=[dict(kind='settings', id='global', label='Настройки Око')],
                                action=None, created_at=now,
                                state='current', finished_at=None, outcome=None, next_at=None))
        groups: dict[str, dict[str, Any]] = {}
        feeds = {feed['id']: feed for feed in self.store.subscriptions.feeds()}
        bindings=self.store.subscriptions.telegram.bindings(list(feeds))
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
        youtube = self.store.youtube.status(now)
        read_states = {state['feed_id']: state for state in self.store.read_sync.statuses()}
        jobs = self.db.execute("SELECT jobs . * , coalesce ( items . feed_id , media_owner . feed_id , linked_root . feed_id ) AS feed_id , coalesce ( items . viewed , media_owner . viewed ) AS viewed , m . resource_key AS resource_key , coalesce ( linked_root . id , m . publication_id , jobs . item ) AS affected_item FROM jobs LEFT JOIN items_read items ON items . id = jobs . item LEFT JOIN media_files m ON jobs . kind = 'download_media' AND m . id = jobs . item LEFT JOIN items_read media_owner ON media_owner . id = m . publication_id LEFT JOIN publication_links link ON link . target_publication_id = coalesce ( items . id , media_owner . id ) LEFT JOIN items_read linked_root ON linked_root . id = link . source_publication_id AND linked_root . viewed = 0 WHERE ( jobs . status IN ( 'pending' , 'running' , 'failed' , 'unavailable' , 'blocked' ) OR ( jobs . status = 'done' AND jobs . kind = 'fetch' AND jobs . problem_code IS NOT NULL AND jobs . error IS NOT NULL ) ) AND ( jobs . problem_code IS NOT NULL OR jobs . error IS NOT NULL )").fetchall()
        for job in jobs:
            code = job['problem_code']
            if code in ('codex-choice', 'ai-permission'):
                continue
            if job['kind']=='download_media' and job['resource_key']=='youtube:subtitles':
                if youtube['paused'] or code is None and self.store.youtube.ready_at()>now:
                    continue
            if code is None and job['kind'] == 'fetch' and feeds.get(job['feed_id'], {}).get('kind') == 'youtube' and self.store.youtube.ready_at() > now:
                continue
            if code is None and job['kind'] == 'read' and not read_states.get(job['item'], {}).get('error', ''):
                continue
            if code == 'youtube-admission':
                continue
            code = code or 'work-' + job['kind']
            feed = job['feed_id'] or job['item'] or 'demo'
            if code in ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch') and any(identity.startswith('profile:') and any(obj['id']==feed for obj in group['objects']) for identity,group in groups.items()):
                continue
            if job['viewed'] and job['kind'] not in ('delete','feed-delete'):
                continue
            shared = code in ('processor-config', 'ai-permission') or code.startswith('source-')
            identity = 'job:' + code + ':' + feed + ':' + job['kind'] + ('' if shared else ':' + job['item'])
            if code == 'processor-config':
                identity = 'processor:configuration'
            settings_action = code in ('processor-config', 'ai-permission')
            obj = dict(kind='feed' if job['kind'] in ('discover', 'read', 'feed-delete') else 'material', id=feed if job['kind'] in ('discover', 'read', 'feed-delete') else job['affected_item'])
            if identity not in groups:
                kind = job['kind']
                title = {'feed-delete':'Удаление фида не завершено', 'fetch':'Не удалось загрузить материал', 'download_media':'Не удалось загрузить вложение', 'discover':'Не удалось обновить источник',
                         'read':'Не удалось подтвердить чтение в Telegram', 'delete':'Удаление не завершено'}.get(kind, 'Не удалось обработать материал')
                if settings_action:
                    title = 'Требуется настройка обработчика'
                action = None
                groups[identity] = dict(id=identity,code=code,section='action' if settings_action else 'object',title=title,
                    consequence='Сохранённое содержимое остаётся доступным.' if kind not in ('delete','feed-delete') else 'Око продолжает попытки удаления.',
                    next_step='Включите обработку, чтобы повторить попытку.' if job['status']=='blocked' else 'Повторите загрузку вручную.' if job['status'] in ('done','failed') else 'Проверьте доступность источника.' if job['status']=='unavailable' else 'Око повторит попытку автоматически.' if not settings_action else 'Откройте настройки обработчика.',
                    objects=[],action=action,operation='producer:'+kind,created_at=now,state='retrying' if job['status'] == 'running' or not job['error'] else 'current',finished_at=None,outcome=None,next_at=None)
                if kind != 'delete' and job['error']:
                    groups[identity]['reason'] = job['error']
                if code == 'source-reconnect':
                    groups[identity].update(section='action', next_step='Проверьте привязку источника; доступ не изменяется автоматически.', action=None)
            if job['kind'] != 'delete' and obj not in groups[identity]['objects']:
                groups[identity]['objects'].append(obj)
        if youtube['paused']:
            owners={job['affected_item'] for job in jobs if job['status'] in ('pending','running')
                and feeds.get(job['feed_id'],{}).get('kind')=='youtube'
                and (job['kind']=='fetch' or job['resource_key']=='youtube:subtitles')}
            objects=[dict(kind='material',id=owner) for owner in sorted(owners)]
            groups['youtube:admission'] = dict(id='youtube:admission',code='youtube-admission',section='wait',title='YouTube ограничил запросы',
                consequence=f'Ожидают субтитров: {len(objects)}.',next_step='Око повторит получение после паузы.',
                objects=objects,action=None,created_at=now,state='retrying',finished_at=None,outcome=None,next_at=youtube['next_at'])
        affected = {obj['id'] for group in groups.values() for obj in group['objects'] if obj['kind'] == 'material'}
        known_partial = {obj['id'] for record in self.db.execute("SELECT body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code' = 'material-incomplete'") for obj in json.loads(record['body'])['objects']}
        saved = self.db.execute("SELECT i . id , i . feed_id , i.content::jsonb#>>'{extra,_reader}' AS header , f . status AS fetch_status , f . problem_code AS fetch_code , EXISTS ( SELECT 1 FROM media_files m WHERE m . publication_id = i . id AND EXISTS ( SELECT 1 FROM jobs j WHERE j . item = m . id AND j . kind = 'download_media' AND j . status IN ( 'pending' , 'running' ) AND j . error IS NULL AND j . problem_code IS NULL ) ) AS media_waiting , CASE WHEN i . platform = 'youtube' THEN EXISTS ( SELECT 1 FROM media_files m JOIN jobs j ON j . item = m . id AND j . kind = 'download_media' WHERE m . publication_id = i . id AND m . resource_key = 'youtube:subtitles' AND j . status = 'pending' AND j . problem_code IS NULL ) ELSE FALSE END AS caption_waiting FROM items_read i LEFT JOIN jobs f ON f . item = i . id AND f . kind = 'fetch' WHERE i . viewed = 0 AND i . feed_id IS NOT NULL AND i . complete = 1 AND NOT i . source_deleted AND CASE WHEN jsonb_typeof(i.content::jsonb#>'{extra,_reader,full}')='boolean' THEN NOT (i.content::jsonb#>>'{extra,_reader,full}')::boolean ELSE (i.content::jsonb#>>'{extra,_reader,full}')::integer=0 END").fetchall()
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
        for receipt in self.db.execute("SELECT * FROM reader_action_results r WHERE sequence = ( SELECT max ( sequence ) FROM reader_action_results WHERE identity = r . identity ) AND result IN ( 'failed' , 'unknown' , 'pending' ) ").fetchall():
            if receipt['result'] == 'pending' and receipt['id'] not in self._startup_pending and not self.db.execute('SELECT 1 FROM reader_problems WHERE identity = %s AND finished IS NULL', ('action:' + receipt['identity'],)).fetchone():
                continue
            context = json.loads(receipt['body'])
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
            current.append(dict(id='action:' + receipt['identity'],code='action-unknown' if unknown else 'action-failed',section='wait' if unknown else 'action',
                title='Результат действия пока неизвестен' if unknown else context['title'],
                consequence='Око сверяет результат; действие не повторяется автоматически.' if unknown else 'Не удалось подтвердить выполнение действия.',
                next_step='Проверьте состояние после восстановления связи.' if unknown else 'Вернитесь к действию, чтобы исправить проблему.',
                objects=context['objects'],action=receipt_action,operation=operation,request_id=receipt['id'],
                state='retrying' if receipt['result'] == 'pending' and not unknown else 'current',created_at=receipt['started'],finished_at=None,outcome=None,next_at=None))
        return current

    def command_record(self, identity: str) -> CommandRecord | None:
        row = self.db.execute('SELECT * FROM reader_action_results WHERE id = %s', (identity,)).fetchone()
        if row is None:
            return None
        body = json.loads(row['body'])
        return CommandRecord(row['identity'], json.dumps(body['command_context']) if 'command_context' in body else None,
                             body.get('command_error'), 'unknown' if row['result']=='pending' and identity in self._startup_pending else row['result'], row['completed'] or row['started'])

    def command_error(self, identity: str, error_json: str) -> None:
        row = self.db.execute('SELECT body FROM reader_action_results WHERE id = %s', (identity,)).fetchone()
        if row:
            body = json.loads(row['body'])
            body['command_error'] = error_json
            with self.db:
                self.db.execute('UPDATE reader_action_results SET body = %s WHERE id = %s', (json.dumps(body), identity))

    def action_started(self, identity: str, context: dict[str, Any], now: float, *, restart: bool = False, expected_identity: str | None = None) -> None:
        with self.db:
            prior = self.db.execute('SELECT identity,result FROM reader_action_results WHERE id = %s', (identity,)).fetchone()
            if restart and prior and prior['identity'] != (expected_identity or context['identity']):
                raise CommandContextMismatch('Контекст команды не совпадает')
            if restart:
                self._startup_pending.discard(identity)
            inserted = self.db.execute('INSERT INTO reader_action_results ( id , identity , body , started , result ) VALUES ( %s , %s , %s , %s , %s ) ON CONFLICT DO NOTHING',
                            (identity, context['identity'], json.dumps(context), now, 'pending')).rowcount
            if inserted or restart and prior and prior['result'] != 'pending':
                status_changed(self.db)
            if restart:
                self.db.execute("UPDATE reader_action_results SET body = %s , started = %s , completed = NULL , result = 'pending' WHERE id = %s", (json.dumps(context), now, identity))

    def action_finished(self, identity: str, succeeded: bool, now: float) -> None:
        with self.db:
            result = 'succeeded' if succeeded else 'failed'
            prior = self.db.execute('SELECT result FROM reader_action_results WHERE id = %s', (identity,)).fetchone()
            self.db.execute("UPDATE reader_action_results SET completed = %s , result = %s WHERE id = %s AND result != 'abandoned'", (now, result, identity))
            if prior and prior['result'] not in ('abandoned', result):
                status_changed(self.db)

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
            return result
        return str(row['result'])

    def abandon(self, identity: str, now: float) -> bool:
        row = self.db.execute("SELECT * FROM reader_problems WHERE id = %s AND origin = 'action' AND finished IS NULL", (identity,)).fetchone()
        if not row:
            return False
        receipt = self.db.execute('SELECT * FROM reader_action_results WHERE identity = %s ORDER BY sequence DESC LIMIT 1', (row['identity'][7:],)).fetchone()
        if not receipt or receipt['result'] != 'failed' or not json.loads(receipt['body'])['discardable']:
            return False
        with self.db:
            self.db.execute("UPDATE reader_action_results SET result = 'abandoned' WHERE id = %s", (receipt['id'],))
            status_changed(self.db)
            self.db.execute("UPDATE reader_problems SET finished = %s , outcome = 'irrelevant' WHERE id = %s", (now, identity))
        return True

    def forget_materials(self, members: set[str], now: float) -> None:
        """Deletion wins over keeping an expanded/history row; retain only service facts."""
        for row in problem_links.material_records(self.db, 'problem', members):
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
        by_identity = {candidate['id']: candidate for candidate in candidates}
        identities = set(by_identity)
        feeds = feeds if feeds is not None else {f['id']: f for f in self.store.subscriptions.feeds()}
        existing = {row['identity']: row for row in self.db.execute("SELECT * FROM reader_problems WHERE finished IS NULL")}
        with self.db:
            added: list[tuple[str,str,str,str,float]] = []
            changed = False
            for row in existing.values():
                if row['identity'] not in identities:
                    body = json.loads(row['body'])
                    irrelevant = body['code'] == 'codex-choice' and self.store.codex_config() is None
                    irrelevant = irrelevant or any(obj['kind'] == 'material' and self.store.publications.source_deleted(obj['id']) for obj in body['objects'])
                    irrelevant = irrelevant or any(obj['kind'] == 'feed' and feeds.get(obj['id'], {}).get('connection', 'deleting') == 'deleting' for obj in body['objects'])
                    self.db.execute('UPDATE reader_problems SET finished = %s , outcome = %s WHERE id = %s', (now, 'irrelevant' if irrelevant else 'resolved', row['id']))
                    changed = True
            for candidate in candidates:
                if not candidate['id'].startswith('action:'):
                    candidate.setdefault('operation','producer:status')
                identity = candidate['id']
                body = {key: value for key, value in candidate.items() if key not in ('id', 'created_at', 'finished_at', 'outcome')}
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
        for row in self.db.execute('SELECT * FROM reader_problems WHERE finished IS NULL OR finished > %s ORDER BY created DESC', (now - 86400,)).fetchall():
            problem = json.loads(row['body'])
            if row['finished'] is None:
                candidate = by_identity.get(row['identity'], {})
                if candidate.get('reason'):
                    problem['reason'] = candidate['reason']
            for obj in problem['objects']:
                obj.pop('label',None)
            problem.update(id=row['id'], created_at=row['created'], finished_at=row['finished'],
                           outcome=row['outcome'], state='finished' if row['finished'] is not None else problem['state'])
            if row['finished'] is not None:
                pass  # Keep the recorded consequence and cause; outcome owns completion.
            (recent if row['finished'] is not None else current).append(problem)
        return dict(current=current, recent=recent, count=len(current), updated_at=now)

    @staticmethod
    def header(raw: str | None) -> dict[str,Any]:
        if not raw:
            return {}
        value=json.loads(raw)
        manifest=SourceManifest.model_validate_json(json.dumps(value['source_manifest'])) if value.get('source_manifest') else None
        value['expected_incomplete']=expected_incomplete(manifest)
        return {field:value[field] for field in ('title','full','captions','reason','expected_incomplete') if field in value}
