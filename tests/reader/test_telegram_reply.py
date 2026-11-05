"""Telegram reply context through the reader HTTP interface."""
from tests.support.telegram import configure_fast_web
import httpx
import pytest

from tests.support.app import create_app
from tests.support.telegram import settled
from tests.support.telegram_web import page
from tests.support.v3 import TestClient


def quote(publication):
    return next(block for block in publication['content']['blocks'] if block['type']=='quote')


@pytest.mark.parametrize('href', ['/fixture/10', '10', 'https://t.me/fixture/10'])
def test_reply_is_separate_from_own_text_and_survives_restart(tmp_path, href):
    reply = '''<a class="tgme_widget_message_reply" href="/fixture/10">
<div class="tgme_widget_message_author"><span class="tgme_widget_message_author_name">Reply author</span></div>
<div class="tgme_widget_message_text js-message_reply_text">Quoted<br> context <img alt="🙂"> …</div></a>'''
    async def response(request):
        return httpx.Response(200, text=page([(11, reply.replace('href="/fixture/10"', 'href="'+href+'"')+'Own first line<br><br>Own paragraph')]))
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response))
    headers = {'Authorization': 'Bearer test'}
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        configure_fast_web(client)
        assert client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture',folder='Inbox')).status_code == 200
        card = settled(client)[0]
        assert card['title'] == 'Own first line'
        material = client.get('/api/v3/publication/'+card['id']).json()
        assert '\n\n'.join(b['text'] for b in material['content']['blocks'] if b['type']=='text' and b['role']=='body') == 'Own first line\n\nOwn paragraph'
        assert quote(material) == dict(type='quote',author='Reply author', text='Quoted context 🙂 …', source_url='https://t.me/fixture/10')
        frame = client.get('/api/v3/publication/'+card['id']).json()
        entity = frame
        assert quote(entity) == quote(material)
        assert entity['title'] == card['title']
        assert material['status'] == 'ready'
    import json
    from tests.support.storage import database
    with database(tmp_path) as db:
        row=db.execute('SELECT content FROM publications WHERE id=%s', (card['id'],)).fetchone()
        content=row[0]
        assert '_reader' not in content['extra']
        content['extra']['_reader']={'reply_preview':dict(quote(material), text='Legacy displayed context')}
        db.execute('UPDATE publications SET content=%s WHERE id=%s', (json.dumps(content), card['id']))
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        configure_fast_web(client)
        saved = client.get('/api/v3/publication/'+card['id']).json()
        assert quote(saved) == quote(material)
    with database(tmp_path) as db:
        content=db.execute('SELECT content FROM publications WHERE id=%s', (card['id'],)).fetchone()[0]
        assert 'manifest' not in content['extra']['telegram']
        assert content['extra']['_reader']['reply_preview']['text'] == 'Legacy displayed context'


def test_both_ai_operations_receive_reply_and_reply_changes_invalidate_results(tmp_path):
    import json
    from tests.support.models import model_transport
    from tests.support.presence import presence
    from tests.support.reader import wait_for
    calls = []
    native = model_transport([])
    phase = ['CONTEXT_ONE']
    def model(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task)
        source = task['sources'][0]
        if task['operation'] == 'filter':
            result = dict(hidden=source.get('reply_preview', {}).get('text')=='HIDE_CONTEXT', reason='Context evaluated', removed=[])
        else:
            result = dict(points=[dict(text='Summary '+phase[0], sources=[dict(id=source['id'],url=source['url'])])])
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
    async def response(request):
        reply = '<a class="tgme_widget_message_reply" href="https://t.me/fixture/10"><span class="tgme_widget_message_author_name">Other author</span><div class="js-message_reply_text">'+phase[0]+'</div></a>'
        return httpx.Response(200,text=page([(11,reply+'Own text marker')]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response),ai_transport=httpx.MockTransport(model)),headers={'Authorization':'Bearer test'}) as client:
        configure_fast_web(client)
        client.put('/api/v3/processors/llamacpp',json=dict(endpoint='http://localhost:9931',model='local:latest',local_execution=True))
        client.patch('/api/v3/settings/oko/global',json=dict(processor='llamacpp',theses=True,prompt_filters='Evaluate everything'))
        presence(client,'reader')
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        card = settled(client)[0]
        url = '/api/v3/materials/'+card['id']
        first = wait_for(client,lambda: m if (m:=client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json())['summarized_content'].get('points') else None)
        assert {c['operation'] for c in calls} == {'filter','theses'}
        assert all(c['sources'][0]['reply_preview']['text']=='CONTEXT_ONE' and 'Own text marker' in c['sources'][0]['text'] for c in calls)
        assert all(c['sources'][0]['reply_preview']['author']=='Other author' and c['sources'][0]['reply_preview']['source_url']=='https://t.me/fixture/10' for c in calls)
        count = len(calls)
        phase[0] = 'CONTEXT_TWO'
        assert client.post(url+'/retry').status_code == 200
        second=client.get(url.replace('/api/v3/materials/', '/api/v3/publication/')).json()
        assert quote(second)==quote(first)
        assert second['summarized_content']==first['summarized_content']
        assert second['content']==first['content']
        assert len(calls)==count
        assert 'previous_versions' not in second
