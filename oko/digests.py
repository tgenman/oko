"""Frozen source sets and resumable, bounded period reviews."""
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .processing import ProcessingError, Point, InputSource, ProcessingRequest, Theses

if TYPE_CHECKING:
    from .store import Job, Store


class DigestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)
    period: bool = False

    @model_validator(mode='after')
    def ordered(self) -> 'DigestRequest':
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError('Начало периода позже окончания')
        return self


class Progress(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    parts: int = Field(ge=0)
    points: list[Point]
    complete: bool = False
    reduce_cursor: int = 0
    reduced: list[Point] = Field(default_factory=list)
    rounds: int = 0


def review_groups(points: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    size = 0
    refs: set[str] = set()
    for point in points:
        point_size = len(json.dumps(point, ensure_ascii=False))
        point_refs = {r['id'] for r in point['sources']}
        if not groups or size + point_size > 50000 or len(refs | point_refs) > 100:
            groups.append([])
            size, refs = 0, set()
        groups[-1].append(point)
        size += point_size
        refs |= point_refs
    return groups


class Digests:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        (store.root / 'digests').mkdir(exist_ok=True)
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS digest_sets (
          id TEXT PRIMARY KEY, scope TEXT, owner TEXT, members TEXT, signature TEXT);
        ''')
        for path in (store.root / 'digests').glob('*.tmp'):
            path.unlink()

    def path(self, identity: str) -> Path:
        return self.store.root / 'digests' / (identity + '.md')

    def create(self, scope: str, owner: str, body: DigestRequest, now: float, slot: str | None = None, allowed_feeds: set[str] | None = None) -> str:
        self.store.subscriptions.check_owner(scope, owner)
        if scope not in ('folder', 'feed'):
            raise ValueError('Выберите папку или фид')
        values = self.store.subscriptions.effective(scope, owner)['effective']
        start = body.start if body.start is not None else now - values['digest_days']['value'] * 86400 if body.period else None
        end = body.end if body.end is not None else now if body.period else None
        feeds = {f['id']: f for f in self.store.subscriptions.feeds()}
        members = []
        for row in self.store.db.execute('SELECT id,feed_id,published FROM items WHERE viewed=0 ORDER BY published,id').fetchall():
            feed = feeds.get(row['feed_id'])
            if allowed_feeds is not None and row['feed_id'] not in allowed_feeds:
                continue
            if not feed or (feed['folder_id'] if scope == 'folder' else feed['id']) != owner:
                continue
            if start is not None and row['published'] < start or end is not None and row['published'] > end:
                continue
            material = self.store.material(row['id'])
            if not material or not material['complete'] or material.get('loading'):
                raise ValueError('Дождитесь загрузки всех материалов выбранного периода')
            members.append([material['id'], material['version']])
        if not members:
            raise ValueError('В выбранном периоде нет непросмотренных материалов')
        identity = 'digest-' + uuid.uuid4().hex
        with self.store.db:
            self.store.db.execute('INSERT INTO digest_sets VALUES (?,?,?,?,NULL)', (identity, scope, owner, json.dumps(members)))
            self.store.db.execute("INSERT INTO jobs(kind,item) VALUES ('digest',?)", (identity,))
            if slot is not None:
                self.store.db.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', ('digest-tick:'+scope+':'+owner, slot))
        self.sync()
        return identity

    def schedule(self, now: float) -> None:
        # Inherited settings produce folder reviews. Feed overrides produce a
        # separate feed review and are excluded from that folder's scheduled set.
        targets: dict[tuple[str, str], set[str]] = {}
        fields = {'digests', 'prompt_digest', 'digest_days', 'schedule', 'processor'}
        for feed in self.store.subscriptions.feeds():
            settings = self.store.subscriptions.effective('feed', feed['id'])
            values = settings['effective']
            if not values['digests']['value']:
                continue
            target = ('feed', feed['id']) if fields & settings['overrides'].keys() else ('folder', feed['folder_id'])
            targets.setdefault(target, set()).add(feed['id'])
        local = datetime.fromtimestamp(now)
        for (scope, owner), feeds in targets.items():
            values = self.store.subscriptions.effective(scope, owner)['effective']
            schedule = values['schedule']['value']
            if schedule == 'off':
                continue
            hour, minute = map(int, schedule.split(':'))
            due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if due > local:
                due -= timedelta(days=1)
            slot = due.isoformat()
            old = self.store.db.execute('SELECT value FROM state WHERE key=?', ('digest-tick:'+scope+':'+owner,)).fetchone()
            if old and old[0] >= slot:
                continue
            try:
                self.create(scope, owner, DigestRequest(period=True), now, slot, feeds)
            except ValueError:
                # Empty or still loading: keep the tick due until collection completes.
                continue

    def candidate(self, identity: str) -> dict[str, Any] | None:
        row = self.store.db.execute('SELECT * FROM digest_sets WHERE id=?', (identity,)).fetchone()
        if not row:
            return None
        materials = []
        for member, version in json.loads(row['members']):
            material = self.store.material(member)
            if not material or not material['complete'] or material.get('loading') or material['version'] != version:
                return None
            materials.append(material)
        values = self.store.subscriptions.effective(row['scope'], row['owner'])['effective']
        feeds = {f['id']: f for f in self.store.subscriptions.feeds()}
        settings = [self.store.subscriptions.effective('feed', m['feed_id']) for m in materials]
        config = self.store.processor_config()
        signature = hashlib.sha256(json.dumps([row['members'], values, settings, [m['filter'] for m in materials], config.model_dump() if config else None], sort_keys=True).encode()).hexdigest()
        return dict(materials=materials, signature=signature, values=values, public_ai=all(feeds[m['feed_id']]['public_ai'] for m in materials), folder_id=row['owner'] if row['scope'] == 'folder' else feeds[materials[0]['feed_id']]['folder_id'], scope=row['scope'], owner=row['owner'], importance=max((feeds[m['feed_id']]['importance'] for m in materials), key=lambda v: {'normal': 0, 'elevated': 1, 'high': 2}[v]))

    def signature(self, identity: str) -> str | None:
        candidate = self.candidate(identity)
        return candidate['signature'] if candidate else None

    def invalidate(self, identity: str) -> None:
        with self.store.db:
            self.store.db.execute('DELETE FROM digest_sets WHERE id=?', (identity,))
            self.store.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL WHERE kind='digest' AND item=?", (identity,))
            if self.path(identity).exists() or (self.store.root / 'staging' / identity).exists():
                self.store.db.execute("INSERT INTO jobs(kind,item) VALUES ('delete',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',next=0,error=NULL,generation=generation+1 WHERE jobs.status NOT IN ('pending','running')", (identity,))

    def read(self, identity: str, signature: str) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path(identity).read_text().split('\n\n', 1)[0])
            if data['signature'] == signature:
                return Progress.model_validate(data['result']).model_dump()
        except (OSError, ValueError, KeyError):
            pass
        return None

    def sync(self) -> None:
        for row in self.store.db.execute('SELECT * FROM digest_sets').fetchall():
            candidate = self.candidate(row['id'])
            if not candidate:
                self.invalidate(row['id'])
                continue
            state = self.store.db.execute("SELECT status FROM jobs WHERE kind='digest' AND item=?", (row['id'],)).fetchone()
            missing = state and state[0] == 'done' and self.read(row['id'], candidate['signature']) is None
            if candidate['signature'] != row['signature'] or missing:
                with self.store.db:
                    self.store.db.execute('UPDATE digest_sets SET signature=? WHERE id=?', (candidate['signature'], row['id']))
                    self.store.db.execute("UPDATE jobs SET status='pending',next=0,generation=generation+1,error=NULL WHERE kind='digest' AND item=?", (row['id'],))
        for path in (self.store.root / 'digests').glob('*.md'):
            if not self.store.db.execute('SELECT 1 FROM digest_sets WHERE id=?', (path.stem,)).fetchone():
                self.invalidate(path.stem)

    def request(self, job: 'Job') -> tuple[ProcessingRequest, str, bool] | None:
        candidate = self.candidate(job.item)
        if not candidate or any(m['filter']['status'] not in ('off', 'done') for m in candidate['materials']):
            return None
        sources = []
        feeds = {f['id']: f for f in self.store.subscriptions.feeds()}
        for m in candidate['materials']:
            if m['filter']['hidden'] or feeds[m['feed_id']]['kind'] == 'youtube' and m.get('captions') != 'available':
                continue
            text = m['display_text']
            for offset in range(0, max(1, len(text)), 40000):
                sources.append(InputSource(id=m['id'], version=m['version'], url=m['url'], title=m['title'], text=text[offset:offset+40000], published=m['published']))
        chunks: list[list[InputSource]] = []
        for source in sources:
            if not chunks or len(chunks[-1]) >= 10 or sum(len(s.text)+len(s.title) for s in chunks[-1])+len(source.text)+len(source.title) > 60000:
                chunks.append([])
            chunks[-1].append(source)
        data = self.read(job.item, candidate['signature']) or Progress(parts=0, points=[]).model_dump()
        part = data['parts']
        operation: Literal['digest', 'digest_reduce', 'digest_final'] = 'digest'
        prompt = candidate['values']['prompt_digest']['value']
        chunk = chunks[part] if part < len(chunks) else []
        if not data['complete'] and part >= len(chunks) and len(chunks) > 1:
            groups = review_groups(data['points'])
            if data['rounds'] >= 8:
                raise ProcessingError('Не удалось сжать промежуточные результаты; измените промпт или период')
            selected = groups[data['reduce_cursor']] if len(groups) > 1 else groups[0]
            refs = {r['id'] for point in selected for r in point['sources']}
            chunk = [InputSource(id=m['id'], version=m['version'], url=m['url'], title=m['title'], text='', published=m['published']) for m in candidate['materials'] if m['id'] in refs]
            chunk[0] = chunk[0].model_copy(update={'text': json.dumps(selected, ensure_ascii=False)})
            operation = 'digest_reduce' if len(groups) > 1 else 'digest_final'
            if operation == 'digest_reduce':
                prompt = 'Сожми промежуточные темы максимум в десять тезисов с точными ссылками; сохрани противоречия и атрибуцию.'
        request = ProcessingRequest(operation=operation, sources=tuple(chunk), settings_version=self.store.settings_version(), prompt=prompt, processor=candidate['values']['processor']['value'], generation=job.generation)
        return request, candidate['signature'], candidate['public_ai']

    def save(self, job: 'Job', signature: str, result: Theses | None) -> None:
        if not self.store.valid(job) or signature != self.signature(job.item):
            return
        prepared = self.request(job)
        if not prepared:
            return
        data = self.read(job.item, signature) or Progress(parts=0, points=[]).model_dump()
        if result:
            result.validate_sources(prepared[0])
            points = [p.model_dump() for p in result.points]
            if prepared[0].operation == 'digest_final':
                data['points'] = points
                data['complete'] = True
            elif prepared[0].operation == 'digest_reduce':
                data['reduced'].extend(points)
                data['reduce_cursor'] += 1
                # Reconstructing the next request detects completion of this round.
                groups = review_groups(data['points'])
                if data['reduce_cursor'] == len(groups):
                    data['points'] = data['reduced']
                    data['reduced'] = []
                    data['reduce_cursor'] = 0
                    data['rounds'] += 1
            else:
                data['points'].extend(points)
                data['parts'] += 1
        else:
            data['complete'] = True
        # Partial results remain inaccessible; all stages use the atomic writer.
        self.store.save_derivative(job, signature, data, '\n'.join('- '+p['text'] for p in data['points']), finish=data['complete'])
        if not data['complete']:
            with self.store.db:
                self.store.db.execute("UPDATE jobs SET status='pending',next=0 WHERE id=? AND generation=?", (job.id, job.generation))
        else:
            candidate = self.candidate(job.item)
            if candidate is None:
                return
            members = {m['id'] for m in candidate['materials']}
            for view in self.views():
                if view['id'] != job.item and members & {m['id'] for m in view['sources']}:
                    self.invalidate(view['id'])
            self.store.events.sync()

    def views(self) -> list[dict[str, Any]]:
        views = []
        for row in self.store.db.execute("SELECT digest_sets.id FROM digest_sets JOIN jobs ON jobs.item=digest_sets.id AND jobs.kind='digest' WHERE jobs.status='done'").fetchall():
            candidate = self.candidate(row['id'])
            if not candidate:
                continue
            data = self.read(row['id'], candidate['signature'])
            if data is None:
                continue
            sources = candidate['materials']
            views.append(dict(id=row['id'], digest=dict(points=data['points']), sources=sources, folder_id=candidate['folder_id'], feed_id=sources[0]['feed_id'], importance=candidate['importance'], title='Обзор за период', published=max(m['published'] for m in sources), url=sources[0]['url'], complete=True, text='', version=candidate['signature']))
        return views

    def material(self, identity: str) -> dict[str, Any] | None:
        return next((v for v in self.views() if v['id'] == identity), None)
