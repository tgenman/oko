"""Independent fetch/media completion must not lose a newer resource description."""
import asyncio
from dataclasses import replace
import json
import pytest
from oko.collection.contracts import Material,SourceError,FetchResult
from oko.collection.adapters import AdapterException,AdapterIssue,AdapterErrorKind
from oko.services.collection import CollectionService
from oko.services.media import MediaService
from tests.support.source import StructuredSource,SyntheticMaterial,Image
from tests.support.worker_library import worker_library,collection_worker,saved


def image_material(source,store,item,text='Useful text'):
    return source.describe(store.publication(item),SyntheticMaterial('Title',text,images=(Image('image-1.png',b'old'),)))


def accept_material(store,service,item,material):
    with store.jobs.transaction(lambda:True):
        error,effects=service.collected(item,material,manual=False)
        assert error is None
        store.jobs.apply_collection(effects)


def test_retry_does_not_overwrite_a_file_left_by_an_uncommitted_download(tmp_path):
    store,item,execution,tasks=worker_library(tmp_path)
    try:
        service=CollectionService(execution.require_store)
        source=StructuredSource(None)
        accept_material(store,service,item,image_material(source,store,item))
        job=store.jobs.next_job(store.clock(),feeds=['demo'],media_only=True)
        first=store.media.begin(job.item,'first',False)
        first.temporary.write_bytes(b'abandoned')
        store.media.stage(job.item,first,replace(first.media,size_bytes=9))
        store.media.release(first)

        retry=store.media.begin(job.item,'retry',False)
        retry.temporary.write_bytes(b'new bytes')
        actual=replace(retry.media,size_bytes=9)
        staged=store.media.stage(job.item,retry,actual)
        with store.jobs.transaction(lambda:True):
            store.media.accept(job.item,retry,actual,staged)
            store.jobs.finish(job)
        store.media.release(retry)

        assert first.final.read_bytes()==b'abandoned'
        saved_file=store.media.file(item,job.item)
        assert saved_file is not None and saved_file[0].read_bytes()==b'new bytes'
    finally:
        store.db.close()


@pytest.mark.parametrize('failed',[False,True])
def test_old_download_success_or_error_cannot_replace_a_refreshed_locator(tmp_path,failed):
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        class Held(StructuredSource):
            def publication_adapter(self):
                class Provider:
                    async def download_media(self,publication,media,output):
                        entered.set()
                        await release.wait()
                        if failed:raise AdapterException(AdapterIssue('old-locator','Old locator failed',AdapterErrorKind.temporary))
                        output.write(b'old')
                        return media
                return Provider()
        store,item,execution,tasks=worker_library(tmp_path)
        source=Held(None)
        worker=collection_worker(tmp_path,store,source)
        service=worker.service
        material=image_material(source,store,item)
        accept_material(store,service,item,material)
        job=store.jobs.next_job(store.clock(),feeds=['demo'],media_only=True)
        assert job
        task=asyncio.create_task(worker._execute(job))
        await asyncio.wait_for(entered.wait(),2)
        old=material.adapter_content.media[0]
        changed=replace(material,adapter_content=replace(material.adapter_content,media=(replace(old,locator='new-locator',name='new-file.png'),)))
        accept_material(store,service,item,changed)
        assert store.jobs.valid(job)
        release.set()
        await task
        row=store.db.execute('SELECT * FROM media_files WHERE id = %s',(job.item,)).fetchone()
        assert row['source_locator']=='new-locator' and row['name']=='new-file.png'
        assert row['status']=='pending_download' and row['storage_key'] is None
        replacement=store.db.execute('SELECT status , generation , error FROM jobs WHERE id = %s',(job.id,)).fetchone()
        assert replacement['status']=='pending' and replacement['generation']>job.generation and replacement['error'] is None
        assert not list((tmp_path/'staging'/'_media').iterdir())
        store.db.close()
    asyncio.run(scenario())


