"""Individual local files: job ownership, durable acceptance and reader projection."""
from oko.storage.publication_document import stored_content
from oko.storage.reading import saved_content
import asyncio
from io import BufferedWriter
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import shutil
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
    def publication_rows(self, item: str) -> list[dict[str, Any]]:
        snapshot=material_read.current(self.store.db)
        rows = snapshot.media[item] if snapshot is not None and item in snapshot.media else self.store.db.execute("""SELECT m.*,j.id AS reader_job_id,j.status AS reader_job_status,j.reason_message AS reader_reason,j.reason_code AS reader_code
            FROM media_files m LEFT JOIN jobs j ON j.kind='download_media' AND j.item=m.id
            WHERE m.publication_id=%s""",(item,))
        values=[]
        for row in rows:
            value=dict(row)
            value['reason']=value['reader_reason'] or ('Субтитры недоступны' if value['reader_code']=='subtitles_absent' else '')
            values.append(value)
        return values



    @staticmethod
    def url(item: str, media: str) -> str:
        return f'/api/v3/materials/{item}/media/{media}'

    @staticmethod
    def projection_name(row: Any) -> str:
        return 'media-'+str(UUID(row['id']).int)+'.bin'

    def file(self, item: str, media: str) -> tuple[Path, str] | None:
        row = self.store.db.execute("SELECT m . * FROM media_files m JOIN publications i ON i . id = m . publication_id WHERE m . id = %s AND m . publication_id = %s AND m . status = 'ready' ", (media,item)).fetchone()
        if row and row['storage_key']:
            path = existing_file(self.store.root,row['storage_key'])
            if path is not None:
                return path, row['mime_type'] or 'application/octet-stream'
        return None


    def visible_file(self, item: str, media: str) -> tuple[Path,str] | None:
        visible=self.store.db.execute("""SELECT 1 FROM publications p WHERE p.id=%s AND (
            EXISTS (SELECT 1 FROM feeds f WHERE f.id=p.feed_id AND NOT f.deleted)
            OR EXISTS (SELECT 1 FROM publication_links l JOIN publications root ON root.id=l.source_publication_id
                JOIN feeds f ON f.id=root.feed_id WHERE l.target_publication_id=p.id AND NOT f.deleted))""",(item,)).fetchone()
        return self.file(item,media) if visible else None

    def owner(self, media: str) -> str | None:
        row = self.store.db.execute('SELECT publication_id FROM media_files WHERE id = %s', (media,)).fetchone()
        return str(row[0]) if row else None

    def publications_for_feed(self, feed: str) -> list[str]:
        rows=self.store.db.execute('SELECT DISTINCT p.id FROM publications p JOIN media_files m ON m.publication_id=p.id WHERE p.feed_id=%s',(feed,)).fetchall()
        return [str(row[0]) for row in rows]


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
        row = store.db.execute("SELECT m . * , i . platform , i . external_id , i . url FROM media_files m JOIN publications i ON i . id = m . publication_id WHERE m . id = %s AND ( m . status = 'pending_download' OR m . status = 'ready' AND %s = 1 ) ", (identity,revalidate)).fetchone()
        if row is None:
            return None
        temporary = store.root/'staging'/'_media'/(attempt+'-'+secrets.token_hex(8))
        temporary.parent.mkdir(parents=True, exist_ok=True)
        final = store.root/'media'/row['publication_id']/str(uuid4())
        self.active_paths.update((temporary, final))
        return MediaDownload(row['publication_id'], ExternalReference(ExternalKey(Platform(row['platform']), row['external_id']), row['url']),
            MediaReference(row['resource_key'], row['source_locator'], row['name'], row['mime_type'], row['size_bytes'], row['resource_identity'],source_url=row['source_url']), temporary, final)

    def current(self, identity: str, download: MediaDownload, problem_jobs: list[int] | None = None) -> bool:
        problem_column="" if problem_jobs is None else ", (SELECT j.id FROM jobs j WHERE j.kind='download_media' AND j.item=m.id AND j.status='running' AND (j.reason_message IS NOT NULL OR j.reason_code IS NOT NULL)) AS problem_job"
        row=self.store.db.execute('SELECT m . * , i . platform , i . external_id'+problem_column+' FROM media_files m JOIN publications i ON i . id = m . publication_id WHERE m . id = %s',(identity,)).fetchone()
        valid=bool(row and row['publication_id']==download.publication_id
            and row['platform']==download.publication.key.platform.value and row['external_id']==download.publication.key.external_id
            and (row['resource_key'],row['source_locator'],row['name'],row['mime_type'],row['size_bytes'],row['resource_identity'])==
                (download.media.key,download.media.locator,download.media.name,download.media.mime_type,download.media.size_bytes,download.media.resource_identity))
        if valid and row and problem_jobs is not None and row['problem_job'] is not None:
            problem_jobs.append(row['problem_job'])
        return valid

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
        if not store.db.transaction_depth:
            raise RuntimeError('Media acceptance requires an owning transaction')
        identity,saved,size=staged
        previous=store.db.execute('SELECT * FROM media_files WHERE id = %s',(media_id,)).fetchone()
        if not self.current(media_id,download):
            raise ValueError('Media owner changed')
        assert previous is not None
        row = store.db.execute('SELECT content,status FROM publications WHERE id = %s', (download.publication_id,)).fetchone()
        if row is None:
            raise ValueError('Media owner changed')
        content = saved_content(row['content'],mutable=True)
        media = self.publication_rows(download.publication_id)
        for value in media:
            if value['id']==media_id:
                value.update(id=identity,status='ready',reason='',name=actual.name,storage_key=str(saved.relative_to(store.root)),size_bytes=size,
                             source_locator=actual.locator,source_url=actual.source_url if actual.source_url is not None else value['source_url'],mime_type=actual.mime_type)
        subtitles=youtube_media.subtitle_id(content)==media_id
        for block in content.get('blocks',[]):
            if block.get('type')=='media' and block.get('media_id')==media_id:
                block['source_url']=actual.source_url if actual.source_url is not None else previous['source_url']
                if actual.publication_url is not None:block['publication_url']=actual.publication_url
        if identity!=media_id:
            for block in content['blocks']:
                if block.get('media_id')==media_id:
                    block['media_id']=identity
        memberships.lock_graph(store.db)
        problem_jobs: list[int]=[]
        if not self.current(media_id,download,problem_jobs):
            raise ValueError('Media owner changed')
        # Capture the real owner and affected roots before the file ID changes.
        from oko.storage.media_job_problems import previous_candidates as managed_candidates
        problem_job=problem_jobs[0] if problem_jobs else None
        previous_problem=managed_candidates(store,problem_job,store.wall_clock()) if problem_job is not None else []
        source_before: set[str]=set()
        source_evidence: dict[str,set[tuple[str,str]]]={}
        if problem_job is not None and identity!=media_id:
            from oko.storage import source_groups
            source_before=source_groups.job_keys(store,problem_job)
            if source_before:source_evidence=source_groups.job_evidence_many(store,[(problem_job,None)])
        if identity!=media_id:
            job=store.db.execute("SELECT id FROM jobs WHERE kind='download_media' AND item=%s AND processor_visible",(media_id,)).fetchone()
            if job:
                store.problems.capture_processor_history(job['id'],store.wall_clock())
        store.db.execute("UPDATE media_files SET id = %s , status = 'ready' , storage_key = %s , size_bytes = %s , source_locator = %s , source_url = %s , mime_type = %s , name = %s WHERE id = %s",
            (identity,str(saved.relative_to(store.root)),size,actual.locator,actual.source_url if actual.source_url is not None else previous['source_url'],actual.mime_type,actual.name,media_id))
        store.db.execute('UPDATE publications SET content = %s , media_ids = %s WHERE id = %s', (json.dumps(stored_content(content),ensure_ascii=False),json.dumps([value['id'] for value in media]),download.publication_id))
        from oko.storage.incomplete_index import save_class
        save_class(store.db,download.publication_id,content)
        from oko.storage.history_publication import loaded as history_loaded
        store.subscriptions.history.saved(download.publication_id,history_loaded(store.db,store.root,download.publication_id))
        if subtitles:
            store.processing.reconcile_accepted_item(download.publication_id)
        publication_links.notify_parents(store,download.publication_id)
        if problem_job is not None and previous_problem:
            store.problems.sync_media_problem(problem_job,previous_problem,store.wall_clock(),successful=True)
        if problem_job is not None and source_before:
            source_groups.sync(store,'download_media',source_before,store.wall_clock(),exclude={problem_job},evidence=source_evidence)
        store.fault('before_media_commit')
        return True

    def release(self, download: MediaDownload) -> None:
        self.active_paths.difference_update((download.temporary, download.final))
        download.temporary.unlink(missing_ok=True)

    def retired_file_cleaned(self, key: str | None) -> bool:
        if key is None:
            return True
        path=resolve_key(self.store.root,key)
        return bool(path is not None and path not in self.active_paths and not path.exists() and not path.is_symlink())

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
        staging_root=root/'staging'
        for owner in staging_root.iterdir():
            if not owner.is_dir() or owner.is_symlink() or owner.name=='_media':
                continue
            for directory in owner.glob('rebuild-*'):
                if directory.is_dir() and not directory.is_symlink() and not any(directory==path or directory in path.parents for path in self.active_paths):
                    shutil.rmtree(directory)

    def _subtitles_absent(self, media_id: str, owner: str, issue: AdapterIssue) -> str | None:
        store=self.store
        row=store.db.execute('SELECT content FROM publications WHERE id = %s',(owner,)).fetchone()
        if row is None:
            return None
        content=saved_content(row['content'],mutable=True)
        media=self.publication_rows(owner)
        previous=next(value for value in media if value['id']==media_id)
        identity=str(uuid4()) if previous['storage_key'] else media_id
        for value in media:
            if value['id']==media_id:
                value.update(id=identity,status='unavailable',reason=issue.message,storage_key=None,size_bytes=None)
        if identity!=media_id:
            for block in content['blocks']:
                if block.get('media_id')==media_id:
                    block['media_id']=identity
        memberships.lock_graph(store.db)
        job=store.db.execute("SELECT id,processor_visible,reason_code FROM jobs WHERE kind='download_media' AND item=%s",(media_id,)).fetchone()
        source_before: set[str]=set()
        source_evidence: dict[str,set[tuple[str,str]]]={}
        if job and job['processor_visible']:
            store.problems.capture_processor_history(job['id'],store.wall_clock())
        if job and identity!=media_id and (job['reason_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            source_before=source_groups.job_keys(store,job['id'])
            if source_before:source_evidence=source_groups.job_evidence_many(store,[(job['id'],None)])
        store.db.execute("UPDATE media_files SET id = %s , status = 'unavailable' , storage_key = NULL , size_bytes = NULL WHERE id = %s",(identity,media_id))
        store.db.execute('UPDATE publications SET content = %s , media_ids = %s WHERE id = %s',(json.dumps(stored_content(content),ensure_ascii=False),json.dumps([value['id'] for value in media]),owner))
        from oko.storage.incomplete_index import save_class
        save_class(store.db,owner,content)
        store.processing.reconcile_accepted_item(owner)
        publication_links.notify_parents(store,owner)
        if job and source_before:source_groups.sync(store,'download_media',source_before,store.wall_clock(),exclude={job['id']},evidence=source_evidence)
        store.processing.sync()
        return identity

    def failed(self, media_id: str, issue: AdapterIssue) -> tuple[str,str] | None:
        store = self.store
        if not store.db.transaction_depth:
            raise RuntimeError('Media failure requires an owning transaction')
        owner = self.owner(media_id)
        if owner and issue.kind==AdapterErrorKind.notice and issue.code=='youtube-upcoming':
            store.viewed(owner)
            return None
        if owner and issue.kind==AdapterErrorKind.notice and issue.code=='subtitles-unavailable':
            identity=self._subtitles_absent(media_id,owner,issue)
            return (owner,identity) if identity else None
        changed = issue.kind==AdapterErrorKind.media_changed
        status = 'cancelled' if changed else collection_jobs.failure_status(issue)
        content = None
        subtitles = False
        keep_file = False
        if owner:
            publication = store.db.execute('SELECT content FROM publications WHERE id = %s', (owner,)).fetchone()
            if publication:
                content = saved_content(publication['content'],mutable=True)
                subtitles=youtube_media.subtitle_id(content)==media_id
                media = self.publication_rows(owner)
                old=next((value for value in media if value['id']==media_id),None)
                keep_file=bool(old and old['status']=='ready' and old['storage_key'] and existing_file(store.root,old['storage_key']) is not None)
                for value in media:
                    if value['id']==media_id:
                        value.update(reason=issue.message,status='ready' if keep_file else 'unavailable' if status=='unavailable' or changed else 'pending_download')
        memberships.lock_graph(store.db)
        if content is not None:
            assert owner is not None
            store.db.execute('UPDATE media_files SET status = %s WHERE id = %s',
                ('ready' if keep_file else 'unavailable' if status=='unavailable' or changed else 'pending_download',media_id))
            store.db.execute('UPDATE publications SET content = %s WHERE id = %s', (json.dumps(stored_content(content),ensure_ascii=False),owner))
            from oko.storage.incomplete_index import save_class
            save_class(store.db,owner,content)
            store.subscriptions.history.saved(owner,False)
            if subtitles:
                store.processing.reconcile_accepted_item(owner)
            publication_links.notify_parents(store,owner)
        if content is not None:
            if subtitles:
                store.processing.sync()
        return None
