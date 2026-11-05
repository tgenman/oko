from tests.support.source import save_material
"""Deterministic temporary libraries for HTTP/browser performance acceptance."""
from pathlib import Path
import argparse
import json

from tests.support.synthetic_material import material as Material
from oko.processing.config import ProcessorConfig
from tests.support.storage import Store
from oko.subscriptions.subscriptions import Overrides
from tests.support.library_templates import templates


def saved_library(root:Path, per_feed:int) -> dict[str,object]:
    """Clone one real accepted publication into a closed synthetic library."""
    from uuid import uuid4
    from tests.support.collection_acceptance import discovered
    from tests.support.legacy_source import Publication
    from tests.support.source import publication_packet
    from oko.storage.transaction import transaction
    from oko.subscriptions.subscriptions import AddFeed

    store=Store(root,lambda _:None)
    try:
        feeds=[]
        for index in range(4):
            feeds.append(store.subscriptions.add_feed(AddFeed(url='https://fixture.test/feed/'+str(index),
                title=f'Synthetic {index}',folder=f'Folder {index}',verification='synthetic'))['id'])
        store.subscriptions.update_settings('oko','global',Overrides(theses=False,prompt_filters='',processor='llamacpp'))
        store.jobs.enqueue_discovery(feeds[0])
        discovery=store.jobs.next_job(store.clock(),feeds=[feeds[0]])
        assert discovery is not None
        discovered(store,[publication_packet(Publication('prototype','https://example.org/prototype',1),
            Material('Prototype','',captions='not_requested',description='Synthetic video'),needs_fetch=True)],'{}',discovery,feeds[0])
        job=store.jobs.next_job(store.clock(),feeds=[feeds[0]])
        assert job is not None and job.kind=='fetch'
        save_material(store,job,Material('Prototype','Synthetic saved text.',captions='available'))
        prototype=store.db.execute('SELECT content FROM publications WHERE id=%s',(job.item,)).fetchone()[0]
        rows=[]
        for index,feed in enumerate(feeds):
            for number in range(per_feed):
                identity=str(uuid4())
                content=json.loads(json.dumps(prototype))
                content['blocks'][0]['text']='Publication '+identity+'\n'+('Synthetic saved text. '*100)
                url=f'https://example.org/{index}/{number}'
                rows.append((identity,url,url,feed,number,number,'Publication '+identity,json.dumps(content)))
        with transaction(store.db):
            store.db.execute('DELETE FROM jobs WHERE item=%s',(job.item,))
            store.db.execute('DELETE FROM publications WHERE id=%s',(job.item,))
            store.db.executemany("""INSERT INTO publications(id,url,platform,external_id,feed_id,published_at,discovered_at,title,content,status)
                VALUES (%s,%s,'other_web',%s,%s,%s,%s,%s,%s::jsonb,'ready')""",rows)
        store.configure_processor(ProcessorConfig(endpoint='http://localhost:9931',model='local:latest',local_execution=True))
        store.subscriptions.update_settings('oko','global',Overrides(theses=False))
        store.settings_changed()
        with transaction(store.db):
            for row in rows:
                store.publications.reconcile(row[0])
        store.processing.sync()
        store.set_work_enabled(False)
        count=store.db.execute("SELECT count(*) FROM publications WHERE content <> '{}'::jsonb").fetchone()[0]
        assert count==per_feed*4
    finally:
        store.db.close()
    return dict(materials=count,feeds=feeds,active=False)


def seed(root:Path, per_feed:int, active:bool=False) -> dict[str,object]:
    result = templates().copy(root, ('saved', per_feed), lambda source: saved_library(source, per_feed))
    if active:
        store = Store(root, lambda _:None)
        try:
            store.subscriptions.update_settings('oko','global',Overrides(theses=True))
            store.settings_changed()
            store.processing.sync()
            store.set_work_enabled(True)
        finally:
            store.db.close()
    return dict(result, active=active)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--per-feed',type=int,choices=[26,100],required=True)
    parser.add_argument('--active',action='store_true')
    args=parser.parse_args()
    print(json.dumps(seed(args.root,args.per_feed,args.active)))