def test_media_id_replacement_does_not_invalidate_a_running_content_fetch(tmp_path):
    store,item,execution,tasks=worker_library(tmp_path)
    service=CollectionService(execution.require_store)
    source=StructuredSource(None)
    accept_material(store,service,item,image_material(source,store,item))
    media_job=store.jobs.next_job(store.clock(),feeds=['demo'],media_only=True)
    download=store.media.begin(media_job.item,'first',False)
    download.temporary.write_bytes(b'old')
    staged=store.media.stage(media_job.item,download,download.media)
    with store.jobs.transaction(lambda:True):
        store.media.accept(media_job.item,download,download.media,staged)
        store.jobs.finish(media_job)
    store.media.release(download)
    with store.jobs.transaction(lambda:True):
        store.jobs.enqueue_fetch(item)
        store.jobs.revalidate_media(media_job.item)
    fetch=store.jobs.next_job(store.clock(),feeds=['demo'])
    assert fetch.kind=='fetch'
    revision=service.revision(item)
    media_job=store.jobs.next_job(store.clock(),feeds=['demo'],media_only=True)
    download=store.media.begin(media_job.item,'second',True)
    download.temporary.write_bytes(b'replacement')
    actual=replace(download.media,size_bytes=len(b'replacement'))
    staged=store.media.stage(media_job.item,download,actual)
    with store.jobs.transaction(lambda:True):
        store.media.accept(media_job.item,download,actual,staged)
        store.jobs.finish(media_job)
    store.media.release(download)
    assert store.db.execute('SELECT id FROM media_files').fetchone()[0]!=media_job.item
    assert store.jobs.valid(fetch) and service.current(item,revision)
    store.db.close()


@pytest.mark.parametrize('failed',[False,True])
def test_fetch_rejects_an_older_description_without_remaining_running(tmp_path,failed):
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        class Held:
            changes=None
            async def fetch(self,publication):
                entered.set()
                await release.wait()
                if failed:raise SourceError('Old fetch failed')
                return FetchResult(Material('Title','Old response'))
        store,item,execution,tasks=worker_library(tmp_path)
        source=StructuredSource(Held())
        worker=collection_worker(tmp_path,store,source)
        with store.jobs.transaction(lambda:True):store.jobs.enqueue_fetch(item)
        job=store.jobs.next_job(store.clock(),feeds=['demo'])
        assert job.kind=='fetch'
        task=asyncio.create_task(worker._execute(job))
        await asyncio.wait_for(entered.wait(),2)
        changed=source.describe(store.publication(item),Material('Title','Newer description'))
        accept_material(store,worker.service,item,changed)
        release.set()
        await task
        assert 'Newer description' in json.dumps(saved(store,item)['content'])
        row=store.db.execute('SELECT status , error FROM jobs WHERE id = %s',(job.id,)).fetchone()
        assert tuple(row)==('cancelled',None)
        assert not store.db.in_transaction
        store.db.close()
    asyncio.run(scenario())


def test_two_workers_ready_to_write_commit_sequentially_on_one_connection(tmp_path):
    from tests.support.worker_library import workers
    from tests.support.filters import transport
    async def scenario():
        media_started,model_started,release=asyncio.Event(),asyncio.Event(),asyncio.Event()
        class Held(StructuredSource):
            def publication_adapter(self):
                class Provider:
                    async def download_media(self,publication,media,output):
                        media_started.set()
                        await release.wait()
                        output.write(b'old')
                        return media
                return Provider()
        native=transport([],theses=True)
        async def model(request):
            if request.url.path=='/v1/chat/completions':model_started.set();await release.wait()
            return native.handler(request)
        import httpx
        store,item,execution,tasks=worker_library(tmp_path)
        source=Held(None)
        collection=collection_worker(tmp_path,store,source)
        accept_material(store,collection.service,item,image_material(source,store,item))
        media_job=store.jobs.next_job(store.clock(),feeds=['demo'],media_only=True)
        media_task=asyncio.create_task(collection._execute(media_job))
        filtering=workers(collection.execution,collection.jobs,httpx.MockTransport(model))[0]
        await filtering.tick()
        await asyncio.wait_for(asyncio.gather(media_started.wait(),model_started.wait()),2)
        trace=[]
        store.db.set_trace_callback(trace.append)
        release.set()
        await asyncio.gather(media_task,collection.jobs._ai)
        store.db.set_trace_callback(None)
        active=False
        began=0
        for statement in trace:
            if statement.startswith('BEGIN'):
                assert not active
                active=True;began+=1
            elif statement in ('COMMIT','ROLLBACK'):
                assert active
                active=False
        assert began>=2 and not active and not store.db.in_transaction
        assert saved(store,item)['filtered_content']
        assert store.db.execute('SELECT status FROM media_files').fetchone()[0]=='ready'
        assert store.db.execute("SELECT count(*) FROM jobs WHERE kind IN ('filter','download_media') AND status='done'").fetchone()[0]==2
        store.db.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['absolute', 'parent', 'symlink', 'internal'])
