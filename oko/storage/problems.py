"""Current reader obstacles; identity and recovery are independent of message text."""
import json
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oko.storage.store import Store


class Problems:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        self.db = store.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS reader_problems (
              id TEXT PRIMARY KEY, identity TEXT NOT NULL, origin TEXT NOT NULL,
              body TEXT NOT NULL, created REAL NOT NULL, finished REAL, outcome TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS reader_problems_current
              ON reader_problems(identity) WHERE finished IS NULL;
            CREATE TABLE IF NOT EXISTS reader_action_results (
              sequence INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, identity TEXT NOT NULL,
              body TEXT NOT NULL, started REAL NOT NULL, completed REAL, result TEXT NOT NULL);
        ''')

    def snapshot(self, now: float) -> dict[str, Any]:
        current: list[dict[str, Any]] = []
        scopes = [('oko','global')] + [('feed', feed['id']) for feed in self.store.subscriptions.feeds()]
        needs_codex = any((values := self.store.subscriptions.effective(scope, owner)['effective'])['processor']['value'] == 'codex' and
                         any(values[key]['value'] for key in ('theses', 'prompt_filters')) for scope, owner in scopes)
        if needs_codex and self.store.codex_config() is None:
            current.append(dict(id='processor:codex-choice', code='codex-choice', section='action',
                                title='Не выбрана модель Codex', consequence='Тезисы пока не создаются.',
                                next_step='Выберите и сохраните модель для каждого действия.',
                                objects=[dict(kind='settings', id='global', label='Настройки Око')],
                                action=dict(kind='settings', page='codex'), created_at=now,
                                state='current', finished_at=None, outcome=None, next_at=None))
        groups: dict[str, dict[str, Any]] = {}
        feeds = {feed['id']: feed for feed in self.store.subscriptions.feeds()}
        youtube = self.store.youtube.status(now)
        jobs = self.db.execute("SELECT jobs.*,items.feed_id,items.viewed FROM jobs LEFT JOIN items ON items.id=jobs.item WHERE jobs.status IN ('pending','running') AND (jobs.problem_code IS NOT NULL OR jobs.error IS NOT NULL)").fetchall()
        for job in jobs:
            code = job['problem_code']
            if code == 'codex-choice':
                continue
            if code is None and job['kind'] == 'fetch' and feeds.get(job['feed_id'], {}).get('kind') == 'youtube' and self.store.youtube.ready_at() > now:
                continue
            if code is None and job['kind'] == 'read' and not next((state['error'] for state in self.store.read_sync.statuses() if state['feed_id'] == job['item']), ''):
                continue
            if code == 'youtube-admission':
                continue
            code = code or 'legacy-' + job['kind']
            feed = job['feed_id'] or job['item'] or 'demo'
            if job['viewed'] and job['kind'] != 'delete':
                continue
            shared = code in ('processor-config', 'ai-permission') or code.startswith('source-')
            identity = 'job:' + code + ':' + feed + ':' + job['kind'] + ('' if shared else ':' + job['item'])
            if code == 'processor-config':
                identity = 'processor:configuration'
            settings_action = code in ('processor-config', 'ai-permission')
            obj = dict(kind='feed' if job['kind'] in ('discover', 'read') else 'material', id=feed if job['kind'] in ('discover', 'read') else job['item'])
            if identity not in groups:
                kind = job['kind']
                title = {'fetch':'Не удалось загрузить материал', 'discover':'Не удалось обновить источник',
                         'read':'Не удалось подтвердить чтение в Telegram', 'delete':'Удаление не завершено'}.get(kind, 'Не удалось обработать материал')
                if settings_action:
                    title = 'Требуется настройка обработчика'
                action = dict(kind='settings', page='llamacpp' if code == 'processor-config' else 'models') if settings_action else dict(kind='collect' if kind == 'discover' else 'feed-settings' if kind == 'read' else 'theses-retry' if kind == 'theses' else 'filter-retry' if kind == 'filter' else 'retry', id=obj['id'])
                groups[identity] = dict(id=identity,code=code,section='action' if settings_action else 'object',title=title,
                    consequence='Сохранённое содержимое остаётся доступным.' if kind != 'delete' else 'Око продолжает попытки удаления.',
                    next_step='Око повторит попытку автоматически.' if not settings_action else 'Откройте настройки обработчика.',
                    objects=[],action=None if kind == 'delete' else action,created_at=now,state='retrying' if job['status'] == 'running' or not job['error'] else 'current',finished_at=None,outcome=None,next_at=None)
                if kind != 'delete' and job['error']:
                    groups[identity]['reason'] = job['error']
                if code == 'source-reconnect':
                    groups[identity].update(section='action', next_step='Переподключите источник.', action=dict(kind='feed-settings', id=feed, page='reconnect'))
            if job['kind'] != 'delete' and obj not in groups[identity]['objects']:
                groups[identity]['objects'].append(obj)
        if youtube['paused']:
            objects = [dict(kind='material',id=row['id']) for row in self.db.execute("SELECT items.id FROM items JOIN jobs ON jobs.item=items.id WHERE items.viewed=0 AND jobs.kind='fetch' AND jobs.status IN ('pending','running')").fetchall()
                       if feeds.get(self.store.item_feed(row['id']), {}).get('kind') == 'youtube']
            groups['youtube:admission'] = dict(id='youtube:admission',code='youtube-admission',section='wait',title='YouTube ограничил запросы',
                consequence=f'Ожидают субтитров: {len(objects)}.',next_step='Око повторит получение после паузы.',
                objects=objects,action=dict(kind='objects'),created_at=now,state='current',finished_at=None,outcome=None,next_at=youtube['next_at'])
        affected = {obj['id'] for group in groups.values() for obj in group['objects'] if obj['kind'] == 'material'}
        for row in self.db.execute('SELECT id,feed_id FROM items WHERE viewed=0 AND complete=1').fetchall():
            if row['id'] in affected or self.store.versions.deleted(row['id']):
                continue
            header = self.header(row['id'])
            if header.get('full', True) or (youtube['paused'] and feeds.get(row['feed_id'], {}).get('kind') == 'youtube'):
                continue
            cause = header.get('captions')
            waiting = self.db.execute("SELECT 1 FROM jobs WHERE kind='fetch' AND item=? AND status IN ('pending','running') AND problem_code IS NULL", (row['id'],)).fetchone()
            known_partial = any(any(obj['id'] == row['id'] for obj in json.loads(record['body'])['objects']) for record in self.db.execute("SELECT body FROM reader_problems WHERE finished IS NULL AND json_extract(body,'$.code')='material-incomplete'"))
            if waiting and not known_partial:
                continue
            identity = 'incomplete:' + row['feed_id'] + ':' + (cause if cause in ('missing','error') else row['id'])
            if identity not in groups:
                groups[identity] = dict(id=identity,code='material-incomplete',section='object',title='Материал сохранён не полностью',
                    consequence='Доступная часть остаётся пригодной для чтения.',next_step='Повторите загрузку или откройте доступную часть.',
                    objects=[],action=dict(kind='retry',id=row['id']),created_at=now,state='current',finished_at=None,outcome=None,next_at=None)
                if header.get('reason'):
                    groups[identity]['reason'] = header['reason']
            groups[identity]['objects'].append(dict(kind='material',id=row['id']))
            if self.db.execute("SELECT 1 FROM jobs WHERE kind='fetch' AND item=? AND status IN ('pending','running')", (row['id'],)).fetchone():
                groups[identity]['state'] = 'retrying'
        current.extend(groups.values())
        for receipt in self.db.execute('''SELECT * FROM reader_action_results r WHERE sequence=(
            SELECT max(sequence) FROM reader_action_results WHERE identity=r.identity) AND result IN ('failed','unknown','pending') ''').fetchall():
            if receipt['result'] == 'pending' and not self.db.execute("SELECT 1 FROM reader_problems WHERE identity=? AND finished IS NULL", ('action:' + receipt['identity'],)).fetchone():
                continue
            context = json.loads(receipt['body'])
            unknown = receipt['result'] == 'unknown'
            receipt_action: dict[str, Any] | None = context['action']
            if unknown and receipt_action and receipt_action['kind'] not in ('settings', 'feed-settings', 'material', 'add'):
                receipt_action = None
            current.append(dict(id='action:' + receipt['identity'],code='action-unknown' if unknown else 'action-failed',section='wait' if unknown else 'action',
                title='Результат действия пока неизвестен' if unknown else context['title'],
                consequence='Око сверяет результат; действие не повторяется автоматически.' if unknown else 'Не удалось подтвердить выполнение действия.',
                next_step='Проверьте состояние после восстановления связи.' if unknown else 'Вернитесь к действию, чтобы исправить проблему.',
                objects=context['objects'],action=receipt_action,operation=receipt['identity'],request_id=receipt['id'],
                state='retrying' if receipt['result'] == 'pending' else 'current',created_at=receipt['started'],finished_at=None,outcome=None,next_at=None))
        return self.reconcile(current, now)

    def action_started(self, identity: str, context: dict[str, Any], now: float) -> None:
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO reader_action_results(id,identity,body,started,result) VALUES (?,?,?,?,?)',
                            (identity, context['identity'], json.dumps(context), now, 'pending'))

    def action_finished(self, identity: str, succeeded: bool, now: float) -> None:
        with self.db:
            self.db.execute("UPDATE reader_action_results SET completed=?,result=? WHERE id=? AND result!='abandoned'",
                            (now, 'succeeded' if succeeded else 'failed', identity))

    def report_action(self, identity: str, context: dict[str, Any], result: str, now: float) -> str:
        self.action_started(identity, context, now)
        row = self.db.execute('SELECT * FROM reader_action_results WHERE id=?', (identity,)).fetchone()
        if row['identity'] != context['identity']:
            raise ValueError('Контекст действия не совпадает')
        if row['result'] in ('pending', 'unknown'):
            with self.db:
                self.db.execute('UPDATE reader_action_results SET result=? WHERE id=?', (result, identity))
            return result
        return str(row['result'])

    def abandon(self, identity: str, now: float) -> bool:
        row = self.db.execute("SELECT * FROM reader_problems WHERE id=? AND origin='action' AND finished IS NULL", (identity,)).fetchone()
        if not row:
            return False
        receipt = self.db.execute('SELECT * FROM reader_action_results WHERE identity=? ORDER BY sequence DESC LIMIT 1', (row['identity'][7:],)).fetchone()
        if not receipt or receipt['result'] != 'failed' or not json.loads(receipt['body'])['discardable']:
            return False
        with self.db:
            self.db.execute("UPDATE reader_action_results SET result='abandoned' WHERE id=?", (receipt['id'],))
            self.db.execute("UPDATE reader_problems SET finished=?,outcome='irrelevant' WHERE id=?", (now, identity))
        return True

    def forget_materials(self, members: set[str], now: float) -> None:
        """Deletion wins over keeping an expanded/history row; retain only service facts."""
        for row in self.db.execute('SELECT * FROM reader_problems').fetchall():
            body = json.loads(row['body'])
            before = body['objects']
            body['objects'] = [obj for obj in before if not (obj['kind'] == 'material' and obj['id'] in members)]
            if len(before) == len(body['objects']):
                continue
            body['action'] = None if (body.get('action') or {}).get('id') in members else body.get('action')
            if not body['objects']:
                body['action'] = None
                self.db.execute("UPDATE reader_problems SET body=?,finished=COALESCE(finished,?),outcome='irrelevant' WHERE id=?",
                                (json.dumps(body), now, row['id']))
            else:
                self.db.execute('UPDATE reader_problems SET body=? WHERE id=?', (json.dumps(body), row['id']))
        for row in self.db.execute('SELECT * FROM reader_action_results').fetchall():
            body = json.loads(row['body'])
            before = body['objects']
            body['objects'] = [obj for obj in before if not (obj['kind'] == 'material' and obj['id'] in members)]
            if len(before) != len(body['objects']):
                if not body['objects']:
                    body['action'] = None
                self.db.execute("UPDATE reader_action_results SET body=?,result='abandoned' WHERE id=?", (json.dumps(body), row['id']))

    def reconcile(self, candidates: list[dict[str, Any]], now: float) -> dict[str, Any]:
        """Preserve occurrence identity; completed records never become current on restart."""
        identities = {candidate['id'] for candidate in candidates}
        with self.db:
            for row in self.db.execute("SELECT * FROM reader_problems WHERE finished IS NULL").fetchall():
                if row['identity'] not in identities:
                    body = json.loads(row['body'])
                    irrelevant = body['code'] == 'codex-choice' and self.store.codex_config() is None
                    irrelevant = irrelevant or any(obj['kind'] == 'material' and self.store.versions.deleted(obj['id']) for obj in body['objects'])
                    irrelevant = irrelevant or any(obj['kind'] == 'feed' and not any(feed['id'] == obj['id'] for feed in self.store.subscriptions.feeds()) for obj in body['objects'])
                    self.db.execute('UPDATE reader_problems SET finished=?,outcome=? WHERE id=?', (now, 'irrelevant' if irrelevant else 'resolved', row['id']))
            for candidate in candidates:
                identity = candidate['id']
                body = {key: value for key, value in candidate.items() if key not in ('id', 'created_at', 'finished_at', 'outcome', 'reason')}
                # Object labels are hydrated at read time, never an archive of titles.
                body['objects'] = [{key: value for key, value in obj.items() if key != 'label'} for obj in candidate['objects']]
                row = self.db.execute('SELECT id FROM reader_problems WHERE identity=? AND finished IS NULL', (identity,)).fetchone()
                if row:
                    self.db.execute('UPDATE reader_problems SET body=? WHERE id=?', (json.dumps(body), row['id']))
                else:
                    self.db.execute('INSERT INTO reader_problems VALUES (?,?,?,?,?,NULL,NULL)',
                                    (uuid.uuid4().hex, identity, 'action' if identity.startswith('action:') else 'background', json.dumps(body), now))
            self.db.execute('DELETE FROM reader_problems WHERE finished<=?', (now - 86400,))
            self.db.execute('''DELETE FROM reader_action_results WHERE completed<=? AND sequence NOT IN
                (SELECT max(sequence) FROM reader_action_results GROUP BY identity)''', (now - 86400,))
        current: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        for row in self.db.execute('SELECT * FROM reader_problems ORDER BY created DESC').fetchall():
            problem = json.loads(row['body'])
            if row['finished'] is None:
                candidate = next((p for p in candidates if p['id'] == row['identity']), {})
                if candidate.get('reason'):
                    problem['reason'] = candidate['reason']
            for obj in problem['objects']:
                obj['label'] = self.label(obj)
            problem.update(id=row['id'], created_at=row['created'], finished_at=row['finished'],
                           outcome=row['outcome'], state='finished' if row['finished'] is not None else problem['state'])
            if row['finished'] is not None:
                problem['consequence'] = 'Проблема больше не актуальна.' if row['outcome'] == 'irrelevant' else 'Причина устранена.'
                problem['next_step'] = ''
            (recent if row['finished'] is not None else current).append(problem)
        return dict(current=current, recent=recent, count=len(current), updated_at=now)

    def label(self, obj: dict[str, Any]) -> str:
        if obj['kind'] == 'settings':
            return 'Настройки Око'
        if obj['kind'] == 'feed':
            return next((feed['title'] for feed in self.store.subscriptions.feeds() if feed['id'] == obj['id']), 'Источник недоступен')
        row = self.db.execute('SELECT viewed FROM items WHERE id=?', (obj['id'],)).fetchone()
        if row and row['viewed']:
            return 'Удалённый материал'
        return str(self.header(obj['id']).get('title', 'Материал'))

    def header(self, identity: str) -> dict[str, Any]:
        try:
            with self.store.path(identity).open() as source:
                value = json.loads(source.readline())
                return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}
