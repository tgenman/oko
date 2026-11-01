"""Independent fetch/media completion must not lose a newer resource description."""
import asyncio
from dataclasses import replace
import json
import pytest
from oko.collection.adapters import AdapterException,AdapterIssue,AdapterErrorKind
from oko.services.collection import CollectionService
from tests.support.source import StructuredSource,SyntheticMaterial,Image
from tests.support.worker_library import worker_library, collection_worker


def image_material(source,store,item,text='Useful text'):
    return source.describe(store.publication(item),SyntheticMaterial('Title',text,images=(Image('image-1.png',b'old'),)))


def accept_material(store,service,item,material):
    with store.jobs.transaction(lambda:True):
        error,effects=service.collected(item,material,manual=False)
        assert error is None
        store.jobs.apply_collection(effects)


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


def test_cancelled_caption_attempt_cannot_block_or_release_new_owner(tmp_path):
    from oko.collection.youtube.access import CaptionsDeferred
    from oko.storage.job_types import Job
    from oko.workers.collection import CaptionAttempt
    async def scenario():
        store,item,execution,tasks=worker_library(tmp_path)
        try:
            calls=[]
            class Captions:
                async def fetch(self,video):
                    calls.append(video)
                    return 'Synthetic captions'
            worker=collection_worker(tmp_path,store,StructuredSource(None))
            clock=[1800000000.0]
            worker._wall_clock=lambda:clock[0]
            worker._clock=lambda:clock[0]
            store.wall_clock=lambda:clock[0]
            worker._youtube_captions=Captions()
            with store.db:
                for identity in ('caption-one','caption-two','caption-three'):
                    store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES (%s,%s,%s,'synthetic','caption.txt','pending_download')",(identity,item,identity))
                    store.db.execute("INSERT INTO jobs(kind,item,status) VALUES ('download_media',%s,'running')",(identity,))
            def job(identity):
                row=store.db.execute("SELECT id,kind,item,generation FROM jobs WHERE kind='download_media' AND item=%s",(identity,)).fetchone()
                return Job(row['id'],row['kind'],row['item'],row['generation'])
            old_job=job('caption-one')
            old=CaptionAttempt(worker,store,old_job)
            assert await old.fetch('old')=='Synthetic captions'
            with store.jobs.transaction(lambda:True):
                store.jobs.cancel(old_job)
            clock[0]+=11
            new=CaptionAttempt(worker,store,job('caption-two'))
            assert await new.fetch('new')=='Synthetic captions'
            old.release()
            clock[0]+=11
            third=CaptionAttempt(worker,store,job('caption-three'))
            with pytest.raises(CaptionsDeferred):
                await third.fetch('third')
            assert calls==['old','new']
            new.release()
        finally:
            store.db.close()
    asyncio.run(scenario())
