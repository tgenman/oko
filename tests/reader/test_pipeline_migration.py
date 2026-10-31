"""Runtime invariants retained after removing the obsolete file migration tools."""
import asyncio
import json
import httpx
from tests.support.storage import Store, database
from tests.support.worker_library import pass_worker, saved, worker_library, workers
from tests.support.app import create_app
from tests.support.filters import transport
from tests.support.presence import presence
from tests.support.reader import wait_for
from tests.support.source import Image, SyntheticMaterial
from tests.support.v3 import TestClient
from oko.collection.contracts import DiscoveryResult, FetchResult, Publication


def test_postgres_web_configuration_keeps_proven_channel_type(tmp_path):
    store=Store(tmp_path,lambda _:None)
    try:
        store.subscriptions.ensure_demo()
        with store.db:
            store.db.execute("UPDATE feeds SET kind='telegram',config=%s WHERE id='demo'",(json.dumps(dict(source_kind='channel')),))
            store.db.execute("INSERT INTO telegram_bindings VALUES ('demo',%s)",(json.dumps(dict(kind='web',username='fixture')),))
            store.publications.configure_feed('demo')
        assert store.db.execute("SELECT config FROM feeds WHERE id='demo'").fetchone()[0]['source_kind']=='channel'
    finally:
        store.db.close()

def test_postgres_client_profile_durable_fields_survive_startup(tmp_path):
    store=Store(tmp_path,lambda _:None)
    with store.db:
        store.db.execute("INSERT INTO telegram_profiles VALUES ('personal','synthetic-account',123,7,'verified',1234,NULL)")
    store.db.close()
    reopened=Store(tmp_path,lambda _:None)
    try:
        row=reopened.db.execute("SELECT role,account,user_id,generation,state,checked FROM telegram_profiles").fetchone()
        assert tuple(row)==('personal','synthetic-account',123,7,'unavailable',1234)
    finally:
        reopened.db.close()
