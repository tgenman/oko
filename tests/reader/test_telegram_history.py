from tests.support.cards import read_all_cards
from tests.support.telegram import connected_feed
from tests.support.presence import presence, close_presence
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
        presence(client, 'reader')
        response=connect_request(client,json=dict(account='fixture',peer='@fixture',folder='Telegram',auto_read=False,initial_history=dict(mode='count',count=2)))
        assert response.status_code==200,response.text
        feed=connected_feed(client,response)
        for _ in range(200):
            h=client.get('/api/v3/settings/feed/'+feed['id']).json()['feed']['history']
            if h and h['state']=='complete': break
            time.sleep(.02)
        assert h['progress']['loaded']==2
        assert {[int(identity) for identity in client.get('/api/v3/materials/'+c['id']).json()['source']['message_ids']][0] for c in read_all_cards(client, '/api/v3/cards')}=={4,6}
    archive.rows.append(dict(id=7,date=1759350007,text='new',group=None,media=None))
    with open_reader() as client:
        presence(client, 'reader')
        presence(client, 'reader')
        for _ in range(200):
            cards=read_all_cards(client, '/api/v3/cards')
            if len(cards)==3: break
            time.sleep(.02)
        assert len(cards)==3
        assert client.get('/api/v3/settings/feed/'+feed['id']).json()['feed']['history']['progress']['loaded']==2



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
        presence(client, 'reader')
        feed=connected_feed(client, connect_request(client,json=dict(account='fixture',peer='@fixture',folder='Telegram',auto_read=True,initial_history=dict(mode='count',count=2))))
        for _ in range(200):
            reads=client.get('/api/v3/status').json()['telegram_reads']
            if reads and reads[0]['waiting']:break
            time.sleep(.02)
        assert reads[0]['initial']=='3' # Before oldest member of the chosen whole album (4,5).
        assert reads[0]['confirmed'] is None
        assert reads[0]['waiting']=='telegram_read_frontier'
        assert archive.acks==[]


def test_archive_error_and_fixed_boundary_survive_restart_before_the_next_window(tmp_path):
    from oko.collection.contracts import SourceError
    from tests.support.reader import wait_for
    class FailingArchive(Archive):
        broken=True
        async def history_page(self,account,peer,upper,before,since,**scope):
            if before and self.broken:
                if not any(row['id']==7 for row in self.rows):
                    self.rows.append(dict(id=7,date=1759350007,text='live',group=None,media=None))
                raise SourceError('Архив временно недоступен',600)
            return await super().history_page(account,peer,upper,before,since,**scope)
    archive=FailingArchive()
    def reader():
        return TestClient(create_app(tmp_path,token='test',telegram_client=archive),headers=HEADERS)
    with reader() as client:
        presence(client,'reader')
        feed=connected_feed(client,connect_request(client,json=dict(account='fixture',peer='@fixture',folder='Telegram',auto_read=False,initial_history=dict(mode='count',count=5))))
        def history():
            return client.get('/api/v3/settings/feed/'+feed['id']).json()['feed']['history']
        failed=wait_for(client,lambda: value if (value:=history())['state']=='waiting' else None)
        assert failed['progress']['discovery_problem']['message']=='Архив временно недоступен'
        assert not failed['progress']['scan_complete']
        retry=failed['progress']['discovery_problem']['retry_at']
    with reader() as client:
        presence(client,'reader')
        saved=history()
        assert saved['state']=='waiting' and saved['progress']['discovery_problem']['retry_at']==retry
        archive.broken=False
        client.post('/api/v3/collect')
        wait_for(client,lambda:history()['state']=='complete')
        assert all(client.get('/api/v3/materials/'+card['id']).json()['source']['message_ids']!=['7'] for card in read_all_cards(client, '/api/v3/cards'))
        client.post('/api/v3/collect')
        wait_for(client,lambda:any(client.get('/api/v3/materials/'+card['id']).json()['source']['message_ids']==['7'] for card in read_all_cards(client, '/api/v3/cards')))


def test_count_discovery_accepts_ready_content_without_refetching_each_message(tmp_path):
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
        presence(client, 'reader')
        feed = connected_feed(client, connect_request(client, json=dict(account='fixture', peer='@fixture', folder='Telegram', auto_read=False, initial_history=dict(mode='count', count=3))))
        for _ in range(200):
            history = client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']
            if history['state'] == 'complete': break
            time.sleep(.02)
        assert not archive.early_content
        assert history['progress']['loaded'] == 3
        assert history['progress']['available'] == 3
        assert history['progress']['unavailable'] == 0
        assert archive.content == []
        assert {[int(identity) for identity in client.get('/api/v3/materials/'+c['id']).json()['source']['message_ids']][0] for c in read_all_cards(client, '/api/v3/cards')} == {2,3,4}


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
        presence(client, 'reader')
        feed = connected_feed(client, connect_request(client, json=dict(account='fixture', peer='@fixture', folder='Telegram', auto_read=False, initial_history=dict(mode='count', count=2))))
        for _ in range(200):
            history = client.get('/api/v3/settings/feed/' + feed['id']).json()['feed']['history']
            if history['state'] == 'complete': break
            time.sleep(.02)
        assert history['progress']['loaded'] == 2
        assert {[int(identity) for identity in client.get('/api/v3/materials/'+c['id']).json()['source']['message_ids']][0] for c in read_all_cards(client, '/api/v3/cards')} == {3,6}


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
        presence(client, 'reader')
        feed = connected_feed(client, connect_request(client, json=dict(account='fixture', peer='@fixture', folder='Telegram', auto_read=True, initial_history=dict(mode='count', count=2))))
        client.patch('/api/v3/feeds/' + feed['id'], json={'read_history': True})
        for _ in range(200):
            reads = client.get('/api/v3/status').json()['telegram_reads']
            if reads and reads[0]['confirmed'] == '3' and reads[0]['waiting']: break
            time.sleep(.02)
        assert reads[0]['initial'] == '2'
        assert reads[0]['confirmed'] == '3'
        assert 6 not in archive.acks
        assert reads[0]['waiting']
