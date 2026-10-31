import json
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction


def test_ten_thousand_groups_commit_and_rollback_with_transaction_local_workset(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) SELECT 'p-'||n,'rss','p-'||n,0,1,%s,0,'incomplete:f:p-'||n,'f',true FROM generate_series(1,10000)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,10000)n")
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                for start in range(0,10000,100):store.incomplete_draft.mark([f'p-{n}' for n in range(start+1,start+101)])
                assert store.db.execute('SELECT count(*) FROM pg_temp.oko_incomplete_work').fetchone()[0]==10000
                store.db.execute('INSERT INTO pg_temp.oko_incomplete_groups(key,is_old) SELECT old_key,true FROM pg_temp.oko_incomplete_work WHERE old_key IS NOT NULL')
                plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT key FROM pg_temp.oko_incomplete_groups WHERE key>%s COLLATE "C" ORDER BY key LIMIT 100',('incomplete:f:p-9000',)).fetchone()[0][0]['Plan']
                from tests.support.postgres_work import scan_rows
                assert scan_rows(plan)<=100
                raise RuntimeError('rollback')
        assert store.db.execute("SELECT to_regclass('pg_temp.oko_incomplete_work')").fetchone()[0] is None
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'code'='material-incomplete'").fetchone()[0]==0
        with transaction(store.db):
            for start in range(0,10000,100):store.incomplete_draft.mark([f'p-{n}' for n in range(start+1,start+101)])
            with store.db:
                store.incomplete_draft.mark(['p-1'])
            assert store.db.execute('SELECT count(*) FROM pg_temp.oko_incomplete_work').fetchone()[0]==10000
        assert store.db.execute("SELECT to_regclass('pg_temp.oko_incomplete_work')").fetchone()[0] is None
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='material-incomplete'").fetchone()[0]==10000
    finally:store.db.close()


def test_nested_savepoint_rollback_keeps_only_outer_work(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) VALUES ('a','rss','a',0,1,%s,0,'incomplete:f:a','f',true),('b','rss','b',0,1,%s,0,'incomplete:f:b','f',true)",(raw,raw))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','a',true),('f','b',true)")
        with transaction(store.db):
            store.incomplete_draft.mark(['a'])
            with pytest.raises(RuntimeError):
                with store.db._db.transaction():
                    store.incomplete_draft.mark(['b'])
                    raise RuntimeError('nested rollback')
            assert [r['item'] for r in store.db.execute('SELECT item FROM pg_temp.oko_incomplete_work')]==['a']
        identities=[r['identity'] for r in store.db.execute("SELECT identity FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='material-incomplete'")]
        assert identities==['incomplete:f:a']
        with transaction(store.db):
            with pytest.raises(RuntimeError):
                with store.db._db.transaction():
                    store.incomplete_draft.mark(['b'])
                    raise RuntimeError('creation rolled back')
            assert store.db.execute("SELECT to_regclass('pg_temp.oko_incomplete_work')").fetchone()[0] is None
            store.incomplete_draft.mark(['b'])
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='incomplete:f:b' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_one_large_workset_group_streams_at_most_one_hundred_rows(tmp_path,postgres_url,monkeypatch):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        lengths=[]
        original=store.db.incomplete_row_pages
        def tracked(sql,parameters=()):
            for rows in original(sql,parameters):
                if 'FROM pg_temp.oko_incomplete_work WHERE old_key=ANY' in sql:lengths.append(len(rows))
                yield rows
        monkeypatch.setattr(store.db,'incomplete_row_pages',tracked)
        with transaction(store.db):
            store.db.ensure_incomplete_workset()
            store.db.execute("INSERT INTO pg_temp.oko_incomplete_work(item,old_key) SELECT 'p-'||n,'same-key' FROM generate_series(1,10000)n")
        assert sum(lengths)==10000 and max(lengths)<=100
    finally:store.db.close()


def test_large_groups_are_not_loaded_in_same_batch(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.db.ensure_incomplete_workset()
                store.db.execute("INSERT INTO pg_temp.oko_incomplete_work(item,old_key) SELECT 'p-'||g||'-'||n,'group-'||g FROM generate_series(1,3) g CROSS JOIN generate_series(1,1500) n")
                store.db.execute('INSERT INTO pg_temp.oko_incomplete_groups(key,is_old) SELECT DISTINCT old_key,true FROM pg_temp.oko_incomplete_work')
                store.incomplete_draft.update_group_weights()
                assert list(store.incomplete_draft.group_pages())==[['group-1'],['group-2'],['group-3']]
                raise RuntimeError('rollback synthetic work')
    finally:store.db.close()
