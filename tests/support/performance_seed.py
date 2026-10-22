"""Deterministic temporary libraries for HTTP/browser performance acceptance."""
from pathlib import Path
import argparse
import json

from oko.collection.contracts import Material
from oko.processing.llamacpp import ProcessorConfig
from oko.storage.store import Store
from oko.subscriptions.subscriptions import Overrides
from tests.support.reading_cost import build_library
from tests.support.library_templates import templates


def saved_library(root:Path, per_feed:int) -> dict[str,object]:
    feeds,_=build_library(root,[per_feed]*4)
    store=Store(root,lambda _:None)
    try:
        store.subscriptions.update_settings('oko','global',Overrides(theses=False,prompt_filters='',processor='llamacpp'))
        while job:=store.next_job(store.clock(),feeds=feeds):
            assert job.kind=='fetch',job
            store.save(job,Material('Publication '+job.item,'Publication '+job.item+'\n'+('Synthetic saved text. '*100),captions='available'))
        store.configure_processor(ProcessorConfig(endpoint='http://localhost:9931',model='local:latest',local_execution=True))
        store.subscriptions.update_settings('oko','global',Overrides(theses=False))
        store.settings_changed()
        store.processing.sync()
        store.set_work_enabled(False)
        count=store.db.execute('SELECT count(*) FROM items WHERE complete=1 AND viewed=0').fetchone()[0]
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
