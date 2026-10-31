import json
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.incomplete_group_draft import save_keys,sync_group_with_time,reservation,virtual_group


def test_pause_reservation_stable_through_deadline_tick_and_restart(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            content=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(content,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("UPDATE youtube_access SET blocked_until=200 WHERE id=1")
            save_keys(store.db,['p'])
        with transaction(store.db):sync_group_with_time(store,'incomplete:f:p',100)
        reserved=reservation(store,'incomplete:f:p')
        assert reserved is not None and virtual_group(store,'incomplete:f:p',100) is None
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone() is None
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
        clock[0]=201
        virtual=virtual_group(store,'incomplete:f:p',201)
        assert virtual and virtual['id']==reserved['id'] and virtual['created_at']==200
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                sync_group_with_time(store,'incomplete:f:p',201)
                raise RuntimeError('rollback')
        assert reservation(store,'incomplete:f:p')['id']==reserved['id']
        with transaction(store.db):sync_group_with_time(store,'incomplete:f:p',201)
        current=store.db.execute("SELECT id,created FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()
        assert current['id']==reserved['id'] and current['created']==200
    finally:store.db.close()


def test_multiple_groups_keep_distinct_reserved_ids_and_virtual_reads_are_pure(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('a','youtube','a',0,1,%s,0),('b','youtube','b',0,1,%s,0)",(raw,raw))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','a',true),('f','b',true)")
            store.db.execute("UPDATE youtube_access SET blocked_until=200 WHERE id=1")
            save_keys(store.db,['a','b'])
        with transaction(store.db):
            sync_group_with_time(store,'incomplete:f:a',100)
            sync_group_with_time(store,'incomplete:f:b',100)
        ids=[reservation(store,'incomplete:f:'+item)['id'] for item in ['a','b']]
        assert len(set(ids))==2
        before=[tuple(r) for r in store.db.execute("SELECT id,identity,body,created,finished,xmin::text FROM reader_problems ORDER BY id")]
        clock[0]=201
        projected=[virtual_group(store,'incomplete:f:'+item,201) for item in ['a','b']]
        assert [p['id'] for p in projected]==ids
        after=[tuple(r) for r in store.db.execute("SELECT id,identity,body,created,finished,xmin::text FROM reader_problems ORDER BY id")]
        assert before==after
        with transaction(store.db):
            sync_group_with_time(store,'incomplete:f:a',201)
            sync_group_with_time(store,'incomplete:f:b',201)
        actual=[store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',('incomplete:f:'+item,)).fetchone()[0] for item in ['a','b']]
        assert actual==ids
    finally:store.db.close()


def test_pause_extension_and_early_release_keep_reserved_identity(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute('UPDATE youtube_access SET blocked_until=200 WHERE id=1')
            save_keys(store.db,['p'])
        with transaction(store.db):sync_group_with_time(store,'incomplete:f:p',100)
        old=reservation(store,'incomplete:f:p')['id']
        with transaction(store.db):
            store.db.execute('UPDATE youtube_access SET blocked_until=300 WHERE id=1')
            sync_group_with_time(store,'incomplete:f:p',110)
        assert reservation(store,'incomplete:f:p')['id']==old and reservation(store,'incomplete:f:p')['deadline']==300
        assert virtual_group(store,'incomplete:f:p',250) is None
        with transaction(store.db):
            store.db.execute('UPDATE youtube_access SET blocked_until=0 WHERE id=1')
            sync_group_with_time(store,'incomplete:f:p',120)
        row=store.db.execute("SELECT id,created FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()
        assert row['id']==old and row['created']==120
    finally:store.db.close()


def test_real_youtube_block_and_release_drive_group_reservation(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            save_keys(store.db,['p'])
        store.youtube.blocked(100,900)
        saved=reservation(store,'incomplete:f:p')
        assert saved and virtual_group(store,'incomplete:f:p',100) is None
        clock[0]=110
        store.youtube.completed()
        current=store.db.execute("SELECT id,created FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()
        assert current and current['id']==saved['id'] and current['created']==110
    finally:store.db.close()


def test_unowned_legacy_reservation_body_is_not_adopted_or_mutated(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import reservation_body
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute('UPDATE youtube_access SET blocked_until=200 WHERE id=1')
            save_keys(store.db,['p'])
            foreign=json.dumps(reservation_body('incomplete:f:p',200))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('foreign','incomplete:reservation:foreign','background',%s,1,1,'irrelevant')",(foreign,))
        assert reservation(store,'incomplete:f:p') is None
        with transaction(store.db):sync_group_with_time(store,'incomplete:f:p',100)
        own=reservation(store,'incomplete:f:p')
        assert own and own['id']!='foreign'
        row=store.db.execute("SELECT identity,body,finished FROM reader_problems WHERE id='foreign'").fetchone()
        assert tuple(row)==('incomplete:reservation:foreign',foreign,1)
    finally:store.db.close()


def test_foreign_group_pointer_is_preserved_and_another_slot_is_used(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import reservation_pointer
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute('UPDATE youtube_access SET blocked_until=200 WHERE id=1')
            save_keys(store.db,['p'])
            store.db.execute("INSERT INTO state(key,value) VALUES (%s,'unrecognized')",(reservation_pointer('incomplete:f:p'),))
        with transaction(store.db):sync_group_with_time(store,'incomplete:f:p',100)
        assert store.db.execute('SELECT value FROM state WHERE key=%s',(reservation_pointer('incomplete:f:p'),)).fetchone()[0]=='unrecognized'
        own=reservation(store,'incomplete:f:p')
        assert own is not None
        assert not store.db.execute("SELECT incomplete_group_conflict FROM publications WHERE id='p'").fetchone()[0]
    finally:store.db.close()


def test_thousand_youtube_groups_reserve_in_batches(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) SELECT 'p-'||n,'youtube','p-'||n,0,1,%s,0,'incomplete:f:p-'||n,'f',true FROM generate_series(1,1000)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,1000)n")
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.youtube.blocked(100,900)
        finally:store.db.set_trace_callback(None)
        count=store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:incomplete-reservation'").fetchone()[0]
        assert count==1000
        print('1000 reservations SQL statements:',len(queries))
        assert len(queries)<500
        before={row['group_key']:row['id'] for row in store.db.execute("SELECT id,body::jsonb->>'incomplete_group_identity' AS group_key FROM reader_problems WHERE body::jsonb->>'operation'='producer:incomplete-reservation'")}
        store.wall_clock=lambda:110
        extension=[];store.db.set_trace_callback(extension.append)
        try:store.youtube.blocked(110,900)
        finally:store.db.set_trace_callback(None)
        after={row['group_key']:row['id'] for row in store.db.execute("SELECT id,body::jsonb->>'incomplete_group_identity' AS group_key FROM reader_problems WHERE body::jsonb->>'operation'='producer:incomplete-reservation'")}
        assert before==after
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:incomplete-reservation' AND incomplete_due=1910").fetchone()[0]==1000
        print('1000 reservations extension SQL statements:',len(extension))
        assert len(extension)<500
        store.wall_clock=lambda:120
        release=[];store.db.set_trace_callback(release.append)
        try:store.youtube.completed()
        finally:store.db.set_trace_callback(None)
        live={row['identity']:row['id'] for row in store.db.execute("SELECT identity,id FROM reader_problems WHERE body::jsonb->>'operation'='producer:incomplete' AND finished IS NULL")}
        assert live==before
        print('1000 reservations release SQL statements:',len(release))
        assert len(release)<500
    finally:store.db.close()


def test_thousand_due_reservations_virtual_projection_is_batched_and_read_only(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import virtual_due_groups
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) SELECT 'p-'||n,'youtube','p-'||n,0,1,%s,0,'incomplete:f:p-'||n,'f',true FROM generate_series(1,1000)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,1000)n")
        store.youtube.blocked(100,900)
        before=store.db.execute("SELECT md5(string_agg(id||':'||xmin::text,'|' ORDER BY id)) FROM reader_problems").fetchone()[0]
        queries=[];store.db.set_trace_callback(queries.append)
        try:result=virtual_due_groups(store,1001)
        finally:store.db.set_trace_callback(None)
        assert len(result)==1000 and len({r['id'] for r in result})==1000
        assert store.db.execute("SELECT md5(string_agg(id||':'||xmin::text,'|' ORDER BY id)) FROM reader_problems").fetchone()[0]==before
        assert not [q for q in queries if q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE'))]
        print('1000 virtual groups SQL statements:',len(queries))
        assert len(queries)<150
    finally:store.db.close()


def test_ten_thousand_reservations_are_excluded_from_gc_and_expiry_probe(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:200000)
    try:
        with store.db:
            body=json.dumps({'code':'material-incomplete','operation':'producer:incomplete-reservation','objects':[]})
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome,incomplete_due) SELECT 'r-'||n,'incomplete:reservation:r-'||n,'background',%s,1,1,'irrelevant',300000 FROM generate_series(1,10000)n",(body,))
            normal=json.dumps({'code':'foreign','operation':'producer:status','objects':[]})
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('ordinary','other','background',%s,1,199999,'resolved')",(normal,))
            store.db._db.execute('ANALYZE reader_problems')
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.read_versions.time_boundaries()
        finally:store.db.set_trace_callback(None)
        q=next(q for q in queries if 'jsonb_build_array(next_at,blocked_until,strikes)' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+q,(store.clock(),200000,200000,200000-86400,None,200000,200000-86400)).fetchone()[0][0]['Plan']
        print('expiry probe rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<1000
        store.read_versions.advance(wall=200000)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE incomplete_due IS NOT NULL").fetchone()[0]==10000
    finally:store.db.close()


def test_long_youtube_pause_reservation_survives_problem_gc(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            save_keys(store.db,['p'])
        store.youtube.blocked(100,200000)
        old=reservation(store,'incomplete:f:p')
        assert old
        clock[0]=100000
        store.read_versions.advance(wall=clock[0])
        assert reservation(store,'incomplete:f:p')==old
    finally:store.db.close()


def test_all_reservation_pointer_slots_occupied_are_reported_without_overwrite(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import reservation_pointers
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute('UPDATE youtube_access SET blocked_until=200 WHERE id=1')
            save_keys(store.db,['p'])
            pointers=reservation_pointers('incomplete:f:p')
            for pointer in pointers:store.db.execute("INSERT INTO state(key,value) VALUES (%s,'foreign')",(pointer,))
        with transaction(store.db):sync_group_with_time(store,'incomplete:f:p',100)
        assert [row[0] for row in store.db.execute('SELECT value FROM state WHERE key=ANY(%s) ORDER BY key',(pointers,))]==['foreign']*3
        assert store.db.execute("SELECT incomplete_group_conflict FROM publications WHERE id='p'").fetchone()[0]
        with pytest.raises(ValueError,match='Unrecognized incomplete reservation pointer'):store.problems.snapshot(100)
    finally:store.db.close()


def test_tick_page_ignores_ten_thousand_unrelated_state_rows(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) SELECT 'p-'||n,'youtube','p-'||n,0,1,%s,0,'incomplete:f:p-'||n,'f',true FROM generate_series(1,1000)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO state(key,value) SELECT 'foreign-'||n,'foreign' FROM generate_series(1,10000)n")
        store.youtube.blocked(100,900)
        with store.db:
            store.db._db.execute('ANALYZE reader_problems')
            store.db._db.execute('ANALYZE state')
        clock[0]=1001
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.read_versions.advance(wall=clock[0])
        finally:store.db.set_trace_callback(None)
        q=next(q for q in queries if q.startswith('SELECT id,identity,body,finished,incomplete_due,incomplete_future,incomplete_closes FROM reader_problems'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+q,(1001,)).fetchone()[0][0]['Plan']
        print('tick page rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<200
    finally:store.db.close()


def test_invalid_due_rows_do_not_starve_legitimate_reservation(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            save_keys(store.db,['p'])
        store.youtube.blocked(100,900)
        saved=reservation(store,'incomplete:f:p')
        fake=json.dumps({'code':'foreign','operation':'producer:incomplete-reservation','objects':[]})
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome,incomplete_due) SELECT 'a-fake-'||lpad(n::text,3,'0'),'incomplete:reservation:a-fake-'||n,'background',%s,1,1,'irrelevant',1000 FROM generate_series(1,100)n",(fake,))
        clock[0]=1001
        store.read_versions.advance(wall=clock[0])
        assert reservation(store,'incomplete:f:p')['id']==saved['id']
        store.read_versions.advance(wall=clock[0])
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()
        assert row and row['id']==saved['id']
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'a-fake-%%' AND body=%s",(fake,)).fetchone()[0]==100
    finally:store.db.close()


def test_tick_page_failure_rolls_back_activation_and_cursor(tmp_path,postgres_url,monkeypatch):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) SELECT 'p-'||n,'youtube','p-'||n,0,1,%s,0,'incomplete:f:p-'||n,'f',true FROM generate_series(1,101)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,101)n")
        store.youtube.blocked(100,900)
        before=[tuple(row) for row in store.db.execute("SELECT id,identity,body,finished,incomplete_due FROM reader_problems WHERE incomplete_due IS NOT NULL ORDER BY id")]
        original=store.db.execute
        def fail(query,parameters=()):
            if query.startswith('SELECT id FROM reader_problems WHERE finished <='):
                assert store.read_versions._incomplete_due_cursor is not None
                raise RuntimeError('rollback')
            return original(query,parameters)
        monkeypatch.setattr(store.db,'execute',fail)
        clock[0]=1001
        with pytest.raises(RuntimeError,match='rollback'):store.read_versions.advance(wall=clock[0])
        assert store.read_versions._incomplete_due_cursor is None
        assert [tuple(row) for row in store.db.execute("SELECT id,identity,body,finished,incomplete_due FROM reader_problems WHERE incomplete_due IS NOT NULL ORDER BY id")]==before
        monkeypatch.setattr(store.db,'execute',original)
        store.read_versions.advance(wall=clock[0])
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:incomplete' AND finished IS NULL").fetchone()[0]==100
    finally:store.db.close()


def test_restart_after_deadline_before_tick_keeps_virtual_id(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import virtual_due_groups
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            save_keys(store.db,['p'])
        store.youtube.blocked(100,900)
        old=reservation(store,'incomplete:f:p')['id']
        clock[0]=1001
        assert virtual_due_groups(store,clock[0])[0]['id']==old
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0],defer_recovery=True)
        assert virtual_due_groups(store,clock[0])[0]['id']==old
        store.read_versions.advance(wall=clock[0])
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()[0]==old
    finally:store.db.close()


def test_status_does_not_scan_ten_thousand_future_reservations(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            body=json.dumps({'code':'material-incomplete','operation':'producer:incomplete-reservation','objects':[]})
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome,incomplete_due) SELECT 'r-'||n,'incomplete:reservation:r-'||n,'background',%s,1,-62135596800,'irrelevant',1000 FROM generate_series(1,10000)n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.problems.snapshot(100)
        finally:store.db.set_trace_callback(None)
        q=next(q for q in queries if q.startswith('SELECT * FROM reader_problems WHERE finished IS NULL OR finished >'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+q,(100-86400,)).fetchone()[0][0]['Plan']
        print('future reservations status rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_after_deadline_before_tick_source_delete_keeps_seen_occurrence_history(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import virtual_due_groups
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            save_keys(store.db,['p'])
        store.youtube.blocked(100,900)
        clock[0]=1001
        seen=virtual_due_groups(store,clock[0])
        assert len(seen)==1
        with transaction(store.db):store.mark_source_removed('p')
        history=store.db.execute('SELECT body,finished,outcome FROM reader_problems WHERE id=%s',(seen[0]['id'],)).fetchone()
        assert history and history['finished'] is not None and history['outcome']=='irrelevant'
        assert json.loads(history['body'])['objects']==[{'kind':'material','id':'p'}]
    finally:store.db.close()


def test_view_after_deadline_scrubs_virtual_occurrence_material(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import virtual_due_groups
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'blocks':[],'extra':{'_reader':{'full':False,'reason':'partial'}}})
            store.db.execute("INSERT INTO publications(id,source_id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube:p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            save_keys(store.db,['p'])
        store.youtube.blocked(100,900);clock[0]=1001
        seen=virtual_due_groups(store,clock[0]);assert len(seen)==1
        assert store.viewed_set(['p'])
        row=store.db.execute('SELECT body,finished,outcome FROM reader_problems WHERE id=%s',(seen[0]['id'],)).fetchone()
        assert row and row['finished'] is not None and row['outcome']=='irrelevant'
        assert not any(obj.get('id')=='p' for obj in json.loads(row['body'])['objects'])
    finally:store.db.close()


def test_due_virtual_sticky_survives_bulk_owner_rekey_before_tick(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import virtual_due_groups
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]'),('g','g','G','default','rss','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) SELECT 'p-'||n,'youtube','p-'||n,0,1,%s,0 FROM generate_series(1,101)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT feed,'p-'||n,feed='f' FROM generate_series(1,101)n CROSS JOIN (VALUES ('f'),('g')) v(feed)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','p-'||n,'done',NULL,NULL FROM generate_series(1,101)n")
            save_keys(store.db,['p-'+str(n) for n in range(1,102)])
        store.youtube.blocked(100,900);clock[0]=1001
        assert len(virtual_due_groups(store,clock[0]))==101
        with transaction(store.db):
            store.db.execute("UPDATE jobs SET status='pending' WHERE kind='fetch'")
            store.remove_feed('f')
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE identity LIKE 'incomplete:g:%%' AND finished IS NULL AND body::jsonb->>'state'='retrying'").fetchone()[0]==101
    finally:store.db.close()


def test_new_pause_after_deadline_archives_seen_occurrence_and_reserves_new(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import virtual_due_groups
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            save_keys(store.db,['p'])
        store.youtube.blocked(100,900);clock[0]=1001
        old=virtual_due_groups(store,clock[0])[0]['id']
        store.youtube.blocked(clock[0],900)
        historic=store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(old,)).fetchone()
        assert historic and historic['finished'] is not None
        new=reservation(store,'incomplete:f:p')
        assert new and new['id']!=old and new['deadline']>clock[0]
        assert virtual_due_groups(store,clock[0])==[]
    finally:store.db.close()


def test_time_sensitive_fetch_suppressor_opens_and_then_closes_group(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','bad',NULL)")
            save_keys(store.db,['p']);store.incomplete_draft.mark(['p'])
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()
        store.youtube.start(clock[0])
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()
        assert row is not None
        clock[0]=111
        result=store.problems.snapshot(clock[0])
        assert not any(p['id']==row['id'] for p in result['current'])
        assert any(p['id']==row['id'] for p in result['recent'])
    finally:store.db.close()


@pytest.mark.parametrize('operation',['source_removed','viewed'])
def test_late_event_after_virtual_group_update_preserves_seen_history_and_privacy(tmp_path,postgres_url,operation):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'blocks':[],'extra':{'_reader':{'full':False,'captions':'missing'}}})
            store.db.execute("INSERT INTO publications(id,source_id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('a','youtube:a','youtube','a',0,1,%s,0),('b','youtube:b','youtube','b',0,1,%s,0)",(raw,raw))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','a',true),('f','b',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','a','failed','bad',NULL)")
            save_keys(store.db,['a','b']);store.incomplete_draft.mark(['a','b'])
        store.youtube.start(100);clock[0]=111
        due=store.problems.snapshot(clock[0])
        seen=next(p for p in due['current'] if p['code']=='material-incomplete')
        assert [o['id'] for o in seen['objects']]==['b']
        if operation=='source_removed':
            with transaction(store.db):store.mark_source_removed('b')
        else:assert store.viewed_set(['b'])
        row=store.db.execute('SELECT body,finished,outcome FROM reader_problems WHERE id=%s',(seen['id'],)).fetchone()
        assert row and row['finished'] is not None
        objects=json.loads(row['body'])['objects']
        if operation=='source_removed':assert objects==[{'kind':'material','id':'b'}] and row['outcome']=='irrelevant'
        else:assert not any(o['id']=='b' for o in objects) and row['outcome']=='irrelevant'
    finally:store.db.close()


def test_thousand_timed_closures_are_scheduled_in_batches_without_rss_scan(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import refresh_youtube_flags
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','Y','default','youtube','rss','connected','[]'),('rss','rss','R','default','rss','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) SELECT 'y-'||n,'youtube','y-'||n,0,1,%s,0,'incomplete:yt:y-'||n,'yt',true FROM generate_series(1,1000)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'yt','y-'||n,true FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','y-'||n,'failed','bad',NULL FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','rss-'||n,'failed','bad',NULL FROM generate_series(1,10000)n")
            refresh_youtube_flags(store,['y-'+str(n) for n in range(1,1001)])
            store.db._db.execute('ANALYZE jobs')
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.youtube.start(100)
        finally:store.db.set_trace_callback(None)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE finished IS NULL AND incomplete_closes AND incomplete_due=110").fetchone()[0]==1000
        from collections import Counter
        print('1000 scheduled closures SQL statements',len(queries),Counter(q[:75] for q in queries).most_common(8))
        assert len(queries)<500
        q=next(q for q in queries if q.startswith("SELECT kind,item FROM jobs WHERE kind='fetch'"))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+q).fetchone()[0][0]['Plan']
        print('sensitive fetch rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<2000
    finally:store.db.close()


def test_invalid_due_schedule_is_visible_as_safe_problem_without_mutation(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','bad',NULL)")
            save_keys(store.db,['p']);store.incomplete_draft.mark(['p'])
        store.youtube.start(100)
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()
        assert row
        with store.db:store.db.execute("UPDATE reader_problems SET incomplete_future='bad' WHERE id=%s",(row['id'],))
        before=tuple(store.db.execute('SELECT body,incomplete_due,incomplete_future FROM reader_problems WHERE id=%s',(row['id'],)).fetchone())
        result=store.problems.snapshot(111)
        safe=next(p for p in result['current'] if p['id']==row['id'])
        assert safe['objects']==[] and safe['title']=='Сведения о неполном материале недоступны'
        assert tuple(store.db.execute('SELECT body,incomplete_due,incomplete_future FROM reader_problems WHERE id=%s',(row['id'],)).fetchone())==before
    finally:store.db.close()


def test_unrecognized_due_reservation_is_visible_without_losing_raw_data(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        fake=json.dumps({'code':'material-incomplete','operation':'producer:incomplete-reservation','objects':[],'unknown':'kept'})
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome,incomplete_due) VALUES ('foreign','incomplete:reservation:foreign','background',%s,1,-62135596800,'irrelevant',200)",(fake,))
        clock[0]=201
        result=store.problems.snapshot(clock[0])
        safe=next(p for p in result['current'] if p['id']=='foreign')
        assert safe['objects']==[] and safe['title']=='Сведения о неполном материале недоступны'
        assert store.db.execute("SELECT body FROM reader_problems WHERE id='foreign'").fetchone()[0]==fake
    finally:store.db.close()


def test_future_snapshot_updates_before_deadline_without_changing_reserved_id(tmp_path,postgres_url):
    from oko.storage.incomplete_index import save_class
    from oko.storage.incomplete_group_draft import virtual_due_groups
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            initial={'extra':{'_reader':{'full':False,'reason':'old'}}}
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(json.dumps(initial),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            save_keys(store.db,['p'])
        store.youtube.blocked(100,900)
        old=reservation(store,'incomplete:f:p')['id']
        changed={'extra':{'_reader':{'full':False,'reason':'new'}}}
        clock[0]=200
        with transaction(store.db):
            store.db.execute("UPDATE publications SET content=%s WHERE id='p'",(json.dumps(changed),))
            save_class(store.db,'p',changed)
        assert reservation(store,'incomplete:f:p')['id']==old
        clock[0]=1001
        result=virtual_due_groups(store,clock[0])
        assert len(result)==1 and result[0]['id']==old and result[0]['reason']=='new'
    finally:store.db.close()


def test_feed_removal_after_deadline_does_not_restore_forgotten_material(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import virtual_due_groups
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'blocks':[],'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,source_id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube:p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            save_keys(store.db,['p'])
        store.youtube.blocked(100,900);clock[0]=1001
        seen=virtual_due_groups(store,clock[0]);assert len(seen)==1
        store.remove_feed('f')
        row=store.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(seen[0]['id'],)).fetchone()
        assert row and row['finished'] is not None
        assert not any(o.get('id')=='p' for o in json.loads(row['body'])['objects'])
    finally:store.db.close()


def test_interrupted_recovery_applies_seen_timed_close_before_reconciliation(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','bad',NULL)")
            save_keys(store.db,['p']);store.incomplete_draft.mark(['p'])
        store.youtube.start(100)
        old=store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()['id']
        clock[0]=111
        assert any(p['id']==old for p in store.problems.snapshot(clock[0])['recent'])
        with store.db:store.db.execute("DELETE FROM state WHERE key='experimental-incomplete-problems-v1'")
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
        row=store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(old,)).fetchone()
        assert row['finished'] is not None
        assert any(p['id']==old for p in store.problems.snapshot(clock[0])['recent'])
    finally:store.db.close()
