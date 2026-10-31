from tests.support.storage import Store
from tests.support.postgres_work import scan_rows


def test_scoped_generic_job_query_on_unrelated_errors(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','foreign-'||n,'failed','error','source-network' FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','selected','failed','error','source-network')")
            store.db._db.execute('ANALYZE jobs')
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.problems.incomplete_generic_groups(1,['selected'])
        finally:store.db.set_trace_callback(None)
        scoped=next(q for q in queries if q.startswith('SELECT jobs . *'))
        rows=store.db._db.execute(scoped,(['selected'],['selected'])).fetchall()
        assert len(rows)==1 and rows[0]['item']=='selected'
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+scoped,(['selected'],['selected'])).fetchone()[0][0]['Plan']
        print('scoped rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_scoped_generic_jobs_match_root_and_child_media_projection(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('root','rss','root',0),('child','rss','child',0),('other','rss','other',0)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','child')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m-child','child','image:1','x','file','unavailable'),('m-other','other','image:1','x','file','unavailable')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','child','failed','bad','source-network'),('filter','root','failed','bad','source-network'),('download_media','m-child','failed','bad','source-network'),('fetch','other','failed','bad','source-network'),('download_media','m-other','failed','bad','source-network')")
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.problems.candidates(1)
        finally:store.db.set_trace_callback(None)
        q=next(q for q in queries if q.startswith('SELECT jobs . *'))
        all_rows=store.db._db.execute(q).fetchall()
        targets=['root','child','m-child']
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.problems.incomplete_generic_groups(1,targets)
        finally:store.db.set_trace_callback(None)
        scoped=next(q for q in queries if q.startswith('SELECT jobs . *'))
        scope_rows=store.db._db.execute(scoped,(targets,targets)).fetchall()
        def selected(rows):return sorted((r['kind'],r['item'],r['affected_item']) for r in rows if r['affected_item']=='root')
        assert selected(scope_rows)==selected(all_rows)
        assert len(selected(scope_rows))==3
    finally:store.db.close()


def test_scoped_suppression_matches_generic_output_in_linked_graph(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import generic_job_targets,suppressed_materials
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('root','rss','root',0),('child','rss','child',0),('other','rss','other',0)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','child')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m-child','child','image:1','x','file','unavailable'),('m-other','other','image:1','x','file','unavailable')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','child','failed','bad','source-network'),('filter','root','failed','bad','source-network'),('download_media','m-child','failed','bad','source-network'),('fetch','other','failed','bad','source-network')")
        assert set(generic_job_targets(store.db,['root']))=={'root','child','m-child'}
        groups,_,_=store.problems.incomplete_generic_groups(1)
        expected={obj['id'] for group in groups.values() for obj in group['objects'] if obj['kind']=='material'} & {'root'}
        assert expected=={'root'}
        assert suppressed_materials(store,['root'],1)==expected
    finally:store.db.close()


def test_scoped_profile_lookup_avoids_unrelated_profile_history(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','selected','f-selected','failed','bad','profile_unavailable')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'p-'||n,'profile:p-'||n,'background',json_build_object('objects',json_build_array(json_build_object('kind','feed','id','foreign-'||n)))::text,1 FROM generate_series(1,10000)n")
            selected=json.dumps({'objects':[{'kind':'feed','id':'f-selected'}]})
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('selected','profile:selected','background',%s,1)",(selected,))
            store.db._db.execute('ANALYZE reader_problems')
        queries=[];store.db.set_trace_callback(queries.append)
        try:groups,_,_=store.problems.incomplete_generic_groups(1,['selected'])
        finally:store.db.set_trace_callback(None)
        assert not groups
        query=next(q for q in queries if q.startswith("SELECT body FROM reader_problems WHERE identity LIKE 'profile:"))
        needles=[json.dumps([{'kind':'feed','id':'f-selected'}])]
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(needles,)).fetchone()[0][0]['Plan']
        print('profile rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_sticky_probe_ignores_ten_thousand_foreign_same_material(tmp_path,postgres_url):
    import json
    from oko.storage.incomplete_group_draft import known_incomplete_materials
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            foreign=json.dumps({'code':'foreign','operation':'producer:status','objects':[{'kind':'material','id':'p'}]})
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'foreign-'||n,'foreign-'||n,'background',%s,1 FROM generate_series(1,10000)n",(foreign,))
            prior=json.dumps({'code':'material-incomplete','operation':'producer:status','objects':[{'kind':'material','id':'p'}]})
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','incomplete:f:p','background',%s,1)",(prior,))
            store.db._db.execute('ANALYZE reader_problems')
        queries=[];store.db.set_trace_callback(queries.append)
        try:assert known_incomplete_materials(store,['p'])=={'p'}
        finally:store.db.set_trace_callback(None)
        q=next(q for q in queries if q.startswith('SELECT target.id FROM unnest'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+q,(['p'],)).fetchone()[0][0]['Plan']
        print('sticky probe rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_profile_event_does_not_scan_ten_thousand_full_same_feed(tmp_path,postgres_url):
    import json
    from tests.support.postgres_work import scan_rows
    from oko.storage.transaction import begin_write
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            full=json.dumps({'extra':{'_reader':{'full':True}}});partial=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) SELECT 'p-'||n,'rss','p-'||n,0,1,%s,1 FROM generate_series(1,10000)n",(full,))
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) VALUES ('partial','rss','partial',0,1,%s,0,'incomplete:f:partial','f',true)",(partial,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','partial',true)")
            store.db._db.execute('ANALYZE publications')
        queries=[];store.db.set_trace_callback(queries.append)
        try:
            with store.db:
                begin_write(store.db)
                store.incomplete_draft.mark_profile_feeds({'f'})
        finally:store.db.set_trace_callback(None)
        q=next(q for q in queries if q.startswith('SELECT id FROM publications WHERE incomplete_group_owner'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+q,(['f'],)).fetchone()[0][0]['Plan']
        print('profile feed group rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<100
    finally:store.db.close()


def test_youtube_time_event_ignores_ten_thousand_rss_fetch_failures(tmp_path,postgres_url):
    import json
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('rss','rss','R','default','rss','rss','connected','[]'),('yt','yt','Y','default','youtube','rss','connected','[]')")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) SELECT 'rss-'||n,'rss','rss-'||n,0,1,%s,0 FROM generate_series(1,10000)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'rss','rss-'||n,true FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','rss-'||n,'failed','bad',NULL FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) VALUES ('target','youtube','target',0,1,%s,0,'incomplete:yt:target','yt',true)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','target',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','bad',NULL)")
            store.db._db.execute('ANALYZE jobs');store.db._db.execute('ANALYZE publications');store.db._db.execute('ANALYZE feed_publications')
        from oko.storage.incomplete_group_draft import refresh_youtube_flags
        with store.db:refresh_youtube_flags(store,['target'])
        queries=[];store.db.set_trace_callback(queries.append)
        try:store.youtube.start(100)
        finally:store.db.set_trace_callback(None)
        q=next(q for q in queries if q.startswith("SELECT kind,item FROM jobs WHERE kind='fetch'"))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+q).fetchone()[0][0]['Plan']
        print('time event fetch probe rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<1000
    finally:store.db.close()
