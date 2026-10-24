"""Offline copy and fail-closed group guard, using synthetic stores only."""
import fcntl
import json
import pytest
from oko.storage.store import Store
from oko.storage.migration import prepare,MigrationRefused,group_guard,file_hash
from tests.support.worker_library import worker_library,workers,pass_worker,saved
import asyncio


def test_offline_copy_keeps_settings_results_files_ids_and_source_bytes(tmp_path):
    source=tmp_path/'source'
    store,item,execution,tasks=worker_library(source)
    asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
    before=saved(store,item)
    store.db.close()
    (source/'instance.lock').touch()
    (source/'synthetic-config.json').write_text('{"test": true}')
    original=file_hash(source/'state.sqlite')
    target=tmp_path/'target'
    report=prepare(source,target)
    assert report['verified'] and not report['switched'] and report['source_unchanged']
    assert report['ids']['items'][item]==item
    assert file_hash(source/'state.sqlite')==original
    assert (target/'synthetic-config.json').read_bytes()==(source/'synthetic-config.json').read_bytes()
    copied=Store(target,lambda _:None)
    assert saved(copied,item)['filtered_content']==before['filtered_content']
    assert copied.subscriptions.effective('feed','demo')['effective']['prompt_filters']['value']=='Keep useful text'
    copied.db.close()
    assert json.loads((target/'migration-report.json').read_text())['conflicts']==[]


@pytest.mark.parametrize('kind',[None,'group','supergroup','forum'])
def test_group_or_unknown_type_blocks_even_empty_telegram_feed(tmp_path,kind):
    source=tmp_path/'source'
    store=Store(source,lambda _:None)
    store.subscriptions.ensure_demo()
    with store.db:
        store.db.execute("UPDATE feeds SET kind='telegram',config=? WHERE id='demo'",(json.dumps({} if kind is None else dict(source_kind=kind)),))
    store.db.close()
    (source/'instance.lock').touch()
    original=file_hash(source/'state.sqlite')
    with pytest.raises(MigrationRefused,match='group_or_unknown_data'):prepare(source,tmp_path/'target')
    assert not (tmp_path/'target').exists() and file_hash(source/'state.sqlite')==original


def test_web_configuration_keeps_proven_channel_type(tmp_path):
    store=Store(tmp_path,lambda _:None)
    store.subscriptions.ensure_demo()
    with store.db:
        store.db.execute("UPDATE feeds SET kind='telegram',config='{\"source_kind\":\"channel\"}' WHERE id='demo'")
        store.db.execute("INSERT INTO telegram_bindings VALUES ('demo',?)",(json.dumps(dict(kind='web',username='fixture')),))
        store.publications.configure_feed('demo')
    assert group_guard(store.db)['telegram_types']=='channels_only'
    assert json.loads(store.db.execute("SELECT config FROM feeds WHERE id='demo'").fetchone()[0])['source_kind']=='channel'
    store.db.close()


def test_running_owner_and_existing_destination_are_never_modified(tmp_path):
    source=tmp_path/'source'
    store=Store(source,lambda _:None)
    store.db.close()
    lock=source/'instance.lock'
    lock.touch()
    with lock.open('rb') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(MigrationRefused,match='still running'):prepare(source,tmp_path/'target')
    target=tmp_path/'existing'
    target.mkdir()
    (target/'keep').write_text('untouched')
    with pytest.raises(MigrationRefused,match='new separate'):prepare(source,target)
    assert (target/'keep').read_text()=='untouched'


def test_client_profile_durable_fields_survive_expected_startup_reset(tmp_path):
    source=tmp_path/'source'
    store=Store(source,lambda _:None)
    with store.db:
        store.db.execute("INSERT INTO telegram_profiles VALUES ('personal','synthetic-account',123,7,'verified',1234,NULL)")
    store.db.close()
    (source/'instance.lock').touch()
    report=prepare(source,tmp_path/'copy')
    assert report['settings']['verified']
    copied=Store(tmp_path/'copy',lambda _:None)
    row=copied.db.execute("SELECT role,account,user_id,generation,state,checked FROM telegram_profiles").fetchone()
    assert tuple(row)==('personal','synthetic-account',123,7,'unavailable',1234)
    copied.db.close()
