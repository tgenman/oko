"""Folder/window event views. Only current input identities survive in SQLite."""
from oko.diagnostics import record_caught
import hashlib
import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

from oko.reader.composition import represented_originals
from oko.processing.contracts import EventsResult, InputSource, ProcessingRequest, ProcessingError

if TYPE_CHECKING:
    from oko.storage.store import Job, Store


def group_identity(members: list[str]) -> str:
    return 'event-' + hashlib.sha256(json.dumps(sorted(members)).encode()).hexdigest()[:32]


class Events:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        (store.root / 'events').mkdir(exist_ok=True)
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS event_inputs (item TEXT PRIMARY KEY, signature TEXT);
        ''')
        for path in (store.root / 'events').glob('*.tmp'):
            path.unlink()

    def path(self, batch: str) -> Path:
        return self.store.root / 'events' / (batch + '.md')

    def candidates(self) -> dict[str, dict[str, Any]]:
        store = self.store
        feeds = {f['id']: f for f in store.subscriptions.feeds()}
        folders: dict[str, list[dict[str, Any]]] = {}
        covered = represented_originals(store.digests.views())
        for row in store.db.execute("""SELECT id FROM items WHERE viewed=0 AND complete=1
                AND NOT EXISTS (SELECT 1 FROM jobs WHERE kind='fetch' AND item=items.id AND status IN ('pending','running'))
                ORDER BY published,id""").fetchall():
            if row['id'] in covered:
                continue
            material = store.material(row['id'])
            if not material or material.get('loading') or material['feed_id'] not in feeds:
                continue
            if feeds[material['feed_id']]['kind'] == 'youtube' and (material.get('captions') != 'available' or not material['display_text'].strip()):
                continue
            filtered = material['filter']
            if filtered['hidden'] or filtered['status'] not in ('off', 'done'):
                continue
            folder = feeds[material['feed_id']]['folder_id']
            folders.setdefault(folder, []).append(material)
        result = {}
        config = store.processor_config()
        for folder, materials in folders.items():
            values = store.subscriptions.effective('folder', folder)['effective']
            if not values['events']['value']:
                continue
            windows: list[list[dict[str, Any]]] = []
            for material in materials:
                if not windows or material['published'] - windows[-1][-1]['published'] > values['event_hours']['value'] * 3600:
                    windows.append([])
                windows[-1].append(material)
            for window in windows:
                if len(window) < 2:
                    continue
                batch = 'event-' + hashlib.sha256((folder + ':' + window[0]['id']).encode()).hexdigest()[:32]
                settings = [store.subscriptions.effective('feed', m['feed_id']) for m in window]
                signature = hashlib.sha256(json.dumps([[(m['id'], m['version'], m['filter'], m['published']) for m in window], settings, values, store.settings_version(), config.model_dump() if config else None], sort_keys=True).encode()).hexdigest()
                result[batch] = dict(signature=signature, materials=window, folder_id=folder, event_hours=values['event_hours']['value'], processor=values['processor']['value'], public_ai=all(feeds[m['feed_id']]['public_ai'] for m in window), importance=max((feeds[m['feed_id']]['importance'] for m in window), key=lambda value: {'normal': 0, 'elevated': 1, 'high': 2}[value]))
        return result

    def signature(self, batch: str) -> str | None:
        candidate = self.candidates().get(batch)
        return candidate['signature'] if candidate else None

    def sync(self) -> None:
        store = self.store
        candidates = self.candidates()
        for row in store.db.execute('SELECT item,signature FROM event_inputs').fetchall():
            candidate = candidates.get(row['item'])
            state = store.db.execute("SELECT status FROM jobs WHERE kind='events' AND item=?", (row['item'],)).fetchone()
            if candidate and candidate['signature'] == row['signature'] and state and (state[0] != 'done' or self.read(row['item'], candidate)):
                candidates.pop(row['item'])
                continue
            self.invalidate(row['item'])
        for batch, candidate in candidates.items():
            with store.db:
                store.db.execute('INSERT INTO event_inputs VALUES (?,?) ON CONFLICT(item) DO UPDATE SET signature=excluded.signature', (batch, candidate['signature']))
                store.db.execute("INSERT INTO jobs(kind,item) VALUES ('events',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=generation+1,next=0,error=NULL", (batch,))
        for path in (store.root / 'events').glob('*.md'):
            if not store.db.execute('SELECT 1 FROM event_inputs WHERE item=?', (path.stem,)).fetchone():
                self.invalidate(path.stem)

    def invalidate(self, batch: str) -> None:
        with self.store.db:
            self.store.db.execute('DELETE FROM event_inputs WHERE item=?', (batch,))
            self.store.db.execute('DELETE FROM state WHERE key=?', ('event-retry:' + batch,))
            self.store.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL WHERE kind='events' AND item=?", (batch,))
            if self.path(batch).exists() or (self.store.root / 'staging' / batch).exists():
                self.store.db.execute("INSERT INTO jobs(kind,item) VALUES ('delete',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',next=0,error=NULL,generation=generation+1 WHERE jobs.status NOT IN ('pending','running')", (batch,))

    def retry(self, identity: str) -> bool:
        material = self.material(identity)
        if material is None:
            return False
        batch = material['batch']
        members = material['event']['members']
        self.store.forget_execution('events', batch)
        with self.store.db:
            self.store.db.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                  ('event-retry:' + batch, json.dumps(members)))
            self.store.db.execute("UPDATE jobs SET status='pending',next=0,error=NULL,generation=generation+1 WHERE kind='events' AND item=?", (batch,))
        return True

    def request(self, job: 'Job') -> tuple[ProcessingRequest, str, bool] | None:
        candidate = self.candidates().get(job.item)
        if not candidate:
            return None
        if len(candidate['materials']) > 100:
            raise ProcessingError('Набор событий превышает 100 публикаций; уменьшите окно или разделите папку')
        selected = self.store.db.execute('SELECT value FROM state WHERE key=?', ('event-retry:' + job.item,)).fetchone()
        if selected:
            members = set(json.loads(selected[0]))
            candidate = dict(candidate, materials=[m for m in candidate['materials'] if m['id'] in members])
        request = ProcessingRequest(operation='events', event_hours=candidate['event_hours'], sources=tuple(InputSource(id=m['id'], version=m['version'], url=m['url'], title=m['title'], text=m['display_text'], published=m['published']) for m in candidate['materials']), settings_version=self.store.settings_version(), prompt='Времена публикаций: ' + json.dumps({m['id']: m['published'] for m in candidate['materials']}), processor=candidate['processor'], generation=job.generation)
        return request, candidate['signature'], candidate['public_ai']

    def read(self, batch: str, candidate: dict[str, Any]) -> EventsResult | None:
        row = self.store.db.execute("SELECT status FROM jobs WHERE kind='events' AND item=?", (batch,)).fetchone()
        if not row:
            return None
        if row[0] != 'done' and not self.store.db.execute('SELECT 1 FROM state WHERE key=?', ('event-retry:' + batch,)).fetchone():
            return None  # An explicit retry retains accepted groups until success.
        try:
            data = json.loads(self.path(batch).read_text().split('\n\n', 1)[0])
            if data['signature'] != candidate['signature']:
                return None
            return EventsResult.model_validate(data['result'])
        except (OSError, ValueError, KeyError):
            record_caught('events.read')
            return None

    def save(self, job: 'Job', signature: str, result: EventsResult) -> None:
        if not self.store.valid(job) or signature != self.signature(job.item):
            return
        request = self.request(job)
        if not request:
            return
        result.validate_sources(request[0])
        origin = self.store.execution_provenance(job) or dict(model=None)
        origins = {group_identity(g.members): origin for g in result.groups}
        selected = self.store.db.execute('SELECT value FROM state WHERE key=?', ('event-retry:' + job.item,)).fetchone()
        if selected:
            members = set(json.loads(selected[0]))
            header = json.loads(self.path(job.item).read_text().split('\n\n', 1)[0])
            if header['signature'] != signature:
                return
            previous = EventsResult.model_validate(header['result'])
            for group in previous.groups:
                if not members.intersection(group.members):
                    identity = group_identity(group.members)
                    origins[identity] = header.get('event_origins', {}).get(identity) or header.get('provenance') or dict(model=None)
            result = EventsResult(groups=[g for g in previous.groups if not members.intersection(g.members)] + result.groups)
            candidate = self.candidates()[job.item]
            originals = tuple(InputSource(id=m['id'], version=m['version'], url=m['url'], title=m['title'], text=m['display_text'], published=m['published']) for m in candidate['materials'])
            result.validate_sources(request[0].model_copy(update={'sources': originals}))
        self.store.save_derivative(job, signature, result.model_dump(), '\n'.join(g.title for g in result.groups), event_origins=origins)
        with self.store.db:
            self.store.db.execute('DELETE FROM state WHERE key=?', ('event-retry:' + job.item,))

    def views(self) -> list[dict[str, Any]]:
        # Candidate discovery reads every publication. A reader only needs it
        # when an accepted result (including one retained during retry) exists.
        if not self.store.db.execute("""SELECT 1 FROM jobs WHERE kind='events' AND
                (status='done' OR EXISTS (SELECT 1 FROM state WHERE key='event-retry:' || jobs.item))
                LIMIT 1""").fetchone():
            return []
        views = []
        feeds = {f['id']: f for f in self.store.subscriptions.feeds()}
        for batch, candidate in self.candidates().items():
            result = self.read(batch, candidate)
            if not result:
                continue
            for group in result.groups:
                if len(group.members) < 2:
                    continue
                sources = [m for m in candidate['materials'] if m['id'] in group.members]
                identity = group_identity(group.members)
                views.append(dict(id=identity, batch=batch, provenance=self.store.derivative_provenance(self.path(batch), candidate['signature'], identity), event=group.model_dump(), sources=sources, folder_id=candidate['folder_id'], feed_id=sources[0]['feed_id'], importance=max((feeds[m['feed_id']]['importance'] for m in sources), key=lambda value: {'normal': 0, 'elevated': 1, 'high': 2}[value]), title=group.title, published=max(m['published'] for m in sources), url=sources[0]['url'], complete=True, text='', version=candidate['signature']))
        return views

    def material(self, identity: str) -> dict[str, Any] | None:
        return next((v for v in self.views() if v['id'] == identity), None)

