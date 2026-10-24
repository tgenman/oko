"""A pending publication is not a previously saved full copy."""
import pytest
from tests.support.source import publication_packet, save_material
from oko.collection.contracts import Material, Publication
from oko.storage.store import Store
from oko.subscriptions.subscriptions import AddFeed


@pytest.mark.parametrize('members', [(), (1, 2)])
def test_first_save_accepts_pending_material_without_full_flag(tmp_path, members):
    store = Store(tmp_path, lambda _:None)
    try:
        feed = store.subscriptions.add_feed(AddFeed(url='https://source.test/feed',title='Synthetic',folder='Inbox',verification='synthetic'))['id']
        store.enqueue_discovery(feed)
        discovery = store.next_job(0, feeds=[feed])
        assert discovery is not None and discovery.kind == 'discover'
        store.discovered([publication_packet(Publication('one','https://source.test/one',0,members=members))],'{}',discovery,feed)
        job = store.next_job(0, feeds=[feed])
        assert job is not None and job.kind == 'fetch'
        pending = store.material(job.item)
        assert pending is not None and not pending['complete'] and 'full' not in pending
        save_material(store, job, Material('Saved title','Saved text',members=members))
        saved = store.material(job.item)
        assert saved is not None and saved['complete'] and saved['full']
        assert saved['text'] == 'Saved text'
    finally:
        store.db.close()
