from tests.support.storage import Store


def test_turning_off_captions_closes_processor_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO folders(id,title) VALUES ('folder','Folder') ON CONFLICT DO NOTHING")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','folder','youtube','youtube','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('p',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','captions','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('download_media','m','pending','processor-config','failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        store.jobs.prepare_youtube('f',False)
        row=store.db.execute('SELECT status,problem_code,processor_visible FROM jobs WHERE id=%s',(job,)).fetchone()
        assert row['status']=='cancelled' and row['problem_code'] is None
        assert not row['processor_visible']
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_caption_repair_keeps_failure_in_history(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        content={'blocks':[{'type':'media','role':'subtitles','media_id':'m'}],'extra':{'youtube':{},'_reader':{}}}
        with store.db:
            store.db.execute("INSERT INTO folders(id,title) VALUES ('folder','Folder') ON CONFLICT DO NOTHING")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','folder','youtube','youtube','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed,platform,content) VALUES ('p',0,'youtube',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES ('m','p','youtube:subtitles','x','captions','ready',NULL)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('download_media','m','failed','processor-config','original failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        old=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()[0]
        store.processing._repairs.add('p');store.processing._repair_one()
        row=store.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old,)).fetchone()
        body=json.loads(row['body'])
        print('problem',body,'finished',row['finished'],'job',dict(store.db.execute('SELECT status,problem_code,error,processor_visible FROM jobs WHERE id=%s',(job,)).fetchone()))
        assert row['finished'] is not None
        assert body.get('reason')=='original failure' and any(obj.get('id')=='p' for obj in body['objects'])
    finally:store.db.close()


