"""Publication theses: current inputs, durable results and their reader state."""
from oko.storage import material_read
from oko.reviews import publication_input
from oko.storage.reading import reading_value, saved_bytes, saved_document
from oko.diagnostics import record_caught
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oko.processing.contracts import InputSource, ProcessingRequest, Theses

if TYPE_CHECKING:
    from oko.storage.store import Job, Store


class PublicationTheses:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        store.db.execute('CREATE TABLE IF NOT EXISTS retained_theses (item TEXT PRIMARY KEY, path TEXT, digest TEXT)')

    @reading_value
    def signature(self, item: str) -> str | None:
        return self._signature(item)

    def _signature(self, item: str, legacy: bool = False) -> str | None:
        row = material_read.item(self.store.db, item)
        if not legacy and row and row['platform'] is not None and row['content']!='{}':
            return publication_input.signature(self.store,item,'theses')
        if not row or row['viewed'] or not row['complete'] or not self.store.path(item).is_file():
            return None
        try:
            header, own_text = self.store.original(item)
        except (OSError, ValueError):
            return None
        if header.get('source_manifest', {}).get('preview_fragment') is not None or header.get('source_manifest', {}).get('post_web_unavailable') and not own_text.strip():
            return None
        reply = header.get('reply_preview')
        if reply is not None and not own_text.strip() and not reply.get('text', '').strip():
            return None
        filtered = self.store.filters.read(item)
        if filtered['status'] not in ('off', 'done') or filtered['hidden']:
            return None
        settings = self.store.subscriptions.effective('feed', row['feed_id'], details=False)
        values = {k: v['value'] for k, v in settings['effective'].items()}
        version = self.store.content_version(item)
        filter_input = filtered
        if settings['feed']['kind'] == 'youtube':
            header, text = self.store.original(item)
            if not self.store.subscriptions.youtube_theses(row['feed_id']) or header.get('captions') != 'available':
                return None
            if not legacy:
                version = hashlib.sha256(text.encode()).hexdigest()
                filter_input = {}
        elif not values['theses']:
            return None
        config = self.store.processor_config()
        payload = [version, filter_input, row['members'], self.store.settings_version(row['feed_id']), values, settings['feed']['public_ai'], config.model_dump() if config else None]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def sync(self, items: list[str]) -> None:
        for item in items:
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
                if state and (state[0] not in ('done','cancelled') or self.read(item) is not None):
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
        public_ai=settings['feed']['public_ai']
        version_settings=self.store.settings_version(material['feed_id'])
        if self.store.publications.owns(job.item):
            values,public_ai,version_settings=publication_input.request_settings(self.store,job.item)
        text = material['display_text']
        version = self.store.content_version(job.item)
        if settings['feed']['kind'] == 'youtube':
            text = material['text']
            version = hashlib.sha256(text.encode()).hexdigest()
        request = ProcessingRequest(sources=(InputSource(id=job.item, version=version, url=material['url'], title=material['title'], text=text, reply_preview=material.get('reply_preview')),), settings_version=version_settings, prompt=values['prompt_theses']['value'], processor=values['processor']['value'], generation=job.generation)
        return request, signature, public_ai

    def retry(self, item: str) -> bool:
        row=material_read.item(self.store.db,item)
        if row and row['platform'] is not None and json.loads(row['processing_metadata']).get('summary',{}).get('reason')=='no_input':
            return True
        if not self.store.material(item) or not self.signature(item):
            return False
        self.store.forget_execution('theses', item)
        with self.store.db:
            if not self.store.publications.owns(item):
                self._accepted(item, retain=True)
            self.store.db.execute("UPDATE jobs SET status='pending',next=0,retry_at=NULL,error=NULL,generation=generation+1 WHERE kind='theses' AND item=?", (item,))
            if self.store.publications.owns(item):
                self.store.db.execute("UPDATE items SET status='pending_summary' WHERE id=?",(item,))
        return True

    @reading_value
    def status(self, item: str) -> dict[str, Any] | None:
        if not self.signature(item):
            return None
        row = material_read.jobs(self.store.db, item).get('theses')
        return dict(status=row['status'],error=row['error']) if row else dict(status='pending', error=None)

    def read(self, item: str) -> dict[str, Any] | None:
        row=material_read.item(self.store.db,item)
        if row and row['platform'] is not None and row['content']!='{}':
            metadata=json.loads(row['processing_metadata']).get('summary',{})
            if metadata.get('signature',metadata.get('input_hash'))==self.signature(item) and metadata:
                value=json.loads(row['summarized_content'])
                url=self.store.db.execute('SELECT url FROM items WHERE id=?',(item,)).fetchone()[0]
                return dict(points=[dict(text=p['text'],sources=[dict(id=identity,url=url) for identity in p['source_publication_ids']]) for p in value['points']])
            return None
        accepted = self._accepted(item)
        return accepted[0] if accepted else None

    @reading_value
    def _accepted(self, item: str, *, retain: bool = False) -> tuple[dict[str, Any], Path] | None:
        row = material_read.jobs(self.store.db, item).get('theses')
        if not row or row['status'] not in ('done', 'pending', 'running'):
            return None
        # Accepted bytes remain readable during an explicit regeneration, but
        # only while their signature still matches the current inputs.
        signature = self.signature(item)
        paths = [self.store.path(item).parent / 'theses.md']
        record = material_read.item(self.store.db, item)
        saved = material_read.retained(self.store.db,item)
        if saved:
            accepted_path = self.store.root / saved['path']
            if accepted_path not in paths:
                paths.append(accepted_path)
        feed = record['feed_id'] if record else None
        if feed and material_read.feed(self.store.db, feed)['kind'] == 'youtube':
            # Metadata revisions share the caption result in its published version.
            current_number=self.store.versions.number(item)
            versions = [v for v in material_read.versions(self.store.db,item) if v['number'] != current_number]
            paths.extend(self.store.versions.path(item, v['number']).parent / 'theses.md' for v in versions)
        for path in paths:
            if not path.is_file():
                continue
            try:
                raw = saved_bytes(path)
                header, _ = saved_document(path)
                if header['signature'] == signature:
                    result = Theses.model_validate(header['result']).model_dump()
                    relative = str(path.relative_to(self.store.root))
                    digest = hashlib.sha256(raw).hexdigest()
                    if path != paths[0] and saved and relative == saved['path'] and digest != saved['digest']:
                        continue
                    if row['status'] != 'done':
                        # A moved file alone is not a committed result. Retain
                        # only bytes observed as accepted before explicit retry.
                        if not saved or (saved['path'], saved['digest']) != (relative, digest):
                            continue
                    elif retain:
                        # A retry may replace theses.md before its transaction commits.
                        # Keep the accepted inode and its provenance independently.
                        accepted_path = path.with_name('accepted-theses-'+digest+'.md')
                        if not accepted_path.exists():
                            os.link(path, accepted_path)
                            fd = os.open(accepted_path.parent, os.O_RDONLY)
                            try:
                                os.fsync(fd)
                            finally:
                                os.close(fd)
                        relative = str(accepted_path.relative_to(self.store.root))
                        self.store.db.execute('INSERT INTO retained_theses VALUES (?,?,?) ON CONFLICT(item) DO UPDATE SET path=excluded.path,digest=excluded.digest', (item, relative, digest))
                    return result, path
            except (OSError, ValueError, KeyError):
                record_caught('theses.read')
        if row['status'] == 'done' and signature is not None:
            self.store.processing.repair(item)
        return None

    def provenance(self, item: str) -> dict[str, Any] | None:
        row=material_read.item(self.store.db,item)
        if row and row['platform'] is not None and row['content']!='{}':
            if self.read(item) is None:
                return None
            metadata=json.loads(row['processing_metadata']).get('summary',{})
            return {key:metadata[key] for key in ('processor','model','reasoning_effort','fast') if key in metadata} or None
        accepted = self._accepted(item)
        return self.store.derivative_provenance(accepted[1], self.signature(item)) if accepted else None

    def save(self, job: 'Job', signature: str, theses: Theses) -> None:
        if not self.store.valid(job) or signature != self.signature(job.item):
            return
        self.store.save_derivative(job, signature, theses.model_dump(), '\n'.join('- ' + p.text for p in theses.points))

