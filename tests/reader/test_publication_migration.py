"""Latest-publication migration at the offline-copy and reader HTTP boundaries."""
import hashlib
from pathlib import Path

import httpx

from tests.support.app import create_app
from tests.support.telegram import configure_fast_web, settled
from tests.support.telegram_web import page
from tests.support.v3 import TestClient

HEADERS = {'Authorization': 'Bearer test'}


def snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_migration_preserves_source_and_reads_latest_without_history(tmp_path):
    from oko.storage.migration import migrate_publications

    source, destination = tmp_path/'old', tmp_path/'migration'
    text = ['First text']
    def response(request):
        return httpx.Response(200, text=page([(11, text[0])]))
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(source, **options), headers=HEADERS) as client:
        configure_fast_web(client)
        result = client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        assert result.status_code == 200, result.text
        item = settled(client)[0]['id']
        text[0] = 'Latest text'
        assert client.post('/api/v3/materials/'+item+'/retry').status_code == 200
        settled(client)
        material = client.get('/api/v3/materials/'+item).json()
        assert material['content']['text'] == 'Latest text'
        before_settings = client.get('/api/v3/subscriptions').json()
    before = snapshot(source)
    report = migrate_publications(source, destination)
    assert report['success'], report
    assert snapshot(source) == before
    assert snapshot(destination/'backup') == before
    assert report['publications'][0]['old_id'] == item
    target = destination/'storage'
    with TestClient(create_app(target, **options), headers=HEADERS) as client:
        actual = client.get('/api/v3/materials/'+item).json()
        assert actual['content']['text'] == 'Latest text'
        assert actual['content']['previous_versions'] == []
        assert client.get('/api/v3/subscriptions').json()['folders'] == before_settings['folders']
    # The compatibility projection is disposable, SQLite owns current content.
    import shutil
    shutil.rmtree(target/'content')
    with TestClient(create_app(target, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/materials/'+item).json()['content']['text'] == 'Latest text'
        assert client.post('/api/v3/materials/'+item+'/viewed').status_code == 200
        assert client.get('/api/v3/materials/'+item).status_code == 404


def test_migrated_web_path_updates_sqlite_and_has_no_previous_versions(tmp_path):
    from oko.storage.migration import migrate_publications
    import json
    import sqlite3
    from uuid import UUID

    source, destination = tmp_path/'old', tmp_path/'migration'
    phase = [0]
    def response(request):
        rows = [(11, 'Initial' if phase[0]==0 else 'Edited')]
        if phase[0] == 2:
            rows.append((12, 'New publication'))
        before = int(request.url.params.get('before', '0'))
        return httpx.Response(200, text=page([(i, text) for i, text in rows if not before or i<before]))
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(source, **options), headers=HEADERS) as client:
        configure_fast_web(client)
        result = client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        feed = result.json()['feed_id']
        item = settled(client)[0]['id']
    assert migrate_publications(source, destination)['success']
    target = destination/'storage'
    phase[0] = 1
    with TestClient(create_app(target, **options), headers=HEADERS) as client:
        assert client.post('/api/v3/materials/'+item+'/retry').status_code == 200
        settled(client)
        material = client.get('/api/v3/materials/'+item).json()
        assert material['content']['text'] == 'Edited'
        assert material['content']['previous_versions'] == []
        with sqlite3.connect(target/'state.sqlite') as db:
            content = json.loads(db.execute('SELECT content FROM items WHERE id=?', (item,)).fetchone()[0])
            assert content['blocks'][0] == {'type':'text', 'role':'body', 'text':'Edited'}
        phase[0] = 2
        client.post('/api/v3/collect')
        cards = settled(client, 2)
        new_id = next(c['id'] for c in cards if c['id'] != item)
        UUID(new_id)
        with sqlite3.connect(target/'state.sqlite') as db:
            row = db.execute('SELECT platform,external_id FROM items WHERE id=?', (new_id,)).fetchone()
            assert row == ('telegram', 'https://t.me/fixture/12')
            assert db.execute('SELECT 1 FROM feed_publications WHERE feed_id=? AND publication_id=?', (feed, new_id)).fetchone()
    with TestClient(create_app(target, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/materials/'+item).json()['content']['text'] == 'Edited'


def test_migration_reports_global_key_conflict_and_publishes_no_partial_storage(tmp_path):
    from oko.storage.migration import migrate_publications
    import sqlite3

    source, destination = tmp_path/'old', tmp_path/'migration'
    def response(request):
        name = request.url.path.split('/')[2]
        return httpx.Response(200, text=page([(11, 'Body')]).replace('fixture/', name+'/'))
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(source, **options), headers=HEADERS) as client:
        configure_fast_web(client)
        for name in ('first', 'second'):
            assert client.post('/api/v3/telegram/connect', json=dict(source_locator='@'+name, folder='Inbox')).status_code == 200
        settled(client, 2)
    # A legacy per-feed key can conflict under the new global key. The migration
    # must report it rather than choose one of these independent copies.
    with sqlite3.connect(source/'state.sqlite') as db:
        db.create_function('oko_reader_time', 0, lambda: 0.)
        db.execute("UPDATE items SET url='https://t.me/first/11'")
    before = snapshot(source)
    report = migrate_publications(source, destination)
    assert not report['success']
    assert report['conflicts']
    assert not (destination/'storage').exists()
    assert snapshot(source) == before == snapshot(destination/'backup')


def test_migrated_media_is_owned_by_sqlite_and_survives_projection_rebuild(tmp_path):
    from oko.storage.migration import migrate_publications
    import json
    import shutil
    import sqlite3

    source, destination = tmp_path/'old', tmp_path/'migration'
    photo = '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg?signature=first\')"></a>'
    def response(request):
        if request.url.host == 'cdn.example':
            return httpx.Response(200, content=b'latest-photo', headers={'content-type':'image/jpeg'})
        return httpx.Response(200, text=page([(11, 'Photo'+photo)]))
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(source, **options), headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        item = settled(client)[0]['id']
    assert migrate_publications(source, destination)['success']
    target = destination/'storage'
    with sqlite3.connect(target/'state.sqlite') as db:
        identity, size, status, key = db.execute('SELECT id,size_bytes,status,storage_key FROM media_files').fetchone()
        assert (size, status) == (12, 'ready')
        content, media_ids = db.execute('SELECT content,media_ids FROM items WHERE id=?', (item,)).fetchone()
        assert json.loads(media_ids) == [identity]
        assert {'type':'media', 'role':'image', 'media_id':identity} in json.loads(content)['blocks']
    assert (target/key).read_bytes() == b'latest-photo'
    shutil.rmtree(target/'content')
    with TestClient(create_app(target, **options), headers=HEADERS) as client:
        material = client.get('/api/v3/materials/'+item).json()
        assert client.get(material['content']['attachments'][0]['url']).content == b'latest-photo'


def test_migration_keeps_proven_results_and_edit_reprocesses_current_input(tmp_path):
    from oko.storage.migration import migrate_publications
    from tests.support.models import model_transport
    from tests.support.reader import wait_for
    import json
    import sqlite3

    source, destination = tmp_path/'old', tmp_path/'migration'
    text = ['Initial text']
    calls = []
    def response(request):
        return httpx.Response(200, text=page([(11, text[0])]))
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response), ai_transport=model_transport(calls))
    with TestClient(create_app(source, **options), headers=HEADERS) as client:
        configure_fast_web(client)
        client.put('/api/v3/processors/llamacpp', json={'endpoint':'http://127.0.0.1:8080', 'model':'local:latest', 'local_execution':True})
        client.patch('/api/v3/settings/oko/global', json={'processor':'llamacpp', 'theses':True})
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        item = settled(client)[0]['id']
        before = wait_for(client, lambda: (m if (m := client.get('/api/v3/materials/'+item).json())['theses']['result'] else None))
    count = len([r for r in calls if r.url.path == '/v1/chat/completions'])
    report = migrate_publications(source, destination)
    assert report['success']
    assert report['publications'][0]['results'] == ['theses']
    target = destination/'storage'
    with TestClient(create_app(target, **options), headers=HEADERS) as client:
        preserved = client.get('/api/v3/materials/'+item).json()
        assert preserved['theses']['result'] == before['theses']['result']
        assert len([r for r in calls if r.url.path == '/v1/chat/completions']) == count
        # An unchanged reload retains the proven result and its provenance.
        client.post('/api/v3/materials/'+item+'/retry')
        settled(client)
        assert client.get('/api/v3/materials/'+item).json()['theses']['result'] == before['theses']['result']
        assert len([r for r in calls if r.url.path == '/v1/chat/completions']) == count
        text[0] = 'New input text'
        client.post('/api/v3/materials/'+item+'/retry')
        settled(client)
        wait_for(client, lambda: (len([r for r in calls if r.url.path == '/v1/chat/completions']) > count
                  and client.get('/api/v3/materials/'+item).json()['theses']['result']))
    with sqlite3.connect(target/'state.sqlite') as db:
        summarized, metadata = db.execute('SELECT summarized_content,processing_metadata FROM items WHERE id=?', (item,)).fetchone()
        assert json.loads(summarized)['points']
        assert json.loads(metadata)['summary']['processor']=='llamacpp'
        assert json.loads(metadata)['summary']['model']=='local:latest'
    inputs = [json.loads(json.loads(r.content)['messages'][1]['content']) for r in calls if r.url.path == '/v1/chat/completions']
    assert inputs[-1]['sources'][0]['text'] == 'New input text'


