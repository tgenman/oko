import time
from fastapi.testclient import TestClient
from oko.reader.app import create_app
from tests.support.telegram import Telegram
from tests.reader.test_feed_url import HEADERS

class Archive(Telegram):
    def __init__(self):
        self.rows = [dict(id=n, date=1759350000+n, text=str(n), group=88 if n in (4,5) else None, media=None) for n in range(1,7)]
    async def history_page(self, account, peer, upper, before, since, **scope):
        rows = [m for m in self.rows if (not upper or m['id']<=upper) and (not before or m['id']<before)]
        raw = sorted(rows,key=lambda m:m['id'],reverse=True)[:2]
        expanded = list(raw)
        for m in raw:
            if m['group']:
                expanded += [r for r in self.rows if r['group']==m['group'] and r not in expanded]
        return dict(messages=expanded, upper=upper or max([0]+[m['id'] for m in self.rows]), next_before=min([m['id'] for m in raw],default=before), more=len(rows)>len(raw))
    async def page(self,account,peer,after,since,**scope):
        return dict(messages=[m for m in self.rows if m['id']>after],more=False,next_after=max([after]+[m['id'] for m in self.rows]))
    async def messages(self,account,peer,ids,**scope):
        return [m for m in self.rows if m['id'] in ids]

def test_count_selects_whole_album_and_resumes_without_live_count(tmp_path):
    archive=Archive()
    def open_reader():
        return TestClient(create_app(tmp_path,token='test',telegram_client=archive),headers=HEADERS)
    with open_reader() as client:
        client.post("/api/tabs/reader/heartbeat")
        response=client.post('/api/telegram/connect',json=dict(account='fixture',peer='@fixture',folder='Telegram',auto_read=False,initial_history=dict(mode='count',count=2)))
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
        assert client.post('/api/telegram/connect',json=dict(**body,initial_history=dict(mode='count',count=0))).status_code==422
        feed=client.post('/api/telegram/connect',json=dict(**body,initial_history=dict(mode='date',count=None,date='2025-10-02',timezone='Europe/Moscow'))).json()
        for _ in range(200):
            h=client.get('/api/settings/feed/'+feed['id']).json()['feed']['initial_history']
            if h['state']=='done':break
            time.sleep(.02)
        assert h['loaded']==2
        assert {c['members'][0] for c in client.get('/api/cards').json()}=={2,3}

def test_telegram_form_contains_both_initial_history_choices(tmp_path):
    with TestClient(create_app(tmp_path,token='test'),headers=HEADERS) as client:
        html=client.get('/').text
        assert 'id="telegram-history-mode"' in html
        assert 'id="telegram-history-count"' in html
        assert 'id="telegram-history-date"' in html

def test_snapshot_and_sparse_topic_resume_before_older_pages(tmp_path):
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
        feed=client.post('/api/telegram/connect',json=dict(account='fixture',peer='@fixture',topic_id=1,folder='Telegram',auto_read=False,initial_history=dict(mode='count',count=2))).json()
        for _ in range(100):
            h=client.get('/api/settings/feed/'+feed['id']).json()['feed']['initial_history']
            if any(before==5 for _,before,_ in archive.calls):break
            time.sleep(.02)
        assert h['state']=='loading'
        assert h['available']==1
        assert h['loaded']==1
    archive.blocked=False
    archive.rows.append(dict(id=7,date=1759350007,text='live',group=None,media=None))
    with open_reader() as client:
        client.post('/api/tabs/reader/heartbeat')
        for _ in range(200):
            h=client.get('/api/settings/feed/'+feed['id']).json()['feed']['initial_history']
            if h['state']=='done':break
            time.sleep(.02)
        assert h['loaded']==2
        assert all(topic==1 for _,_,topic in archive.calls)
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
        feed=client.post('/api/telegram/connect',json=dict(account='fixture',peer='@fixture',folder='Telegram',auto_read=True,initial_history=dict(mode='count',count=2))).json()
        for _ in range(200):
            reads=client.get('/api/status').json()['telegram_reads']
            if reads and reads[0]['waiting']:break
            time.sleep(.02)
        assert reads[0]['initial']==3 # Before oldest member of the chosen whole album (4,5).
        assert reads[0]['confirmed']==0
        assert 'Старая непрочитанная история' in reads[0]['waiting']
        assert archive.acks==[]
