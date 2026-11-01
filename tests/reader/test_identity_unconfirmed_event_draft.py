from oko.storage.transaction import transaction
from oko.storage.job_types import Job
from tests.support.storage import Store


def test_profile_suppression_rolls_back_with_checked_state(tmp_path,postgres_url):
    import pytest
    from oko.services.connections import ConnectionService
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            store.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job,'fetch','p',1),100,'unconfirmed',code='identity-unconfirmed')
        before=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:identity-unconfirmed:f:fetch:p' AND finished IS NULL").fetchone()
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                assert not ConnectionService(lambda:store).checked('personal',1,202,2)
                assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:identity-unconfirmed:f:fetch:p' AND finished IS NULL").fetchone()
                raise RuntimeError('rollback')
        after=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:identity-unconfirmed:f:fetch:p' AND finished IS NULL").fetchone()
        assert tuple(after)==tuple(before)
        assert store.db.execute("SELECT state FROM telegram_profiles WHERE role='personal'").fetchone()[0]=='ready'
    finally:store.db.close()


def test_profile_switch_http_etag_tracks_suppression_and_reopen(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from oko.services.connections import ConnectionService
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store();app.state.host.execution.reader_advance=None
            with transaction(store.db):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected',ARRAY[]::text[])")
                store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
                store.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
                store.db.execute("INSERT INTO publications(id) VALUES ('p')")
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
                job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
                store.jobs.retry(Job(job,'fetch','p',1),100,'unconfirmed',code='identity-unconfirmed')
        client.portal.call(setup)
        first=client.get('/api/v3/status/details');assert first.status_code==200
        def checked(user):return ConnectionService(lambda:app.state.host.require_store()).checked('personal',1,user,2)
        assert not client.portal.call(checked,202)
        second=client.get('/api/v3/status/details',headers={'If-None-Match':first.headers['etag']});assert second.status_code==200
        assert second.headers['etag']!=first.headers['etag']
        assert client.portal.call(checked,101)
        third=client.get('/api/v3/status/details',headers={'If-None-Match':second.headers['etag']});assert third.status_code==200
        assert third.headers['etag']!=second.headers['etag']