def test_unproven_filter_file_is_not_migrated_as_an_accepted_result(tmp_path):
    from oko.storage.migration import migrate_publications
    from tests.support.filters import transport
    from tests.support.reader import wait_for
    import json
    import sqlite3

    source, destination = tmp_path/'old', tmp_path/'migration'
    calls = []
    def response(request):
        return httpx.Response(200, text=page([(11, 'A report with facts')]))
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response), ai_transport=transport(calls))
    with TestClient(create_app(source, **options), headers=HEADERS) as client:
        configure_fast_web(client)
        client.put('/api/v3/processors/llamacpp', json={'endpoint':'http://127.0.0.1:8080', 'model':'local:latest', 'local_execution':True})
        client.patch('/api/v3/settings/oko/global', json={'processor':'llamacpp', 'theses':False, 'prompt_filters':'Keep facts'})
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        item = settled(client)[0]['id']
        wait_for(client, lambda: client.get('/api/v3/materials/'+item).json()['filter']['attempt']['state']=='ready')
    with sqlite3.connect(source/'state.sqlite') as db:
        number = db.execute('SELECT number FROM content_current WHERE item=?', (item,)).fetchone()[0]
    path = source/'content'/item/str(number)/'filter.md'
    header, body = path.read_text().split('\n\n', 1)
    value = json.loads(header)
    value['signature'] = 'unproven-input'
    path.write_text(json.dumps(value)+'\n\n'+body)
    report = migrate_publications(source, destination)
    assert report['success']
    assert report['publications'][0]['results'] == []
    target = destination/'storage'
    with sqlite3.connect(target/'state.sqlite') as db:
        assert db.execute('SELECT filtered_content,status FROM items WHERE id=?', (item,)).fetchone() == ('{}', 'pending_filtering')
        assert db.execute("SELECT status FROM jobs WHERE item=? AND kind='filter'", (item,)).fetchone()[0] == 'pending'
    count = len(calls)
    with TestClient(create_app(target, **options), headers=HEADERS) as client:
        wait_for(client, lambda: client.get('/api/v3/materials/'+item).json()['filter']['attempt']['state']=='ready')
        assert len(calls) > count


