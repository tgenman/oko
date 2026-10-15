from tests.support.telegram import connect_request
import time
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.telegram import Telegram
from tests.support.feed_url import HEADERS

from tests.support.telegram_history import Archive

def test_count_selects_whole_album_and_resumes_without_live_count(tmp_path):
    archive=Archive()
    def open_reader():
        return TestClient(create_app(tmp_path,token='test',telegram_client=archive),headers=HEADERS)
    with open_reader() as client:
        client.post("/api/tabs/reader/heartbeat")
        response=connect_request(client,json=dict(account='fixture',peer='@fixture',folder='Telegram',auto_read=False,initial_history=dict(mode='count',count=2)))
        assert response.status_code==200,response.text
        feed=response.json()
        for _ in range(200):
            h=client.get('/api/settings/feed/'+feed['id']).json()['feed']['initial_history']
            if h and h['state']=='done': break
            time.sleep(.02)
        assert h['loaded']==2
        assert {c['members'][0] for c in client.get('/api/cards').json()}=={4,6}
    archive.rows.append(dict(id=7,date=1759350007,text='new',group=None,media=None))
    with open_reader() as client:
        client.post("/api/tabs/reader/heartbeat")
        client.post('/api/tabs/reader/heartbeat')
        for _ in range(200):
            cards=client.get('/api/cards').json()
            if len(cards)==3: break
            time.sleep(.02)
        assert len(cards)==3
        assert client.get('/api/settings/feed/'+feed['id']).json()['feed']['initial_history']['loaded']==2

