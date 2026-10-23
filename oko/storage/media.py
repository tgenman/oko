"""Individual local files: job ownership, durable acceptance and reader projection."""
import asyncio
from io import BufferedWriter
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
from uuid import UUID
from typing import Any, TYPE_CHECKING

from oko.collection.adapters import AdapterIssue, AdapterErrorKind, ExternalKey, ExternalReference, MediaReference, Platform
from oko.storage.reading import reading_value
from oko.storage import collection_jobs, publication_links

if TYPE_CHECKING:
    from typing_extensions import Buffer
    from oko.storage.store import Job, Store


@dataclass(frozen=True)
class MediaDownload:
    publication_id: str
    publication: ExternalReference
    media: MediaReference
    temporary: Path
    final: Path


class MediaOutput(BufferedWriter):
    """Reject writes after the caller withdraws this download's admission."""
    def __init__(self, path: Path, admitted: Callable[[], bool]) -> None:
        super().__init__(path.open('wb',buffering=0))
        self.admitted=admitted

    def write(self, buffer: 'Buffer') -> int:
        if not self.admitted():
            raise asyncio.CancelledError
        return super().write(buffer)


class MediaFiles:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        self.active_paths: set[Path] = set()
        columns = {row['name'] for row in store.db.execute('PRAGMA table_info(jobs)')}
        with store.db:
            for name, declaration in (('retry_at','REAL'), ('retry_delay','REAL NOT NULL DEFAULT 0'), ('wait_scope','TEXT')):
                if name not in columns:
                    store.db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {declaration}')

    def project_header(self, content: dict[str, Any], media: list[dict[str, Any]]) -> None:
        header = content['extra']['_reader']
        resources = header.get('source_manifest', {}).get('resources', [])
        by_key = {row['resource_key']: row for row in media}
        previous_attachments = {value['name']:value for value in header.get('attachments', [])}
        roles = {b['media_id']: b['role'] for b in content['blocks'] if b['type']=='media'}
        order = {identity:index for index,identity in enumerate(roles)}
        media = sorted(media,key=lambda row:order.get(row['id'],len(order)))
        header['attachments'] = []
        for resource in resources:
            row = by_key.get(resource['key'])
            if row is None:
                continue
            ready = row['status']=='ready'
            resource['name'] = self.projection_name(row)
            resource.update(status='loaded' if ready else 'missing' if row['status']=='pending_download' else 'unsupported',
                reason=row['reason'] or ('Вложение загружается' if row['status']=='pending_download' else ''))
            if ready:
                resource['sha256'] = self.store.versions.file_digest(self.store.root/row['storage_key'])
        header['_media_urls'] = {}
        for row in media:
            if row['status']=='ready':
                role = roles.get(row['id'], 'attachment')
                kind = previous_attachments.get(row['name'], {}).get('kind') or {'attachment':'file','subtitles':'file','cover':'image'}.get(role,role)
                name = self.projection_name(row)
                header['attachments'].append(dict(name=name,media_id=row['id'],kind=kind,mime=row['mime_type'] or 'application/octet-stream'))
                header['_media_urls'][name] = self.url(row['publication_id'],row['id'])
        base = content['extra'].get('_collection_reason', header.get('reason', ''))
        waiting = [row['reason'] or 'Вложение загружается' for row in media if row['status']!='ready']
        header['reason'] = '; '.join(dict.fromkeys(([base] if base else [])+waiting))
        header['full'] = not header['reason'] and not any(r.get('external') for r in resources) and not header.get('source_manifest', {}).get('post_web_unavailable')

    @staticmethod
    def url(item: str, media: str) -> str:
        return f'/api/v3/materials/{item}/media/{media}'

    @staticmethod
    def projection_name(row: Any) -> str:
        return 'media-'+str(UUID(row['id']).int)+'.bin'

    def file(self, item: str, media: str) -> tuple[Path, str] | None:
        row = self.store.db.execute('''SELECT m.* FROM media_files m JOIN items i ON i.id=m.publication_id
            WHERE m.id=? AND m.publication_id=? AND i.viewed=0 AND m.status='ready' ''', (media,item)).fetchone()
        if row and row['storage_key']:
            path = self.store.root/row['storage_key']
            if path.is_file():
                return path, row['mime_type'] or 'application/octet-stream'
        return None

    def schedule(self, item: str) -> None:
        db = self.store.db
        db.execute("""UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL,problem_code=NULL WHERE kind='download_media'
            AND status IN ('pending','running','failed','unavailable') AND item NOT IN (SELECT id FROM media_files)""")
        for row in db.execute("SELECT id FROM media_files WHERE publication_id=? AND status='pending_download'", (item,)).fetchall():
            db.execute("INSERT OR IGNORE INTO jobs(kind,item) VALUES ('download_media',?)", (row['id'],))

    def owner(self, media: str) -> str | None:
        row = self.store.db.execute('SELECT publication_id FROM media_files WHERE id=?', (media,)).fetchone()
        return str(row[0]) if row else None

    def retry(self, item: str) -> None:
        """Manual retry preserves source deadlines and the accumulated backoff."""
        store = self.store
        rows = store.db.execute("""SELECT j.* FROM jobs j JOIN media_files m ON m.id=j.item
            WHERE m.publication_id=? AND j.kind='download_media' AND j.status IN ('failed','unavailable','pending')""", (item,)).fetchall()
        for row in rows:
            waiting = bool(row['wait_scope'] and row['retry_at'] and row['retry_at']>store.wall_clock())
            store.db.execute("""UPDATE jobs SET status='pending',generation=generation+1,next=?,retry_at=?,
                error=?,problem_code=? WHERE id=?""", (row['next'] if waiting else 0, row['retry_at'] if waiting else None,
                row['error'] if waiting else None,row['problem_code'] if waiting else None,row['id']))
            if not waiting:
                store.db.execute("UPDATE media_files SET status='pending_download',reason='' WHERE id=?", (row['item'],))

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
        if actual.key != download.media.key or actual.resource_identity != download.media.resource_identity:
            raise ValueError('Media adapter changed the resource identity')
        size = download.temporary.stat().st_size
        if actual.size_bytes is not None and actual.size_bytes != size:
            raise ValueError('Media adapter returned a different size')
        with download.temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        download.final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(download.temporary, download.final)
        store.versions._fsync(download.final.parent)
        store.versions._fsync(download.final.parent.parent)
        store.fault('after_media_move')
        row = store.db.execute('SELECT content FROM items WHERE id=? AND viewed=0', (download.publication_id,)).fetchone()
        if row is None:
            return False
        content = deepcopy(json.loads(row['content']))
        media = [dict(r) for r in store.db.execute('SELECT * FROM media_files WHERE publication_id=?', (download.publication_id,))]
        for value in media:
            if value['id']==job.item:
                value.update(status='ready',reason='',name=actual.name,storage_key=str(download.final.relative_to(store.root)),size_bytes=size,
                             source_locator=actual.locator,mime_type=actual.mime_type)
        self.project_header(content, media)
        with store.db:
            if not store.valid(job) or self.owner(job.item) != download.publication_id:
                return False
            store.db.execute("""UPDATE media_files SET status='ready',reason='',storage_key=?,size_bytes=?,source_locator=?,mime_type=?,name=? WHERE id=?""",
                (str(download.final.relative_to(store.root)),size,actual.locator,actual.mime_type,actual.name,job.item))
            store.db.execute('UPDATE items SET content=? WHERE id=?', (json.dumps(content,ensure_ascii=False),download.publication_id))
            store.finish(job)
            store.subscriptions.history.saved(download.publication_id, bool(content['extra']['_reader']['full']))
            store.read_versions.media_changed(download.publication_id)
            publication_links.notify_parents(store,download.publication_id)
            store.fault('before_media_commit')
        store.fault('after_media_commit')
        store.publications.recover(download.publication_id)
        return True

    def release(self, download: MediaDownload) -> None:
        self.active_paths.difference_update((download.temporary, download.final))
        download.temporary.unlink(missing_ok=True)

    def cleanup(self) -> None:
        """Runs under Runtime's file admission, outside a database transaction."""
        root = self.store.root
        referenced = {root/row[0] for row in self.store.db.execute("SELECT storage_key FROM media_files WHERE storage_key IS NOT NULL")}
        for directory in (root/'media').iterdir():
            if not directory.is_dir() or directory.is_symlink():
                if directory not in self.active_paths and directory not in referenced:
                    directory.unlink()
                continue
            for path in directory.iterdir():
                if path.is_file() and path not in referenced and path not in self.active_paths:
                    path.unlink()
            if not any(directory.iterdir()) and not any(directory in path.parents for path in self.active_paths):
                directory.rmdir()
        staging = root/'staging'/'_media'
        if staging.is_dir():
            for path in staging.iterdir():
                if path.is_file() and path not in self.active_paths:
                    path.unlink()

    def failed(self, job: 'Job', issue: AdapterIssue) -> None:
        store = self.store
        if not store.valid(job):
            return
        owner = self.owner(job.item) if job.kind=='download_media' else job.item
        changed = issue.kind==AdapterErrorKind.media_changed
        status = 'cancelled' if changed else collection_jobs.failure_status(issue)
        content = None
        if owner and job.kind=='download_media':
            publication = store.db.execute('SELECT content FROM items WHERE id=? AND viewed=0', (owner,)).fetchone()
            if publication:
                content = json.loads(publication['content'])
                media = [dict(r) for r in store.db.execute('SELECT * FROM media_files WHERE publication_id=?', (owner,))]
                for value in media:
                    if value['id']==job.item:
                        value.update(reason=issue.message,status='unavailable' if status=='unavailable' or changed else 'pending_download')
                self.project_header(content,media)
        with store.db:
            if not store.valid(job):
                return
            collection_jobs.fail(store, job, issue, cancelled=changed)
            if changed and owner:
                store.enqueue_fetch(owner)
            if content is not None:
                assert owner is not None
                store.db.execute('UPDATE media_files SET reason=?,status=? WHERE id=?',
                    (issue.message,'unavailable' if status=='unavailable' or changed else 'pending_download',job.item))
                store.db.execute('UPDATE items SET content=? WHERE id=?', (json.dumps(content,ensure_ascii=False),owner))
                store.subscriptions.history.saved(owner,False)
                store.read_versions.media_changed(owner)
                publication_links.notify_parents(store,owner)
        if content is not None:
            store.publications.recover(owner)
