import pytest
from tests.support.storage import Store


def test_standalone_youtube_block_commit_failure_restores_timer_memory(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        assert not store.read_versions._processor_youtube_paused
        def fail():raise RuntimeError('commit failed')
        store.db.before_commit=fail
        with pytest.raises(RuntimeError,match='commit failed'):store.youtube.blocked(100,900)
        store.db.before_commit=None
        assert store.youtube._limits()['blocked_until']==0
        assert not store.read_versions._processor_youtube_paused
    finally:
        store.db.before_commit=None
        store.db.close()


def test_standalone_youtube_completed_commit_failure_restores_timer_memory(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        store.youtube.blocked(100,900)
        assert store.read_versions._processor_youtube_paused
        def fail():raise RuntimeError('commit failed')
        store.db.before_commit=fail
        with pytest.raises(RuntimeError,match='commit failed'):store.youtube.completed()
        store.db.before_commit=None
        assert store.youtube._limits()['blocked_until']==1000
        assert store.read_versions._processor_youtube_paused
    finally:
        store.db.before_commit=None
        store.db.close()


def test_standalone_advance_commit_failure_restores_timer_memory(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        store.youtube.blocked(100,900)
        assert store.read_versions._processor_youtube_paused
        clock[0]=1001
        # Force a DML after timer comparison to exercise a failing commit.
        with store.db:store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('old','old','background','{}',1,2,'resolved')")
        def fail():raise RuntimeError('commit failed')
        store.db.before_commit=fail
        with pytest.raises(RuntimeError,match='commit failed'):store.read_versions.advance(wall=clock[0]+86400)
        store.db.before_commit=None
        assert store.read_versions._processor_youtube_paused
    finally:
        store.db.before_commit=None
        store.db.close()


def test_failed_youtube_block_rolls_back_group_and_reservation(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'download_media','m',1),1,'missing',code='processor-config')
        original=store.problems.processor_current()['id']
        def fail():raise RuntimeError('commit failed')
        store.db.before_commit=fail
        with pytest.raises(RuntimeError,match='commit failed'):store.youtube.blocked(100,900)
        store.db.before_commit=None
        assert store.problems.processor_current()['id']==original
        assert store.problems.processor_reservation() is None
        assert not store.read_versions._processor_youtube_paused
    finally:
        store.db.before_commit=None
        store.db.close()