def test_telegram_date_is_inclusive_and_validation_matches_rss(tmp_path):
    archive=Archive()
    archive.rows=[dict(id=n,date=1759352400+offset,text=str(n),group=None,media=None) for n,offset in [(1,-1),(2,0),(3,1)]]
    with TestClient(create_app(tmp_path,token='test',telegram_client=archive),headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        body=dict(account='fixture',peer='@fixture',folder='Telegram',auto_read=False)
        assert connect_request(client,json=dict(**body,initial_history=dict(mode='count',count=0))).status_code==422
        feed=connect_request(client,json=dict(**body,initial_history=dict(mode='date',count=None,date='2025-10-02',timezone='Europe/Moscow'))).json()
        for _ in range(200):
            h=client.get('/api/settings/feed/'+feed['id']).json()['feed']['initial_history']
            if h['state']=='done':break
            time.sleep(.02)
        assert h['loaded']==2
        assert {c['members'][0] for c in client.get('/api/cards').json()}=={2,3}

def test_snapshot_and_sparse_group_pages_resume_before_older_pages(tmp_path):
    import asyncio
    class Sparse(Archive):
        def __init__(self):
            super().__init__()
            self.blocked=True
            self.calls=[]
        async def info(self,account,peer,topic_id=None):
            return dict(peer=-1001234567890,title='Общее',kind='group',forum=True,topic_id=topic_id)
        async def history_page(self,account,peer,upper,before,since,**scope):
            self.calls.append((upper,before,scope.get('topic_id')))
            if before==5:
                if self.blocked: await asyncio.Event().wait()
                return dict(messages=[],upper=6,next_before=3,more=True)
            if before==3:
                return dict(messages=[m for m in self.rows if m['id']==2],upper=6,next_before=1,more=False)
            return dict(messages=[m for m in self.rows if m['id']==6],upper=6,next_before=5,more=True)
    archive=Sparse()
    def open_reader(): return TestClient(create_app(tmp_path,token='test',telegram_client=archive),headers=HEADERS)
    with open_reader() as client:
        client.post('/api/tabs/reader/heartbeat')
        feed=connect_request(client,json=dict(account='fixture',peer='@fixture',folder='Telegram',auto_read=False,initial_history=dict(mode='count',count=2))).json()
        for _ in range(100):
            h=client.get('/api/settings/feed/'+feed['id']).json()['feed']['initial_history']
            if any(before==5 for _,before,_ in archive.calls):break
            time.sleep(.02)
        assert h['state']=='selecting'
        assert h['available']==1
        assert h['loaded']==0
    archive.blocked=False
    archive.rows.append(dict(id=7,date=1759350007,text='live',group=None,media=None))
    with open_reader() as client:
        client.post('/api/tabs/reader/heartbeat')
        for _ in range(200):
            h=client.get('/api/settings/feed/'+feed['id']).json()['feed']['initial_history']
            if h['state']=='done':break
            time.sleep(.02)
        assert h['loaded']==2
        assert all(topic is None for _,_,topic in archive.calls)
        assert any(upper==6 and before==3 for upper,before,_ in archive.calls)
        assert {c['members'][0] for c in client.get('/api/cards').json()}=={2,6,7}

def test_count_history_keeps_safe_numeric_read_boundary(tmp_path):
    class ReadArchive(Archive):
        def __init__(self):
            super().__init__()
            self.acks=[]
        async def read_window(self,account,peer,after,until,**scope):
            return dict(ids=[m['id'] for m in self.rows if after<m['id']<=until],more=False)
        async def acknowledge(self,account,peer,max_id,**scope):
            self.acks.append(max_id)
    archive=ReadArchive()
    with TestClient(create_app(tmp_path,token='test',telegram_client=archive),headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        feed=connect_request(client,json=dict(account='fixture',peer='@fixture',folder='Telegram',auto_read=True,initial_history=dict(mode='count',count=2))).json()
        for _ in range(200):
            reads=client.get('/api/status').json()['telegram_reads']
            if reads and reads[0]['waiting']:break
            time.sleep(.02)
        assert reads[0]['initial']==3 # Before oldest member of the chosen whole album (4,5).
        assert reads[0]['confirmed']==0
        assert 'Старая непрочитанная история' in reads[0]['waiting']
        assert archive.acks==[]


def test_archive_error_does_not_delay_live_and_survives_restart(tmp_path):
    from oko.collection.contracts import SourceError
    class FailingArchive(Archive):
        def __init__(self):
            super().__init__()
            self.failed = False
            self.live_calls = 0
        async def history_page(self, account, peer, upper, before, since, **scope):
            if before:
                self.failed = True
                raise SourceError('Архив временно недоступен', 600)
            return await super().history_page(account, peer, upper, before, since, **scope)
        async def page(self, *args, **kwargs):
            self.live_calls += 1
            if self.failed and not any(m['id'] == 7 for m in self.rows):
                self.rows.append(dict(id=7, date=1759350007, text='live', group=None, media=None))
            return await super().page(*args, **kwargs)
    archive = FailingArchive()
    def reader(): return TestClient(create_app(tmp_path, token='test', telegram_client=archive), headers=HEADERS)
    with reader() as client:
        client.post('/api/tabs/reader/heartbeat')
        feed = connect_request(client, json=dict(account='fixture', peer='@fixture', folder='Telegram', auto_read=False, initial_history=dict(mode='count', count=5))).json()
        for _ in range(150):
            cards = client.get('/api/cards').json()
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if any(c['members'] == [7] for c in cards): break
            time.sleep(.02)
        assert any(c['members'] == [7] for c in cards)
        assert history['discovery_error'] == 'Архив временно недоступен'
        assert history['discovery_retry_at'] > time.time()
        assert client.get('/api/debug').json()['groups']
    with reader() as client:
        client.post('/api/tabs/reader/heartbeat')
        history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
        assert history['discovery_error'] == 'Архив временно недоступен'
        assert history['discovery_retry_at'] > time.time()


def test_count_metadata_manifest_finishes_before_content_and_never_replaces_deleted_member(tmp_path):
    class ManifestArchive(Archive):
        def __init__(self):
            self.rows = [dict(id=n, date=1759350000+n, text=str(n), group=None, media=None) for n in (1,2,3,4)]
            self.pages = 0
            self.content = []
            self.early_content = False
        async def history_page(self, *args, **kwargs):
            self.pages += 1
            return await super().history_page(*args, **kwargs)
        async def messages(self, account, peer, ids, **scope):
            self.early_content |= self.pages < 2
            self.content.extend(ids)
            self.rows = [m for m in self.rows if m['id'] != 2]
            return await super().messages(account, peer, ids, **scope)
    archive = ManifestArchive()
    with TestClient(create_app(tmp_path, token='test', telegram_client=archive), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        feed = connect_request(client, json=dict(account='fixture', peer='@fixture', folder='Telegram', auto_read=False, initial_history=dict(mode='count', count=3))).json()
        for _ in range(200):
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if history['state'] == 'done': break
            time.sleep(.02)
        assert not archive.early_content
        assert history['loaded'] == 2
        assert history['available'] == 3
        assert history['unavailable'] == 1
        assert set(archive.content) == {2,3,4}
        assert {c['members'][0] for c in client.get('/api/cards').json()} == {2,3,4}


def test_count_skips_service_actions_but_keeps_empty_media_posts(tmp_path):
    class ServiceArchive(Archive):
        def __init__(self):
            self.rows = [dict(id=n, date=1759350000+n, text='', group=None, media=None, service=n in (4,5)) for n in range(1,7)]
            self.rows[2]['text'] = 'ordinary post'
            self.rows[5]['media'] = dict(kind='unsupported', mime='application/octet-stream', extension='bin')
            self.acks = []
        async def read_window(self, account, peer, after, until, **scope):
            return dict(ids=[m['id'] for m in self.rows if after < m['id'] <= until], more=False)
        async def acknowledge(self, account, peer, max_id, **scope):
            self.acks.append(max_id)
    with TestClient(create_app(tmp_path, token='test', telegram_client=ServiceArchive()), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        feed = connect_request(client, json=dict(account='fixture', peer='@fixture', folder='Telegram', auto_read=False, initial_history=dict(mode='count', count=2))).json()
        for _ in range(200):
            history = client.get('/api/settings/feed/' + feed['id']).json()['feed']['initial_history']
            if history['state'] == 'done': break
            time.sleep(.02)
        assert history['loaded'] == 2
        assert {c['members'][0] for c in client.get('/api/cards').json()} == {3,6}


def test_service_ids_in_selected_span_still_block_unsafe_read_acknowledgment(tmp_path):
    class ServiceArchive(Archive):
        def __init__(self):
            self.rows = [dict(id=n, date=1759350000+n, text=str(n), group=None, media=None, service=n in (4,5)) for n in range(1,7)]
            self.acks = []
        async def read_window(self, account, peer, after, until, **scope):
            return dict(ids=[m['id'] for m in self.rows if after < m['id'] <= until], more=False)
        async def acknowledge(self, account, peer, max_id, **scope):
            self.acks.append(max_id)
    archive = ServiceArchive()
    with TestClient(create_app(tmp_path, token='test', telegram_client=archive), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        feed = connect_request(client, json=dict(account='fixture', peer='@fixture', folder='Telegram', auto_read=True, initial_history=dict(mode='count', count=2))).json()
        client.patch('/api/feeds/' + feed['id'], json={'read_history': True})
        for _ in range(200):
            reads = client.get('/api/status').json()['telegram_reads']
            if reads and reads[0]['confirmed'] == 3 and reads[0]['waiting']: break
            time.sleep(.02)
        assert reads[0]['initial'] == 2
        assert reads[0]['confirmed'] == 3
        assert 6 not in archive.acks
        assert reads[0]['waiting']
