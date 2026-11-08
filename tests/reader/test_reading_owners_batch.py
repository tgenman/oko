"""Read owner facts once without weakening linked-publication permissions."""
from fastapi.testclient import TestClient
import pytest

from oko.storage import material_read, processing_input
from oko.storage.reading import reading_snapshot
from oko.storage.transaction import read_snapshot, transaction
from tests.support.reading_cost import library, traced_app, tracing, wait_for_recovery
from tests.support.reader import HEADERS
from tests.support.storage import Store


def seed(root):
    feeds, items = library(root, [1, 1, 1])
    store = Store(root, lambda _: None)
    try:
        owner, other, grandchild_owner = feeds
        item = items[0]
        with transaction(store.db):
            store.db.execute('UPDATE feeds SET public_ai=1 WHERE id=%s', (owner,))
            store.db.execute('UPDATE feeds SET public_ai=0 WHERE id=ANY(%s)', ([other,grandchild_owner],))
            for identity, feed in [('first', other), ('no-owner', None), ('same-owner', owner), ('grandchild', grandchild_owner)]:
                store.db.execute("""INSERT INTO publications(id,url,platform,external_id,feed_id,title,content,status)
                    VALUES (%s,%s,'other_web',%s,%s,%s,%s::jsonb,'ready')""",
                    (identity, 'https://fixture.test/'+identity, identity, feed, identity, '{"blocks":[],"extra":{}}'))
            # Do not assume lexical or identity order equals the saved link order.
            for target in ('same-owner', 'no-owner', 'first'):
                store.db.execute('INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,%s)', (item,target))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('no-owner','grandchild')")
        return feeds,item
    finally:
        store.db.close()


def test_owner_inputs_keep_direct_public_ai_and_link_order_without_duplicate_sql(tmp_path):
    feeds,item = seed(tmp_path)
    store = Store(tmp_path, lambda _: None)
    try:
        sql=[]
        store.db.set_trace_callback(sql.append)
        try:
            with read_snapshot(store.db), reading_snapshot(), material_read.material_inputs(store.db,{item,'missing'}):
                value=material_read.current(store.db)
                assert value is not None
                assert value.owners[item] == set(feeds[:2])
                assert value.owners['missing'] == set()
                assert [child['id'] for child in value.children[item]] == ['same-owner','no-owner','first']
                assert processing_input.live_settings(store,item)['public_ai'] is False
        finally:
            store.db.set_trace_callback(None)
        assert not any('SELECT id AS publication_id,feed_id FROM publications' in q for q in sql)
        assert not any('SELECT m.id AS reader_source' in q for q in sql)
        with transaction(store.db):
            store.db.execute('UPDATE feeds SET public_ai=1 WHERE id=%s',(feeds[1],))
        with read_snapshot(store.db), reading_snapshot(), material_read.material_inputs(store.db,{item}):
            assert processing_input.live_settings(store,item)['public_ai'] is True
    finally:
        store.db.close()


def test_publication_http_preserves_link_order_and_conditional_response(tmp_path):
    _,item=seed(tmp_path)
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        wait_for_recovery(app)
        tracing(client,app)
        response=client.get('/api/v3/publication/'+item)
        assert response.status_code==200, response.text
        assert [value['id'] for value in response.json()['linked_publications']] == ['same-owner','no-owner','first']
        assert not any('SELECT id AS publication_id,feed_id FROM publications' in q for q in app.state.last_sql)
        assert not any('SELECT m.id AS reader_source' in q for q in app.state.last_sql)
        unchanged=client.get('/api/v3/publication/'+item,headers={'If-None-Match':response.headers['ETag']})
        assert unchanged.status_code==304


def test_owner_inputs_remain_bounded_across_400_item_batch(tmp_path):
    feeds,_ = seed(tmp_path)
    store = Store(tmp_path, lambda _: None)
    try:
        with transaction(store.db):
            store.db.execute("""INSERT INTO publications(id,url,platform,external_id,feed_id)
                SELECT 'batch-'||n,'https://fixture.test/batch/'||n,'other_web','batch-'||n,%s
                FROM generate_series(1,401) AS n""", (feeds[0],))
        identities={f'batch-{n}' for n in range(1,402)}
        sql=[]
        store.db.set_trace_callback(sql.append)
        try:
            with read_snapshot(store.db), reading_snapshot(), material_read.material_inputs(store.db,identities):
                value=material_read.current(store.db)
                assert value is not None
                assert len(value.owners)==401
                assert all(owners=={feeds[0]} for owners in value.owners.values())
        finally:
            store.db.set_trace_callback(None)
        reads=[query for query in sql if query.lstrip().upper().startswith('SELECT')]
        assert len(reads)<=10, reads
        assert sum('FROM publications i WHERE i.id = ANY' in query for query in reads)==2
        assert sum('FROM publication_links l JOIN publications i' in query for query in reads)==2
    finally:
        store.db.close()


@pytest.mark.parametrize('condition', ['viewed', 'deleted'])
def test_linked_owner_remains_in_public_ai_check_when_viewed_or_deleted(tmp_path, condition):
    feeds,item = seed(tmp_path)
    store = Store(tmp_path, lambda _: None)
    try:
        with transaction(store.db):
            if condition == 'viewed':
                store.db.execute("UPDATE publications SET viewed=true WHERE id='first'")
            else:
                store.db.execute('UPDATE feeds SET deleted=true WHERE id=%s', (feeds[1],))
        with read_snapshot(store.db), reading_snapshot(), material_read.material_inputs(store.db,{item}):
            value=material_read.current(store.db)
            assert value is not None
            previous={row['feed_id'] for row in store.db.execute('''SELECT DISTINCT p.feed_id FROM publications p
                WHERE p.feed_id IS NOT NULL AND (p.id=%s OR p.id IN
                    (SELECT target_publication_id FROM publication_links WHERE source_publication_id=%s))''', (item,item))}
            assert value.owners[item] == previous == set(feeds[:2])
            assert processing_input.live_settings(store,item)['public_ai'] is False
    finally:
        store.db.close()
