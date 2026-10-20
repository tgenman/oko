"""Durable filtered views; originals and media remain owned by Store."""
from oko.storage import material_read
from oko.storage.reading import reading_value, saved_document
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

    @reading_value
    def signature(self, item: str) -> str | None:
        return self._signature(item)

    def _signature(self, item: str, *, legacy: bool = False) -> str | None:
        store = self.store
        row = material_read.item(store.db, item)
        if not row or row['viewed'] or not row['complete'] or not store.path(item).is_file():
            return None
        settings = store.subscriptions.effective('feed', row['feed_id'], details=legacy)
        # Retired public metadata must not invalidate already accepted results.
        # This null field was part of the persisted processing signature.
        settings = dict(settings, feed=dict(settings['feed'], telegram_topic=None))
        if not settings['effective']['prompt_filters']['value'].strip():
            return None
        config = store.processor_config()
        if legacy:
            payload = [store.content_version(item), settings, store.settings_version(), config.model_dump() if config else None]
        else:
            # Display metadata and history progress are not processing inputs.
            feed = {key: settings['feed'][key] for key in ('id', 'kind', 'transport', 'public_ai')}
            payload = ['filter-v2', store.content_version(item), settings['effective'], feed,
                       store.settings_version(row['feed_id']), config.model_dump() if config else None]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def sync(self, items: list[str]) -> None:
        store = self.store
        for item in items:
            signature = self.signature(item)
            old = store.db.execute('SELECT signature FROM filter_inputs WHERE item=?', (item,)).fetchone()
            state = store.db.execute("SELECT id,generation,status FROM jobs WHERE kind='filter' AND item=?", (item,)).fetchone()
            if signature and old and old[0] != signature and state and state['status'] == 'done':
                self._upgrade_result(item, old[0], signature, state['id'], state['generation'])
                old = store.db.execute('SELECT signature FROM filter_inputs WHERE item=?', (item,)).fetchone()
            if signature is None:
                store.read_versions.sync_filter(item, False)
            if signature is None and old is None and (not state or state['status'] == 'cancelled') and not (store.path(item).parent / 'filter.md').exists():
                continue
            if old and old[0] == signature and state:
                filtered = self.read(item)
                store.read_versions.sync_filter(item, bool(filtered['hidden']))
                if state['status'] != 'done' or filtered.get('status') == 'done':
                    continue
            store.read_versions.sync_filter(item, False)
            (store.path(item).parent / 'filter.md').unlink(missing_ok=True)
            with store.db:
                store.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL WHERE kind='filter' AND item=?", (item,))
                if signature:
                    store.db.execute('INSERT INTO filter_inputs VALUES (?,?) ON CONFLICT(item) DO UPDATE SET signature=excluded.signature', (item, signature))
                    store.db.execute("INSERT INTO jobs(kind,item) VALUES ('filter',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=generation+1,next=0,error=NULL", (item,))
                else:
                    store.db.execute('DELETE FROM filter_inputs WHERE item=?', (item,))

    def _upgrade_result(self, item: str, old: str, signature: str, identity: int, generation: int) -> None:
        if old != self._signature(item, legacy=True):
            return
        path = self.store.path(item).parent / 'filter.md'
        try:
            header, text = saved_document(path)
            if header['signature'] not in (old, signature):
                return
            result = FilterResult.model_validate(header['result']).model_dump()
        except (OSError, ValueError, KeyError):
            return
        if header['signature'] == old:
            from oko.storage.store import Job
            self.store.save_derivative(Job(identity, 'filter', item, generation), signature, result, text,
                                       finish=False, provenance=header.get('provenance'))
        with self.store.db:
            self.store.db.execute('UPDATE filter_inputs SET signature=? WHERE item=?', (signature, item))

    @reading_value
    def read(self, item: str) -> dict[str, Any]:
        signature = self.signature(item)
        if not signature:
            return dict(status='off', hidden=False, restored=False, reason='', removed=[])
        store = self.store
        override = material_read.restored_version(store.db,item)
        restored = bool(override and override == store.content_version(item))
        row = material_read.jobs(store.db, item).get('filter')
        if row and row['status'] == 'done':
            try:
                data, _ = saved_document(store.path(item).parent / 'filter.md')
                if data['signature'] == signature:
                    result = FilterResult.model_validate(data['result']).model_dump()
                    return dict(result, hidden=False if restored else result['hidden'], restored=restored, status='done', error=None)
            except (OSError, ValueError, KeyError):
                record_caught('filtering.read')
                pass
            store.processing.repair(item)
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
        request = ProcessingRequest(operation='filter', sources=(InputSource(id=job.item, version=store.content_version(job.item), url=material['url'], title=material['title'], text=material['text']),), settings_version=store.settings_version(material['feed_id']), prompt=values['prompt_filters']['value'], processor=values['processor']['value'], generation=job.generation)
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
