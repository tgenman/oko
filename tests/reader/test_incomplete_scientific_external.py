from tests.support.storage import Store


def test_numeric_scientific_zero_remains_visible(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        raw='{"blocks":[],"extra":{"_reader":{"title":"T","full":0e0,"reason":"partial"}}}'
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed,complete,platform,external_id,content) VALUES ('p',0,1,'rss','p',%s)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            try:
                from oko.storage.incomplete_index import save_class
            except ModuleNotFoundError:
                pass
            else:
                save_class(store.db,'p',raw)
        assert any(p['code']=='material-incomplete' for p in store.problems.snapshot(1)['current'])
    finally:store.db.close()

def test_scientific_zero_http(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        raw='{"blocks":[],"extra":{"_reader":{"title":"T","full":0e0,"reason":"partial"}}}'
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed,complete,platform,external_id,content) VALUES ('p',0,1,'rss','p',%s)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            try:from oko.storage.incomplete_index import save_class
            except ModuleNotFoundError:pass
            else:save_class(store.db,'p',raw)
    finally:store.db.close()
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True)
    with TestClient(app,headers=HEADERS,raise_server_exceptions=False) as client:
        result=client.get('/api/v3/status/details')
        assert result.status_code==200,result.text


def test_numeric_representation_matches_postgres_jsonb_integer(tmp_path,postgres_url):
    import json
    from oko.storage.incomplete_index import content_class
    values=['0','-0','0e0','-0e0','0e1','0e-1','0.0','0.0e1','0.00e2','0.00e1','1e0','1e1','1.0e1','1.00e1','1e-1','1.0','1.00','-1e2','2147483647e0','2147483648e0','-2147483648e0','-2147483649e0','0e1000000000','1e1000000000','1e-1000000000']
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        for number in values:
            raw='{"extra":{"_reader":{"full":'+number+'}}}'
            valid=store.db.execute("SELECT pg_input_is_valid(%s,'jsonb') AS valid",(raw,)).fetchone()['valid']
            if valid:
                value=store.db.execute("SELECT %s::jsonb#>>'{extra,_reader,full}' AS value",(raw,)).fetchone()['value']
                integer=store.db.execute("SELECT pg_input_is_valid(%s,'integer') AS valid",(value,)).fetchone()['valid']
                expected=0 if integer and int(value)==0 else 1 if integer else 2
                assert content_class(raw)==expected,(number,value,expected,content_class(raw))
            else:
                assert content_class(raw)==2,(number,content_class(raw))
        for value in [0.0,1.0,1e10,0.1,-0.0,42,False,True,None]:
            obj={'extra':{'_reader':{'full':value}}}
            assert content_class(obj)==content_class(json.dumps(obj)),value
    finally:store.db.close()
