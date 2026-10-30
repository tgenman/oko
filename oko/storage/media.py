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
from uuid import UUID,uuid4
from typing import Any, TYPE_CHECKING

from oko.collection.adapters import AdapterIssue, AdapterErrorKind, ExternalKey, ExternalReference, MediaReference, Platform
from oko.storage.reading import reading_value, reader_now
from oko.storage.media_paths import existing_file, resolve_key
from oko.storage import collection_jobs, publication_links, youtube_media, files, material_read, memberships

if TYPE_CHECKING:
    from typing_extensions import Buffer
    from oko.storage.store import Store


@dataclass(frozen=True)
class MediaDownload:
    publication_id: str
    publication: ExternalReference
    media: MediaReference
    temporary: Path
    final: Path


StagedMedia = tuple[str, Path, int]


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
    def project_header(self, content: dict[str, Any], media: list[dict[str, Any]]) -> None:
        header = content['extra']['_reader']
        resources = header.get('source_manifest', {}).get('resources', [])
        by_key = {row['resource_key']: row for row in media}
        previous_attachments = {value['name']:value for value in header.get('attachments', [])}
        roles = {b['media_id']: b['role'] for b in content['blocks'] if b['type']=='media'}
        order = {identity:index for index,identity in enumerate(roles)}
        media = [dict(row, status='pending_download') if row['status']=='ready' and not existing_file(self.store.root, row['storage_key']) else row for row in media]
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
                resource['external']=None
                resource['reason']=''
                path = existing_file(self.store.root,row['storage_key'])
                if path is not None:
                    resource['sha256'] = files.file_digest(path)
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
        row = self.store.db.execute("SELECT m . * FROM media_files m JOIN publications i ON i . id = m . publication_id WHERE m . id = %s AND m . publication_id = %s AND i . viewed = 0 AND m . status = 'ready' ", (media,item)).fetchone()
        if row and row['storage_key']:
            path = existing_file(self.store.root,row['storage_key'])
            if path is not None:
                return path, row['mime_type'] or 'application/octet-stream'
        return None


    def owner(self, media: str) -> str | None:
        row = self.store.db.execute('SELECT publication_id FROM media_files WHERE id = %s', (media,)).fetchone()
        return str(row[0]) if row else None


    @reading_value
    def loading(self, item: str) -> bool:
        snapshot=material_read.current(self.store.db)
        if snapshot and item in snapshot.items:
            now=reader_now(self.store.clock())
            return any(row['status']=='running' or row['status']=='pending' and row['next']<=now for row in snapshot.media_jobs.get(item,[]))
        return self.store.db.execute("SELECT 1 FROM jobs j JOIN media_files m ON m . id = j . item WHERE m . publication_id = %s AND j . kind = 'download_media' AND ( j . status = 'running' OR j . status = 'pending' AND j . next <= %s ) LIMIT 1",
            (item, reader_now(self.store.clock()))).fetchone() is not None

    def begin(self, identity: str, attempt: str, revalidate: bool) -> MediaDownload | None:
        store = self.store
        row = store.db.execute("SELECT m . * , i . platform , i . external_id , i . url FROM media_files m JOIN publications i ON i . id = m . publication_id WHERE m . id = %s AND i . viewed = 0 AND ( m . status = 'pending_download' OR m . status = 'ready' AND %s = 1 ) ", (identity,revalidate)).fetchone()
        if row is None:
            return None
        temporary = store.root/'staging'/'_media'/(attempt+'-'+secrets.token_hex(8))
        temporary.parent.mkdir(parents=True, exist_ok=True)
        final = store.root/'media'/row['publication_id']/str(uuid4())
        self.active_paths.update((temporary, final))
        return MediaDownload(row['publication_id'], ExternalReference(ExternalKey(Platform(row['platform']), row['external_id']), row['url']),
            MediaReference(row['resource_key'], row['source_locator'], row['name'], row['mime_type'], row['size_bytes'], row['resource_identity']), temporary, final)

    def current(self, identity: str, download: MediaDownload) -> bool:
        row=self.store.db.execute('SELECT m . * , i . platform , i . external_id FROM media_files m JOIN publications i ON i . id = m . publication_id WHERE m . id = %s AND i . viewed = 0',(identity,)).fetchone()
        return bool(row and row['publication_id']==download.publication_id
            and row['platform']==download.publication.key.platform.value and row['external_id']==download.publication.key.external_id
            and (row['resource_key'],row['source_locator'],row['name'],row['mime_type'],row['size_bytes'],row['resource_identity'])==
                (download.media.key,download.media.locator,download.media.name,download.media.mime_type,download.media.size_bytes,download.media.resource_identity))

    def stage(self, identity: str, download: MediaDownload, actual: MediaReference) -> tuple[str, Path, int]:
        store = self.store
        if not self.current(identity,download):
            raise ValueError('Media owner changed')
        if actual.key != download.media.key or actual.resource_identity != download.media.resource_identity:
            raise ValueError('Media adapter changed the resource identity')
        size = download.temporary.stat().st_size
        if actual.size_bytes is not None and actual.size_bytes != size:
            raise ValueError('Media adapter returned a different size')
        previous=store.db.execute('SELECT * FROM media_files WHERE id = %s',(identity,)).fetchone()
        previous_file = existing_file(store.root,previous['storage_key']) if previous else None
        same=bool(download.final.name!=identity and previous_file
            and files.file_digest(download.temporary)==files.file_digest(previous_file))
        accepted_identity=identity if same or previous and previous['status']=='pending_download' else download.final.name
        saved=previous_file if same and previous_file else download.final
        with download.temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        if same:
            download.temporary.unlink()
        else:
            saved.parent.mkdir(parents=True,exist_ok=True)
            os.link(download.temporary,saved)
            download.temporary.unlink()
            files.fsync_directory(saved.parent)
            files.fsync_directory(saved.parent.parent)
        store.fault('after_media_move')
        return accepted_identity,saved,size

    def accept(self, media_id: str, download: MediaDownload, actual: MediaReference, staged: tuple[str, Path, int]) -> bool:
        store=self.store
        identity,saved,size=staged
        previous=store.db.execute('SELECT * FROM media_files WHERE id = %s',(media_id,)).fetchone()
        if not self.current(media_id,download):
            raise ValueError('Media owner changed')
        row = store.db.execute('SELECT content FROM publications WHERE id = %s AND viewed = 0', (download.publication_id,)).fetchone()
        if row is None:
            raise ValueError('Media owner changed')
        content = deepcopy(json.loads(row['content']))
        media = [dict(r) for r in store.db.execute('SELECT * FROM media_files WHERE publication_id = %s', (download.publication_id,))]
        for value in media:
            if value['id']==media_id:
                value.update(id=identity,status='ready',reason='',name=actual.name,storage_key=str(saved.relative_to(store.root)),size_bytes=size,
                             source_locator=actual.locator,mime_type=actual.mime_type)
        subtitles=youtube_media.subtitle_id(content)==media_id
        if identity!=media_id:
            if previous and previous['status']=='ready':
                content['extra']['_reader']['changed']=True
            for block in content['blocks']:
                if block.get('media_id')==media_id:
                    block['media_id']=identity
        if subtitles:
            content['extra']['youtube']['captions']='available'
            content['extra']['youtube']['captions_digest']=files.file_digest(saved)
            content['extra']['_reader']['captions']='available'
        self.project_header(content, media)
        with store.db:
            memberships.lock_graph(store.db)
            if not self.current(media_id,download):
                raise ValueError('Media owner changed')
            store.db.execute("UPDATE media_files SET id = %s , status = 'ready' , reason = '' , storage_key = %s , size_bytes = %s , source_locator = %s , mime_type = %s , name = %s WHERE id = %s",
                (identity,str(saved.relative_to(store.root)),size,actual.locator,actual.mime_type,actual.name,media_id))
            store.db.execute('UPDATE publications SET content = %s , media_ids = %s WHERE id = %s', (json.dumps(content,ensure_ascii=False),json.dumps([value['id'] for value in media]),download.publication_id))
            store.subscriptions.history.saved(download.publication_id, bool(content['extra']['_reader']['full']))
            store.read_sync.mark_item(download.publication_id)
            store.read_sync.saved_publication(download.publication_id)
            if subtitles:
                store.processing.reconcile_accepted_item(download.publication_id)
            publication_links.notify_parents(store,download.publication_id)
            store.fault('before_media_commit')
        return True

    def release(self, download: MediaDownload) -> None:
        self.active_paths.difference_update((download.temporary, download.final))
        download.temporary.unlink(missing_ok=True)

    def cleanup(self) -> None:
        """Runs under Runtime's file admission, outside a database transaction."""
        root = self.store.root
        referenced = {path for row in self.store.db.execute("SELECT storage_key FROM media_files WHERE storage_key IS NOT NULL") if (path := resolve_key(root,row[0])) is not None}
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

    def _subtitles_absent(self, media_id: str, owner: str, issue: AdapterIssue) -> None:
        store=self.store
        row=store.db.execute('SELECT content FROM publications WHERE id = %s AND viewed = 0',(owner,)).fetchone()
        if row is None:
            return
        content=json.loads(row['content'])
        content['extra']['youtube']['captions']='unavailable'
        content['extra']['youtube'].pop('captions_digest',None)
        content['extra']['_reader']['captions']='unavailable'
        media=[dict(row) for row in store.db.execute('SELECT * FROM media_files WHERE publication_id = %s',(owner,))]
        previous=next(value for value in media if value['id']==media_id)
        identity=str(uuid4()) if previous['storage_key'] else media_id
        for value in media:
            if value['id']==media_id:
                value.update(id=identity,status='unavailable',reason=issue.message,storage_key=None,size_bytes=None)
        if identity!=media_id:
            if previous and previous['status']=='ready':
                content['extra']['_reader']['changed']=True
            for block in content['blocks']:
                if block.get('media_id')==media_id:
                    block['media_id']=identity
        self.project_header(content,media)
        with store.db:
            memberships.lock_graph(store.db)
            store.db.execute("UPDATE media_files SET id = %s , status = 'unavailable' , reason = %s , storage_key = NULL , size_bytes = NULL WHERE id = %s",(identity,issue.message,media_id))
            store.db.execute('UPDATE publications SET content = %s , media_ids = %s WHERE id = %s',(json.dumps(content,ensure_ascii=False),json.dumps([value['id'] for value in media]),owner))
            store.processing.reconcile_accepted_item(owner)
            publication_links.notify_parents(store,owner)
        store.processing.sync()

    def failed(self, media_id: str, issue: AdapterIssue) -> None:
        store = self.store
        owner = self.owner(media_id)
        if owner and issue.kind==AdapterErrorKind.notice and issue.code=='youtube-upcoming':
            store.viewed(owner)
            return
        if owner and issue.kind==AdapterErrorKind.notice and issue.code=='subtitles-unavailable':
            self._subtitles_absent(media_id,owner,issue)
            return
        changed = issue.kind==AdapterErrorKind.media_changed
        status = 'cancelled' if changed else collection_jobs.failure_status(issue)
        content = None
        subtitles = False
        keep_file = False
        if owner:
            publication = store.db.execute('SELECT content FROM publications WHERE id = %s AND viewed = 0', (owner,)).fetchone()
            if publication:
                content = json.loads(publication['content'])
                subtitles=youtube_media.subtitle_id(content)==media_id
                media = [dict(r) for r in store.db.execute('SELECT * FROM media_files WHERE publication_id = %s', (owner,))]
                old=next((value for value in media if value['id']==media_id),None)
                keep_file=bool(old and old['status']=='ready' and old['storage_key'] and existing_file(store.root,old['storage_key']) is not None)
                if subtitles and not keep_file:
                    content['extra']['youtube']['captions']='error'
                    content['extra']['_reader']['captions']='error'
                for value in media:
                    if value['id']==media_id:
                        value.update(reason=issue.message,status='ready' if keep_file else 'unavailable' if status=='unavailable' or changed else 'pending_download')
                self.project_header(content,media)
        with store.db:
            memberships.lock_graph(store.db)
            if content is not None:
                assert owner is not None
                store.db.execute('UPDATE media_files SET reason = %s , status = %s WHERE id = %s',
                    (issue.message,'ready' if keep_file else 'unavailable' if status=='unavailable' or changed else 'pending_download',media_id))
                store.db.execute('UPDATE publications SET content = %s WHERE id = %s', (json.dumps(content,ensure_ascii=False),owner))
                store.subscriptions.history.saved(owner,False)
                if subtitles:
                    store.processing.reconcile_accepted_item(owner)
                publication_links.notify_parents(store,owner)
        if content is not None:
            if subtitles:
                store.processing.sync()
