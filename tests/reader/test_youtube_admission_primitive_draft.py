import json
import pytest
from oko.storage.youtube_admission import sync,current
from oko.storage.transaction import transaction
from tests.support.storage import Store


def test_event_projection_preserves_id_and_rolls_back(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute('UPDATE youtube_access SET blocked_until=200 WHERE id=1')
            old=sync(store,['a','b','a'],200,100)
        with pytest.raises(RuntimeError,match='abort'):
            with transaction(store.db):
                sync(store,['b'],300,101)
                raise RuntimeError('abort')
        row=current(store)
        assert row['id']==old and row['youtube_admission_due']==200
        assert json.loads(row['body'])['objects']==[dict(kind='material',id='a'),dict(kind='material',id='b')]
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
        with transaction(store.db):new=sync(store,['b'],300,101)
        assert new==old and current(store)['youtube_admission_due']==300
    finally:store.db.close()


def test_collision_preserves_foreign_bytes_and_uses_isolated_id(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        raw=json.dumps(dict(code='foreign',objects=[]))
        with transaction(store.db):
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','youtube:admission','background',%s,1)",(raw,))
            old=sync(store,[],200,100)
        assert store.db.execute("SELECT body FROM reader_problems WHERE id='foreign'").fetchone()[0]==raw
        with transaction(store.db):assert sync(store,['a'],300,101)==old
        assert current(store)['id']==old
    finally:store.db.close()


def test_expiry_and_display_deadlines_remain_distinct(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):sync(store,[],1000,100,next_at=1005)
        row=current(store)
        assert row['youtube_admission_due']==1000 and json.loads(row['body'])['next_at']==1005
    finally:store.db.close()


def test_event_after_expired_deadline_preserves_seen_old_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):old=sync(store,['a'],200,100,next_at=210)
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                sync(store,['b'],400,201)
                raise RuntimeError('abort')
        assert current(store)['id']==old
        with transaction(store.db):new=sync(store,['b'],400,201)
        assert new!=old
        assert tuple(store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(old,)).fetchone())==(200,'resolved')
        assert current(store)['id']==new
    finally:store.db.close()


def test_virtual_expiry_matches_tick_and_does_not_write_on_read(tmp_path,postgres_url):
    from oko.storage.youtube_admission import project,finish_due
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):old=sync(store,['a'],200,100,next_at=210)
        before=store.db.execute('SELECT *,xmin::text AS version FROM reader_problems WHERE id=%s',(old,)).fetchone()
        visible=project(before,201)
        after=store.db.execute('SELECT xmin::text FROM reader_problems WHERE id=%s',(old,)).fetchone()[0]
        assert after==before['version'] and visible['finished_at']==200 and visible['next_at']==210
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                assert finish_due(store,201)
                raise RuntimeError('rollback')
        assert current(store)['id']==old
        with transaction(store.db):assert finish_due(store,201)
        persisted=store.db.execute('SELECT * FROM reader_problems WHERE id=%s',(old,)).fetchone()
        assert persisted['finished']==visible['finished_at'] and persisted['outcome']==visible['outcome']
        assert json.loads(persisted['body'])['objects']==visible['objects']
        assert project(persisted,201)==visible
    finally:store.db.close()


def test_unowned_isolated_marker_is_preserved_and_not_claimed(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        raw=json.dumps(dict(code='foreign',objects=[]))
        malformed=json.dumps(dict(code='youtube-admission',operation='producer:youtube-admission',youtube_admission_owner='youtube:admission',objects=[None]))
        with transaction(store.db):
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','youtube:admission','background',%s,1),('bad','youtube:admission:isolated:bad','background',%s,2)",(raw,malformed))
        with transaction(store.db):created=sync(store,[],200,100)
        assert created not in ('foreign','bad') and current(store)['id']==created
        assert store.db.execute("SELECT body FROM reader_problems WHERE id='bad'").fetchone()[0]==malformed
    finally:store.db.close()
