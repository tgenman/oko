"""Individual local files: job ownership, durable acceptance and reader projection."""
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
from typing import Any, TYPE_CHECKING

from oko.collection.adapters import ExternalKey, ExternalReference, MediaReference, Platform
from oko.storage.reading import reading_value

if TYPE_CHECKING:
    from oko.storage.store import Job, Store


@dataclass(frozen=True)
class MediaDownload:
    publication_id: str
    publication: ExternalReference
    media: MediaReference
    temporary: Path
    final: Path


class MediaFiles:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        self.active_paths: set[Path] = set()

    def project_header(self, content: dict[str, Any], media: list[dict[str, Any]]) -> None:
        header = content['extra']['_reader']
        resources = header.get('source_manifest', {}).get('resources', [])
        by_key = {row['resource_key']: row for row in media}
        previous_attachments = {value['name']:value for value in header.get('attachments', [])}
        roles = {b['media_id']: b['role'] for b in content['blocks'] if b['type']=='media'}
        header['attachments'] = []
        for resource in resources:
            row = by_key.get(resource['key'])
            if row is None:
                continue
            ready = row['status']=='ready'
            resource.update(status='loaded' if ready else 'missing' if row['status']=='pending_download' else 'unsupported', reason=row['reason'])
            if ready:
                resource['sha256'] = self.store.versions.file_digest(self.store.root/row['storage_key'])
        for row in media:
            if row['status']=='ready' and not row['name'].startswith('image-'):
                role = roles.get(row['id'], 'attachment')
                kind = previous_attachments.get(row['name'], {}).get('kind') or ('file' if role=='attachment' else role)
                header['attachments'].append(dict(name=row['name'],kind=kind,mime=row['mime_type'] or 'application/octet-stream'))
        base = content['extra'].get('_collection_reason', header.get('reason', ''))
        waiting = [row['reason'] or 'Вложение загружается' for row in media if row['status']!='ready']
        header['reason'] = '; '.join(dict.fromkeys(([base] if base else [])+waiting))
        header['full'] = not header['reason'] and not any(r.get('external') for r in resources) and not header.get('source_manifest', {}).get('post_web_unavailable')

    def schedule(self, item: str) -> None:
        db = self.store.db
        db.execute("""UPDATE jobs SET status='cancelled',generation=generation+1 WHERE kind='download_media'
            AND status IN ('pending','running') AND item NOT IN (SELECT id FROM media_files)""")
        for row in db.execute("SELECT id FROM media_files WHERE publication_id=? AND status='pending_download'", (item,)).fetchall():
            db.execute("INSERT OR IGNORE INTO jobs(kind,item) VALUES ('download_media',?)", (row['id'],))

    def owner(self, media: str) -> str | None:
        row = self.store.db.execute('SELECT publication_id FROM media_files WHERE id=?', (media,)).fetchone()
        return str(row[0]) if row else None

    @reading_value
    def loading(self, item: str) -> bool:
        return self.store.db.execute("""SELECT 1 FROM jobs j JOIN media_files m ON m.id=j.item
            WHERE m.publication_id=? AND j.kind='download_media' AND (j.status='running' OR j.status='pending' AND j.next<=?) LIMIT 1""",
            (item, self.store.clock())).fetchone() is not None

    def begin(self, job: 'Job') -> MediaDownload | None:
        store = self.store
        row = store.db.execute('''SELECT m.*,i.platform,i.external_id,i.url FROM media_files m JOIN items i ON i.id=m.publication_id
            WHERE m.id=? AND i.viewed=0 AND m.status='pending_download' ''', (job.item,)).fetchone()
        if not store.valid(job) or row is None:
            return None
        temporary = store.root/'staging'/'_media'/(str(job.id)+'-'+str(job.generation)+'-'+secrets.token_hex(8))
        temporary.parent.mkdir(parents=True, exist_ok=True)
        final = store.root/'media'/row['publication_id']/row['id']
        self.active_paths.update((temporary, final))
        return MediaDownload(row['publication_id'], ExternalReference(ExternalKey(Platform(row['platform']), row['external_id']), row['url']),
            MediaReference(row['resource_key'], row['source_locator'], row['name'], row['mime_type'], row['size_bytes'], row['resource_identity']), temporary, final)

    def complete(self, job: 'Job', download: MediaDownload, actual: MediaReference) -> bool:
        store = self.store
        if not store.valid(job) or self.owner(job.item) != download.publication_id:
            return False
        if actual.key != download.media.key or actual.name != download.media.name or actual.resource_identity != download.media.resource_identity:
            raise ValueError('Media adapter changed the resource identity')
        size = download.temporary.stat().st_size
        if actual.size_bytes is not None and actual.size_bytes != size:
            raise ValueError('Media adapter returned a different size')
        with download.temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        download.final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(download.temporary, download.final)
        store.versions._fsync(download.final.parent)
        store.fault('after_media_move')
        row = store.db.execute('SELECT content FROM items WHERE id=? AND viewed=0', (download.publication_id,)).fetchone()
        if row is None:
            return False
        content = deepcopy(json.loads(row['content']))
        media = [dict(r) for r in store.db.execute('SELECT * FROM media_files WHERE publication_id=?', (download.publication_id,))]
        for value in media:
            if value['id']==job.item:
                value.update(status='ready',reason='',storage_key=str(download.final.relative_to(store.root)),size_bytes=size,
                             source_locator=actual.locator,mime_type=actual.mime_type)
        self.project_header(content, media)
        with store.db:
            if not store.valid(job) or self.owner(job.item) != download.publication_id:
                return False
            store.db.execute("""UPDATE media_files SET status='ready',reason='',storage_key=?,size_bytes=?,source_locator=?,mime_type=? WHERE id=?""",
                (str(download.final.relative_to(store.root)),size,actual.locator,actual.mime_type,job.item))
            store.db.execute('UPDATE items SET content=? WHERE id=?', (json.dumps(content,ensure_ascii=False),download.publication_id))
            store.finish(job)
            store.read_versions.media_changed(download.publication_id)
            store.fault('before_media_commit')
        store.fault('after_media_commit')
        store.publications.recover(download.publication_id)
        return True

    def release(self, download: MediaDownload) -> None:
        self.active_paths.difference_update((download.temporary, download.final))
        download.temporary.unlink(missing_ok=True)