def test_migration_refuses_a_locked_storage_without_writing(tmp_path):
    from oko.storage.migration import migrate_publications
    import fcntl
    import pytest

    source, destination = tmp_path/'old', tmp_path/'migration'
    with TestClient(create_app(source, token='test', api_only=True), headers=HEADERS):
        pass
    with (source/'instance.lock').open('wb') as owner:
        owner.write(b'synthetic-owner')
        owner.flush()
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = snapshot(source)
        with pytest.raises(BlockingIOError):
            migrate_publications(source, destination)
        assert snapshot(source) == before
        assert not destination.exists()


def test_migrated_reload_keeps_saved_copy_when_source_restricts_whole_post(tmp_path):
    from oko.storage.migration import migrate_publications
    from tests.support.telegram_web import mark_post_unavailable

    source, destination = tmp_path/'old', tmp_path/'migration'
    restricted = [False]
    def response(request):
        html = page([(11, '' if restricted[0] else 'Saved own text')])
        return httpx.Response(200, text=mark_post_unavailable(html, 'fixture/11') if restricted[0] else html)
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(source, **options), headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        item = settled(client)[0]['id']
    assert migrate_publications(source, destination)['success']
    restricted[0] = True
    with TestClient(create_app(destination/'storage', **options), headers=HEADERS) as client:
        client.post('/api/v3/materials/'+item+'/retry')
        settled(client)
        content = client.get('/api/v3/materials/'+item).json()['content']
        assert content['text'] == 'Saved own text'
        assert content['post_web_unavailable']
        assert content['previous_versions'] == []


