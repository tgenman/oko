import json
from tests.support.storage import Store


def test_processing_recovery_hundred_moved_roots_has_bounded_sql(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id) SELECT 'f-'||n FROM generate_series(1,100) n")
            store.db.execute("INSERT INTO publications(id,viewed) SELECT 'r-'||n,0 FROM generate_series(1,100) n")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('target',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f-'||n,'r-'||n,true FROM generate_series(1,100) n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) SELECT 'r-'||n,'target' FROM generate_series(1,100) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','target','failed','failure','article-limit')")
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||n,'job:article-limit:old-'||n||':filter:target','background',jsonb_build_object('code','article-limit','section','object','title','t','consequence','c','next_step','n','state','current','operation','producer:filter','objects',jsonb_build_array(jsonb_build_object('kind','material','id','r-'||n)))::text,1 FROM generate_series(1,100) n""")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_processing_problems()
        finally:store.db.set_trace_callback(None)
        print('sql statements:',len(sql))
        assert len(sql)<100,len(sql)
    finally:store.db.close()


def test_processing_recovery_maps_old_occurrence_to_its_matching_root(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('root-x'),('root-y'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','root-x',true),('y','root-y',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-x','article'),('root-y','article')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','article','failed','failure','article-limit')")
            for identity,problem_id,root in [('old-z','z-for-x','root-x'),('old-a','a-for-y','root-y')]:
                body=dict(code='article-limit',section='object',title='t',consequence='c',next_step='n',state='current',operation='producer:filter',objects=[dict(kind='material',id=root)])
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(problem_id,'job:article-limit:'+identity+':filter:article',json.dumps(body)))
        store.problems.recover_processing_problems()
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:x:filter:article' AND finished IS NULL").fetchone()[0]=='z-for-x'
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:article-limit:y:filter:article' AND finished IS NULL").fetchone()[0]=='a-for-y'
    finally:store.db.close()
