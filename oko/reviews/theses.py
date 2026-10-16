"""Publication theses: current inputs, durable results and their reader state."""
from oko.diagnostics import record_caught
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oko.processing.contracts import InputSource, ProcessingRequest, Theses

if TYPE_CHECKING:
    from oko.storage.store import Job, Store


class PublicationTheses:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        store.db.execute('CREATE TABLE IF NOT EXISTS retained_theses (item TEXT PRIMARY KEY, path TEXT, digest TEXT)')

    def signature(self, item: str) -> str | None:
        return self._signature(item)

    def _signature(self, item: str, legacy: bool = False) -> str | None:
        row = self.store.db.execute('SELECT * FROM items WHERE id=? AND viewed=0 AND complete=1', (item,)).fetchone()
        if not row or not self.store.path(item).is_file():
            return None
        filtered = self.store.filters.read(item)
        if filtered['status'] not in ('off', 'done') or filtered['hidden']:
            return None
        settings = self.store.subscriptions.effective('feed', row['feed_id'])
        values = {k: v['value'] for k, v in settings['effective'].items()}
        version = self.store.content_version(item)
        filter_input = filtered
        if settings['feed']['kind'] == 'youtube':
            raw, text = self.store.path(item).read_text().split('\n\n', 1)
            header = json.loads(raw)
            if not self.store.subscriptions.youtube_theses(row['feed_id']) or header.get('captions') != 'available':
                return None
            if not legacy:
                version = hashlib.sha256(text.encode()).hexdigest()
                filter_input = {}
        elif not values['theses']:
            return None
        config = self.store.processor_config()
        payload = [version, filter_input, row['members'], self.store.settings_version(), values, settings['feed']['public_ai'], config.model_dump() if config else None]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def sync(self) -> None:
        for row in self.store.db.execute('SELECT id FROM items WHERE viewed=0').fetchall():
            item = row['id']
            signature = self.signature(item)
            old = self.store.db.execute('SELECT signature FROM thesis_inputs WHERE item=?', (item,)).fetchone()
            state = self.store.db.execute("SELECT status FROM jobs WHERE kind='theses' AND item=?", (item,)).fetchone()
            if signature and old and old[0] != signature and state and state[0] == 'done':
                self._upgrade_caption_result(item, old[0], signature)
                old = self.store.db.execute('SELECT signature FROM thesis_inputs WHERE item=?', (item,)).fetchone()
            if signature is None and old and state and state[0] == 'done':
                feed = self.store.item_feed(item)
                if self.store.subscriptions.effective('feed', feed)['feed']['kind'] == 'youtube':
                    # Filtering may temporarily hide a completed caption result.
                    # Keep its committed file and job until the inputs are known.
                    continue
            if signature is None and old is None and (not state or state[0] == 'cancelled') and not (self.store.path(item).parent / 'theses.md').exists():
                continue
            if old and old[0] == signature:
                if state and (state[0] != 'done' or self.read(item) is not None):
                    continue
            path = self.store.path(item).parent / 'theses.md'
            path.unlink(missing_ok=True)
            with self.store.db:
                self.store.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL WHERE kind='theses' AND item=?", (item,))
                if signature:
                    self.store.db.execute('INSERT INTO thesis_inputs VALUES (?,?) ON CONFLICT(item) DO UPDATE SET signature=excluded.signature', (item, signature))
                    self.store.db.execute("INSERT INTO jobs(kind,item) VALUES ('theses',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=generation+1,next=0,error=NULL", (item,))
                else:
                    self.store.db.execute('DELETE FROM thesis_inputs WHERE item=?', (item,))

    def _upgrade_caption_result(self, item: str, old: str, signature: str) -> None:
        feed = self.store.item_feed(item)
        if self.store.subscriptions.effective('feed', feed)['feed']['kind'] != 'youtube':
            return
        if self.store.filters.read(item)['removed'] or old != self._signature(item, legacy=True):
            return
        path = self.store.path(item).parent / 'theses.md'
        try:
            header = json.loads(path.read_text().split('\n\n', 1)[0])
            if header['signature'] not in (old, signature):
                return
            result = Theses.model_validate(header['result'])
        except (OSError, ValueError, KeyError):
            record_caught('theses.upgrade')
            return
        if header['signature'] == old:
            from oko.storage.store import Job
            row = self.store.db.execute("SELECT id,generation FROM jobs WHERE kind='theses' AND item=?", (item,)).fetchone()
            job = Job(row['id'], 'theses', item, row['generation'])
            self.store.save_derivative(job, signature, result.model_dump(), '\n'.join('- ' + p.text for p in result.points), finish=False)
        with self.store.db:
            self.store.db.execute('UPDATE thesis_inputs SET signature=? WHERE item=?', (signature, item))

    def request(self, job: 'Job') -> tuple[ProcessingRequest, str, bool] | None:
        signature = self.signature(job.item)
        row = self.store.db.execute('SELECT signature FROM thesis_inputs WHERE item=?', (job.item,)).fetchone()
        material = self.store.material(job.item)
        if not signature or not row or row[0] != signature or not material:
            return None
        settings = self.store.subscriptions.effective('feed', material['feed_id'])
        values = settings['effective']
        text = material['display_text']
        version = self.store.content_version(job.item)
        if settings['feed']['kind'] == 'youtube':
            text = material['text']
            version = hashlib.sha256(text.encode()).hexdigest()
        request = ProcessingRequest(sources=(InputSource(id=job.item, version=version, url=material['url'], title=material['title'], text=text),), settings_version=self.store.settings_version(), prompt=values['prompt_theses']['value'], processor=values['processor']['value'], generation=job.generation)
        return request, signature, settings['feed']['public_ai']

    def retry(self, item: str) -> bool:
        if not self.store.material(item) or not self.signature(item):
            return False
        self.store.forget_execution('theses', item)
        with self.store.db:
            self._accepted(item, retain=True)
            self.store.db.execute("UPDATE jobs SET status='pending',next=0,error=NULL,generation=generation+1 WHERE kind='theses' AND item=?", (item,))
        return True

    def status(self, item: str) -> dict[str, Any] | None:
        if not self.signature(item):
            return None
        row = self.store.db.execute("SELECT status,error FROM jobs WHERE kind='theses' AND item=?", (item,)).fetchone()
        return dict(row) if row else dict(status='pending', error=None)

    def read(self, item: str) -> dict[str, Any] | None:
        accepted = self._accepted(item)
        return accepted[0] if accepted else None

    def _accepted(self, item: str, *, retain: bool = False) -> tuple[dict[str, Any], Path] | None:
        row = self.store.db.execute("SELECT status FROM jobs WHERE kind='theses' AND item=?", (item,)).fetchone()
        if not row or row[0] not in ('done', 'pending', 'running'):
            return None
        # Accepted bytes remain readable during an explicit regeneration, but
        # only while their signature still matches the current inputs.
        signature = self.signature(item)
        paths = [self.store.path(item).parent / 'theses.md']
        saved = self.store.db.execute('SELECT path,digest FROM retained_theses WHERE item=?', (item,)).fetchone()
        if saved:
            accepted_path = self.store.root / saved['path']
            if accepted_path not in paths:
                paths.append(accepted_path)
        feed = self.store.item_feed(item)
        if feed and self.store.subscriptions.effective('feed', feed)['feed']['kind'] == 'youtube':
            # Metadata revisions share the caption result in its published version.
            versions = self.store.db.execute('SELECT number FROM content_versions WHERE item=? AND number!=? ORDER BY number DESC',
                                             (item, self.store.versions.number(item))).fetchall()
            paths.extend(self.store.versions.path(item, v['number']).parent / 'theses.md' for v in versions)
        for path in paths:
            if not path.is_file():
                continue
            try:
                raw = path.read_bytes()
                header = json.loads(raw.decode().split('\n\n', 1)[0])
                if header['signature'] == signature:
                    result = Theses.model_validate(header['result']).model_dump()
                    relative = str(path.relative_to(self.store.root))
                    digest = hashlib.sha256(raw).hexdigest()
                    if path != paths[0] and saved and relative == saved['path'] and digest != saved['digest']:
                        continue
                    if row[0] != 'done':
                        # A moved file alone is not a committed result. Retain
                        # only bytes observed as accepted before explicit retry.
                        if not saved or (saved['path'], saved['digest']) != (relative, digest):
                            continue
                    elif retain:
                        self.store.db.execute('INSERT INTO retained_theses VALUES (?,?,?) ON CONFLICT(item) DO UPDATE SET path=excluded.path,digest=excluded.digest', (item, relative, digest))
                    return result, path
            except (OSError, ValueError, KeyError):
                record_caught('theses.read')
        return None

    def provenance(self, item: str) -> dict[str, Any] | None:
        accepted = self._accepted(item)
        return self.store.derivative_provenance(accepted[1], self.signature(item)) if accepted else None

    def save(self, job: 'Job', signature: str, theses: Theses) -> None:
        if not self.store.valid(job) or signature != self.signature(job.item):
            return
        self.store.save_derivative(job, signature, theses.model_dump(), '\n'.join('- ' + p.text for p in theses.points))

