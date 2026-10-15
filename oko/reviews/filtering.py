"""Durable filtered views; originals and media remain owned by Store."""
from oko.diagnostics import record_caught
import hashlib
import json
from typing import Any, TYPE_CHECKING

from oko.processing.contracts import FilterResult, InputSource, ProcessingRequest

if TYPE_CHECKING:
    from oko.storage.store import Job, Store


class Filters:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS filter_inputs (item TEXT PRIMARY KEY, signature TEXT);
        CREATE TABLE IF NOT EXISTS filter_restores (item TEXT PRIMARY KEY, version TEXT);
        ''')

    def signature(self, item: str) -> str | None:
        store = self.store
        row = store.db.execute('SELECT feed_id FROM items WHERE id=? AND viewed=0 AND complete=1', (item,)).fetchone()
        if not row or not store.path(item).is_file():
            return None
        settings = store.subscriptions.effective('feed', row['feed_id'])
        # Retired public metadata must not invalidate already accepted results.
        # This null field was part of the persisted processing signature.
        settings['feed']['telegram_topic'] = None
        if not settings['effective']['prompt_filters']['value'].strip():
            return None
        config = store.processor_config()
        return hashlib.sha256(json.dumps([store.content_version(item), settings, store.settings_version(), config.model_dump() if config else None], sort_keys=True).encode()).hexdigest()

    def sync(self) -> None:
        store = self.store
        for row in store.db.execute('SELECT id FROM items WHERE viewed=0').fetchall():
            item = row['id']
            signature = self.signature(item)
            old = store.db.execute('SELECT signature FROM filter_inputs WHERE item=?', (item,)).fetchone()
            state = store.db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=?", (item,)).fetchone()
            if signature is None and old is None and (not state or state[0] == 'cancelled') and not (store.path(item).parent / 'filter.md').exists():
                continue
            if old and old[0] == signature and state and (state[0] != 'done' or self.read(item).get('status') == 'done'):
                continue
            (store.path(item).parent / 'filter.md').unlink(missing_ok=True)
            with store.db:
                store.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL WHERE kind='filter' AND item=?", (item,))
                if signature:
                    store.db.execute('INSERT INTO filter_inputs VALUES (?,?) ON CONFLICT(item) DO UPDATE SET signature=excluded.signature', (item, signature))
                    store.db.execute("INSERT INTO jobs(kind,item) VALUES ('filter',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=generation+1,next=0,error=NULL", (item,))
                else:
                    store.db.execute('DELETE FROM filter_inputs WHERE item=?', (item,))

    def read(self, item: str) -> dict[str, Any]:
        signature = self.signature(item)
        if not signature:
            return dict(status='off', hidden=False, restored=False, reason='', removed=[])
        store = self.store
        override = store.db.execute('SELECT version FROM filter_restores WHERE item=?', (item,)).fetchone()
        restored = bool(override and override[0] == store.content_version(item))
        row = store.db.execute("SELECT status,error FROM jobs WHERE kind='filter' AND item=?", (item,)).fetchone()
        if row and row['status'] == 'done':
            try:
                data = json.loads((store.path(item).parent / 'filter.md').read_text().split('\n\n', 1)[0])
                if data['signature'] == signature:
                    result = FilterResult.model_validate(data['result']).model_dump()
                    return dict(result, hidden=False if restored else result['hidden'], restored=restored, status='done', error=None)
            except (OSError, ValueError, KeyError):
                record_caught('filtering.read')
                pass
        return dict(status='pending' if not row or row['status'] == 'done' else row['status'], error=row['error'] if row else None, hidden=False, restored=restored, reason='', removed=[])

    def request(self, job: 'Job') -> tuple[ProcessingRequest, str, bool] | None:
        store = self.store
        signature = self.signature(job.item)
        material = store.material(job.item)
        row = store.db.execute('SELECT signature FROM filter_inputs WHERE item=?', (job.item,)).fetchone()
        if not signature or not material or not row or row[0] != signature:
            return None
        settings = store.subscriptions.effective('feed', material['feed_id'])
        values = settings['effective']
        request = ProcessingRequest(operation='filter', sources=(InputSource(id=job.item, version=store.content_version(job.item), url=material['url'], title=material['title'], text=material['text']),), settings_version=store.settings_version(), prompt=values['prompt_filters']['value'], processor=values['processor']['value'], generation=job.generation)
        return request, signature, settings['feed']['public_ai']

    def save(self, job: 'Job', signature: str, result: FilterResult) -> None:
        store = self.store
        if not store.valid(job) or signature != self.signature(job.item):
            return
        store.save_derivative(job, signature, result.model_dump(), result.reason)

    def restore(self, item: str) -> bool:
        store = self.store
        if not store.material(item) or self.read(item)['status'] != 'done':
            return False
        with store.db:
            store.db.execute('INSERT INTO filter_restores VALUES (?,?) ON CONFLICT(item) DO UPDATE SET version=excluded.version', (item, store.content_version(item)))
        store.processing.sync()
        return True

    def retry(self, item: str) -> bool:
        if not self.signature(item):
            return False
        self.store.forget_execution('filter', item)
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET status='pending',next=0,error=NULL,generation=generation+1 WHERE kind='filter' AND item=?", (item,))
        self.store.processing.sync()
        return True

    def forget(self, item: str) -> None:
        self.store.db.execute('DELETE FROM filter_inputs WHERE item=?', (item,))
        self.store.db.execute('DELETE FROM filter_restores WHERE item=?', (item,))