def test_removing_one_shared_feed_keeps_processor_failure_history(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO folders(id,title) VALUES ('folder','Folder') ON CONFLICT DO NOTHING")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','folder','rss','rss','connected',ARRAY[]::text[]),('b','b','B','folder','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,processing_metadata) VALUES ('p',0,'rss','p',%s)",(json.dumps({'_pass':{'feed_id':'a'}}),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('filter','p','pending','processor-config','original failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        old=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()[0]
        store.remove_feed('a')
        assert store.db.execute("SELECT 1 FROM publications WHERE id='p' AND viewed=0").fetchone()
        row=store.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old,)).fetchone()
        body=json.loads(row['body'])
        print('problem',body,'finished',row['finished'],'job',dict(store.db.execute('SELECT status,problem_code,error,processor_visible FROM jobs WHERE id=%s',(job,)).fetchone()))
        assert row['finished'] is not None
        assert body.get('reason')=='original failure' and any(obj.get('id')=='p' for obj in body['objects'])
    finally:store.db.close()


def test_removing_shared_feed_keeps_other_processor_occurrence(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO folders(id,title) VALUES ('folder','Folder') ON CONFLICT DO NOTHING")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','folder','rss','rss','connected',ARRAY[]::text[]),('b','b','B','folder','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,processing_metadata) VALUES ('p',0,'rss','p',%s)",(json.dumps({'_pass':{'feed_id':'a'}}),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
            first=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('filter','p','pending','processor-config','failure') RETURNING id").fetchone()[0]
            second=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('read','other','pending','processor-config','failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([first,second],None,1)
        occurrence=store.problems.processor_current()['id']
        store.remove_feed('a')
        assert store.problems.processor_current()['id']==occurrence
        assert store.db.execute('SELECT processor_visible FROM jobs WHERE id=%s',(second,)).fetchone()[0]
        assert {obj['id'] for obj in store.problems.processor_configuration_candidate(2)['objects']}=={'other'}
    finally:store.db.close()


def test_discover_processor_problem_not_hidden_by_viewed_publication_with_same_id(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,kind,transport,connection,original_urls) VALUES ('same','same','feed','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('same',1)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('discover','same','failed','processor-config','failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        row=store.db.execute('SELECT processor_visible FROM jobs WHERE id=%s',(job,)).fetchone()
        assert row['processor_visible']
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_read_processor_problem_not_hidden_by_viewed_publication_with_same_id(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,kind,transport,connection,original_urls) VALUES ('same','same','feed','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('same',1)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('read','same','failed','processor-config','failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        assert store.db.execute('SELECT processor_visible FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert store.problems.processor_configuration_candidate(2)['objects']==[{'kind':'feed','id':'same'}]
    finally:store.db.close()


def test_subtitle_notice_replacement_keeps_old_material_history(tmp_path,postgres_url):
    import json
    from oko.collection.adapters import AdapterIssue,AdapterErrorKind
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        content={'blocks':[{'type':'media','role':'subtitles','media_id':'m'}],'extra':{'youtube':{},'_reader':{}}}
        with store.db:
            store.db.execute("INSERT INTO folders(id,title) VALUES ('folder','Folder') ON CONFLICT DO NOTHING")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','folder','youtube','youtube','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed,platform,content) VALUES ('p',0,'youtube',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES ('m','p','youtube:subtitles','x','captions','ready','media/old')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('download_media','m','running',1,'processor-config','original failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        old=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):
            store.media.failed('m',AdapterIssue('subtitles-unavailable','not available',AdapterErrorKind.notice))
            store.jobs.finish(Job(job,'download_media','m',1))
        row=store.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old,)).fetchone();body=json.loads(row['body'])
        assert row['finished'] is not None
        assert any(obj.get('id')=='p' for obj in body['objects']),body
    finally:store.db.close()


def test_subtitle_capture_rolls_back_without_stale_transaction_state(tmp_path,postgres_url):
    import json
    import pytest
    from oko.collection.adapters import AdapterIssue,AdapterErrorKind
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        content={'blocks':[{'type':'media','role':'subtitles','media_id':'m'}],'extra':{'youtube':{},'_reader':{}}}
        with store.db:
            store.db.execute("INSERT INTO publications(id,viewed,platform,content) VALUES ('p',0,'youtube',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES ('m','p','youtube:subtitles','x','captions','ready','media/old')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('download_media','m','running',1,'processor-config','failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.media.failed('m',AdapterIssue('subtitles-unavailable','notice',AdapterErrorKind.notice))
                assert job in store.problems._processor_transaction_captures
                raise RuntimeError('rollback')
        assert job not in store.problems._processor_transaction_captures
        assert store.db.execute("SELECT 1 FROM media_files WHERE id='m'").fetchone()
        with transaction(store.db):store.jobs.finish(Job(job,'download_media','m',1))
        assert not store.problems._processor_transaction_captures
    finally:store.db.close()


def test_subtitle_notice_real_worker_order_keeps_failure_history(tmp_path,postgres_url):
    import json
    from oko.collection.adapters import AdapterIssue,AdapterErrorKind
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        content={'blocks':[{'type':'media','role':'subtitles','media_id':'m'}],'extra':{'youtube':{},'_reader':{}}}
        with store.db:
            store.db.execute("INSERT INTO publications(id,viewed,platform,content) VALUES ('p',0,'youtube',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES ('m','p','youtube:subtitles','x','captions','ready','media/old')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('download_media','m','running',1,'processor-config','original failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        old=store.problems.processor_current()['id']
        attempt=Job(job,'download_media','m',1)
        with transaction(store.db):
            store.media.failed('m',AdapterIssue('subtitles-unavailable','not available',AdapterErrorKind.notice))
            store.jobs.reconcile_cleanup()
            if store.jobs.valid(attempt):store.jobs.finish(attempt)
        row=store.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old,)).fetchone();body=json.loads(row['body'])
        assert row['finished'] is not None
        assert body.get('reason')=='original failure' and any(obj.get('id')=='p' for obj in body['objects'])
    finally:store.db.close()


def test_subtitle_notice_worker_order_keeps_other_group_member(tmp_path,postgres_url):
    import json
    from oko.collection.adapters import AdapterIssue,AdapterErrorKind
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        content={'blocks':[{'type':'media','role':'subtitles','media_id':'m'}],'extra':{'youtube':{},'_reader':{}}}
        with store.db:
            store.db.execute("INSERT INTO publications(id,viewed,platform,content) VALUES ('p',0,'youtube',%s),('other',0,'rss','{}')",(json.dumps(content),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES ('m','p','youtube:subtitles','x','captions','ready','media/old')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('download_media','m','running',1,'processor-config','failure') RETURNING id").fetchone()[0]
            other=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('filter','other','pending','processor-config','other failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job,other],None,1)
        old=store.problems.processor_current()['id'];attempt=Job(job,'download_media','m',1)
        with transaction(store.db):
            store.media.failed('m',AdapterIssue('subtitles-unavailable','not available',AdapterErrorKind.notice))
            store.jobs.reconcile_cleanup()
            if store.jobs.valid(attempt):store.jobs.finish(attempt)
        assert store.problems.processor_current()['id']==old
        assert {o['id'] for o in store.problems.processor_configuration_candidate(2)['objects']}=={'other'}
    finally:store.db.close()


def test_media_accept_replacement_preserves_old_processor_history(tmp_path,postgres_url):
    import json,uuid
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    old_media=str(uuid.uuid4());new_media=str(uuid.uuid4())
    try:
        content={'blocks':[{'type':'media','role':'image','media_id':old_media}],'extra':{'_reader':{'full':True,'reason':'','attachments':[]}}}
        with store.db:
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url,content) VALUES ('p',0,'rss','p','https://p.test',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES (%s,'p','image','x','image','ready','media/old')",(old_media,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('download_media',%s,'running',1,'processor-config','original failure') RETURNING id",(old_media,)).fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        old=store.problems.processor_current()['id']
        download=store.media.begin(old_media,'attempt',True)
        download.final.parent.mkdir(parents=True,exist_ok=True)
        download.final.write_bytes(b'synthetic')
        with transaction(store.db):
            store.media.accept(old_media,download,download.media,(new_media,download.final,9))
            store.jobs.finish(Job(job,'download_media',old_media,1))
        body=json.loads(store.db.execute('SELECT body FROM reader_problems WHERE id=%s',(old,)).fetchone()[0])
        assert body.get('reason')=='original failure' and any(obj.get('id')=='p' for obj in body['objects']),body
    finally:store.db.close()


def test_media_accept_capture_is_cleared_on_rollback(tmp_path,postgres_url):
    import json,uuid,pytest
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    old_media=str(uuid.uuid4());new_media=str(uuid.uuid4())
    try:
        content={'blocks':[{'type':'media','role':'image','media_id':old_media}],'extra':{'_reader':{'full':True,'reason':'','attachments':[]}}}
        with store.db:
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url,content) VALUES ('p',0,'rss','p','https://p.test',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES (%s,'p','image','x','image','ready','media/old')",(old_media,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('download_media',%s,'running',1,'processor-config','failure') RETURNING id",(old_media,)).fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        old=store.problems.processor_current()['id']
        download=store.media.begin(old_media,'attempt',True)
        download.final.parent.mkdir(parents=True,exist_ok=True)
        download.final.write_bytes(b'synthetic')
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.media.accept(old_media,download,download.media,(new_media,download.final,9))
                assert job in store.problems._processor_transaction_captures
                raise RuntimeError('rollback')
        assert job not in store.problems._processor_transaction_captures
        assert store.db.execute('SELECT 1 FROM media_files WHERE id=%s',(old_media,)).fetchone()
        assert store.problems.processor_current()['id']==old
    finally:store.db.close()


def test_media_accept_then_other_job_cancel_keeps_original_history(tmp_path,postgres_url):
    import json,uuid
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    old_media=str(uuid.uuid4());new_media=str(uuid.uuid4())
    try:
        content={'blocks':[{'type':'media','role':'image','media_id':old_media}],'extra':{'_reader':{'full':True,'reason':'','attachments':[]}}}
        with store.db:
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url,content) VALUES ('p',0,'rss','p','https://p.test',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES (%s,'p','image','x','image','ready','media/old')",(old_media,))
            media=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('download_media',%s,'running',1,'processor-config','media failure') RETURNING id",(old_media,)).fetchone()[0]
            other=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('filter','p','running',1,'processor-config','filter failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([media,other],None,1)
        old=store.problems.processor_current()['id'];download=store.media.begin(old_media,'attempt',True)
        download.final.parent.mkdir(parents=True,exist_ok=True);download.final.write_bytes(b'synthetic')
        with transaction(store.db):
            store.media.accept(old_media,download,download.media,(new_media,download.final,9))
            store.jobs.cancel(Job(other,'filter','p',1))
            store.jobs.finish(Job(media,'download_media',old_media,1))
        body=json.loads(store.db.execute('SELECT body FROM reader_problems WHERE id=%s',(old,)).fetchone()[0])
        assert any(obj.get('id')=='p' for obj in body['objects']),body
    finally:store.db.close()


def test_real_subtitle_accept_reconciles_other_job_before_finish(tmp_path,postgres_url):
    import json,uuid
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    old_media=str(uuid.uuid4());new_media=str(uuid.uuid4())
    try:
        content={'blocks':[{'type':'media','role':'subtitles','media_id':old_media}],'extra':{'youtube':{},'_reader':{'full':True,'reason':'','attachments':[]}}}
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','Feed','default','youtube','youtube','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url,content,complete) VALUES ('p',0,'youtube','p','https://p.test',%s,1)",(json.dumps(content),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('f','p',true,true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES (%s,'p','youtube:subtitles','x','caption','ready','media/old')",(old_media,))
            media=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('download_media',%s,'running',1,'processor-config','media failure') RETURNING id",(old_media,)).fetchone()[0]
            other=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('filter','p','pending',1,'processor-config','filter failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([media,other],None,1)
        old=store.problems.processor_current()['id'];download=store.media.begin(old_media,'attempt',True)
        download.final.parent.mkdir(parents=True,exist_ok=True);download.final.write_bytes(b'synthetic')
        with transaction(store.db):
            store.media.accept(old_media,download,download.media,(new_media,download.final,9))
            store.jobs.finish(Job(media,'download_media',old_media,1))
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(other,)).fetchone()[0]=='cancelled'
        body=json.loads(store.db.execute('SELECT body FROM reader_problems WHERE id=%s',(old,)).fetchone()[0])
        assert any(obj.get('id')=='p' for obj in body['objects']),body
    finally:store.db.close()
