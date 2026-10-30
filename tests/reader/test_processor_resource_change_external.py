import json
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.domain.collection import CollectionEffects


def test_publish_same_media_changes_to_caption_during_pause(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,viewed,content) VALUES ('p','youtube','p','https://example.test/p',0,%s)",(json.dumps({'blocks':[], 'extra':{'_reader':{'title':'P'}}}),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image:1','https://example.test/file','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','original failure','processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,100)
            identity=store.problems.processor_current()['id']
        store.youtube.blocked(100,900)
        with transaction(store.db):
            row=dict(store.db.execute("SELECT id,publication_id,resource_key,source_locator,name,mime_type,size_bytes,storage_key,status,reason,resource_identity FROM media_files WHERE id='m'").fetchone())
            row['resource_key']='youtube:subtitles'
            effects=CollectionEffects()
            store.publications.publish('p',({'blocks':[], 'extra':{'_reader':{'title':'P'}}},[row]),'fingerprint',effects)
            store.jobs.apply_collection(effects)
        saved=store.db.execute('SELECT finished,body FROM reader_problems WHERE id=%s',(identity,)).fetchone()
        body=json.loads(saved['body'])
        assert saved['finished'] is not None
        assert body.get('reason')=='original failure',body
        assert any(obj.get('id')=='p' for obj in body['objects']),body
    finally: store.db.close()


def test_resource_change_rollback_preserves_old_occurrence(tmp_path,postgres_url):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,viewed,content) VALUES ('p','youtube','p','https://example.test/p',0,%s)",(json.dumps({'blocks':[], 'extra':{'_reader':{'title':'P'}}}),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image:1','https://example.test/file','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','original failure','processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,100)
            identity=store.problems.processor_current()['id']
        store.youtube.blocked(100,900)
        with pytest.raises(RuntimeError,match='abort'):
            with transaction(store.db):
                row=dict(store.db.execute("SELECT id,publication_id,resource_key,source_locator,name,mime_type,size_bytes,storage_key,status,reason,resource_identity FROM media_files WHERE id='m'").fetchone())
                row['resource_key']='youtube:subtitles'
                effects=CollectionEffects()
                store.publications.publish('p',({'blocks':[], 'extra':{'_reader':{'title':'P'}}},[row]),'fingerprint',effects)
                store.jobs.apply_collection(effects)
                raise RuntimeError('abort')
        assert store.db.execute("SELECT resource_key FROM media_files WHERE id='m'").fetchone()[0]=='image:1'
        assert store.problems.processor_current()['id']==identity
        assert store.problems.processor_configuration_candidate(100)['reason']=='original failure'
        assert not store.problems._processor_transaction_captures
    finally: store.db.close()


def test_adapter_key_change_creates_new_media_id(tmp_path,postgres_url):
    from dataclasses import replace
    from oko.collection.contracts import Publication
    from tests.support.source import StructuredSource,SyntheticMaterial,Image,publication_packet
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.ensure_demo(); publication=Publication('p','https://example.test/p',1)
        with transaction(store.db):
            effects=store.collect_discovery([publication_packet(publication)],'{}');store.jobs.apply_collection(effects)
        item=store.db.execute("SELECT id FROM publications WHERE url='https://example.test/p'").fetchone()[0]
        described=StructuredSource(None).describe(publication,SyntheticMaterial('Title','Text',images=(Image('image:1',b'x'),)))
        with transaction(store.db):
            _,effects=store.collect_material(item,described);store.jobs.apply_collection(effects)
        old=store.db.execute('SELECT id FROM media_files WHERE publication_id=%s',(item,)).fetchone()[0]
        content=described.adapter_content
        assert content is not None
        ref=replace(content.media[0],key='youtube:subtitles')
        changed_content=dict(content.content)
        changed_content['blocks']=[dict(block,ref={'$media':'youtube:subtitles'}) if block.get('type')=='media' else block for block in content.content['blocks']]
        changed=replace(described,adapter_content=replace(content,media=(ref,),content=changed_content))
        _,media=store.publications.prepare(item,changed)
        assert media[0]['id'] != old
    finally: store.db.close()