def test_media_key_cannot_read_outside_library(tmp_path, kind):
    store,item,execution,tasks=worker_library(tmp_path/'library')
    try:
        service=CollectionService(execution.require_store)
        source=StructuredSource(None)
        accept_material(store,service,item,image_material(source,store,item))
        job=store.jobs.next_job(store.clock(),feeds=['demo'],media_only=True)
        external=tmp_path/'outside.txt'
        external.write_text('private')
        if kind == 'absolute':
            key = str(external)
        elif kind == 'parent':
            key = '../outside.txt'
        elif kind == 'symlink':
            (store.root/'media'/'escape').symlink_to(tmp_path, target_is_directory=True)
            key = 'media/escape/outside.txt'
        else:
            external = store.root/'private.txt'
            external.write_text('private')
            key = 'private.txt'
        with store.jobs.transaction(lambda:True):
            store.db.execute("UPDATE media_files SET status='ready',storage_key=%s WHERE id=%s", (key,job.item))
        assert store.media.file(item,job.item) is None
        assert external.read_text() == 'private'
    finally:
        store.db.close()


def test_reader_http_does_not_serve_a_media_key_outside_the_library(tmp_path):
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from tests.support.v3 import TestClient
    root = tmp_path/'library'
    store,item,execution,tasks=worker_library(root)
    try:
        service=CollectionService(execution.require_store)
        source=StructuredSource(None)
        accept_material(store,service,item,image_material(source,store,item))
        job=store.jobs.next_job(store.clock(),feeds=['demo'],media_only=True)
        secret=tmp_path/'outside.txt'
        secret.write_text('private')
        with store.jobs.transaction(lambda:True):
            store.db.execute("UPDATE media_files SET status='ready',storage_key=%s WHERE id=%s", (str(secret),job.item))
    finally:
        store.db.close()
    with TestClient(create_app(root,token='test',api_only=True),headers=HEADERS) as client:
        response=client.get('/api/v3/materials/'+item+'/media/'+job.item)
        assert response.status_code == 404
        assert 'private' not in response.text
        assert client.get('/api/v3/publication/'+item).status_code == 200


def test_failed_media_attempt_and_diagnostic_roll_back_together(tmp_path, monkeypatch):
    class Failing(StructuredSource):
        def publication_adapter(self):
            class Provider:
                async def download_media(self,publication,media,output):
                    raise AdapterException(AdapterIssue('synthetic-failure','Synthetic failure',AdapterErrorKind.temporary))
            return Provider()
    store,item,execution,tasks=worker_library(tmp_path)
    try:
        source=Failing(None)
        worker=collection_worker(tmp_path,store,source)
        accept_material(store,worker.service,item,image_material(source,store,item))
        job=store.jobs.next_job(store.clock(),feeds=['demo'],media_only=True)
        def fail_diagnostic(*args, **kwargs):
            raise RuntimeError('diagnostic failure')
        monkeypatch.setattr(worker,'_finish_collection',fail_diagnostic)
        with pytest.raises(RuntimeError, match='diagnostic failure'):
            asyncio.run(worker._execute(job))
        row=store.db.execute('SELECT status,error FROM jobs WHERE id=%s',(job.id,)).fetchone()
        assert row[:] == ('running',None)
        media=store.db.execute('SELECT status,reason FROM media_files WHERE id=%s',(job.item,)).fetchone()
        assert media[:] == ('pending_download','')
    finally:
        store.db.close()