def test_migration_preserves_external_video_in_common_content(tmp_path):
    from oko.storage.migration import migrate_publications
    from tests.support.telegram_web import external_page, VIDEO
    from tests.support.youtube import PNG
    import json
    import sqlite3

    source, destination = tmp_path/'old', tmp_path/'migration'
    def response(request):
        if request.url.host == 'cdn.example':
            return httpx.Response(200, content=PNG, headers={'content-type':'image/png'})
        return httpx.Response(200, text=external_page([(11, 'External video'+VIDEO)], before=int(request.url.params.get('before', '0'))))
    options = dict(token='test', api_only=True, rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(source, **options), headers=HEADERS) as client:
        configure_fast_web(client)
        client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        item = settled(client)[0]['id']
    assert migrate_publications(source, destination)['success']
    with sqlite3.connect(destination/'storage/state.sqlite') as db:
        content = json.loads(db.execute('SELECT content FROM items WHERE id=?', (item,)).fetchone()[0])
        assert {'type':'external_media', 'kind':'video', 'url':'https://t.me/fixture/11'} in content['blocks']
        assert db.execute('SELECT COUNT(*) FROM media_files').fetchone()[0] == 1


def test_new_feed_on_migrated_storage_uses_uuid_identity(tmp_path):
    from oko.storage.migration import migrate_publications
    from uuid import UUID

    source, destination = tmp_path/'old', tmp_path/'migration'
    def response(request):
        if request.url.host == 'example.org':
            return httpx.Response(200, text=('<rss version="2.0"><channel><title>RSS</title><item><guid>entry</guid><title>RSS entry</title><link>https://example.org/article</link></item></channel></rss>'
                if request.url.path == '/feed.xml' else '<article>Article text</article>'))
        return httpx.Response(200, text=page([(11, 'New feed')]))
    options = dict(token='test', api_only=True, rss_enabled=True, rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(source, **options), headers=HEADERS):
        pass
    assert migrate_publications(source, destination)['success']
    with TestClient(create_app(destination/'storage', **options), headers=HEADERS) as client:
        configure_fast_web(client)
        response = client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Inbox'))
        assert response.status_code == 200, response.text
        UUID(response.json()['feed_id'])
        UUID(settled(client)[0]['id'])
        from tests.support.feed_url import verify, add
        checked = verify(client)
        rss = add(client, checked)
        assert rss.status_code == 200, rss.text
        UUID(rss.json()['id'])
        cards = settled(client, 2)
        rss_card = next(c for c in cards if c['feed_id'] == rss.json()['id'])
        from tests.support.reader import wait_for
        article=wait_for(client,lambda:(m if 'Article text' in (m:=client.get('/api/v3/materials/'+rss_card['id']).json())['content']['text'] else None))
        assert 'https://example.org/article' in article['content']['text']


def test_migration_does_not_relabel_results_when_processing_text_changes(tmp_path):
    import sqlite3
    from oko.storage.migration import migrate_publications
    from tests.support.models import model_transport
    from tests.support.reader import wait_for
    source,destination=tmp_path/'old',tmp_path/'migration'
    def response(request):
        if request.url.host=='api.telegra.ph':
            return httpx.Response(200,json={'ok':True,'result':{'title':'Article','content':[{'tag':'p','children':['Article text']}]}})
        return httpx.Response(200,text=page([(11,'Post <a href="https://telegra.ph/Fixture">article</a>')]))
    with TestClient(create_app(source,token='test',api_only=True,rss_transport=httpx.MockTransport(response),ai_transport=model_transport([])),headers=HEADERS) as client:
        configure_fast_web(client)
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9931','model':'local:latest','local_execution':True})
        client.patch('/api/v3/settings/oko/global',json={'processor':'llamacpp','theses':True,'prompt_filters':''})
        client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox'))
        item=wait_for(client,lambda:next((c['id'] for c in client.get('/api/v3/cards').json() if c['theses']['result']),None))
    report=migrate_publications(source,destination)
    assert report['success']
    assert report['publications'][0]['results']==[]
    assert any(row['publication_id']==item and 'processing text' in row['reason'] for row in report['unportable'])
    with sqlite3.connect(destination/'storage'/'state.sqlite') as db:
        assert db.execute('SELECT summarized_content FROM items WHERE id=?',(item,)).fetchone()[0]=='{}'
    assert list((destination/'backup'/'content'/item).glob('*/theses.md'))
