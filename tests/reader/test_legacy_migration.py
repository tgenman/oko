"""Legacy channel evidence and retained terminal history use synthetic storage."""
import json
import sqlite3
import pytest
from oko.storage.store import Store
from oko.storage.migration import prepare,group_guard,MigrationRefused,file_hash
from oko.storage.migration_compatibility import certificates,rows
from tests.support.storage import database

ITEM='a'*32
MEDIA='11111111-1111-4111-8111-111111111111'


def source(root):
    store=Store(root,lambda _:None)
    store.subscriptions.ensure_demo()
    binding=dict(kind='web',username='fixture',preview_url='https://t.me/s/fixture')
    with store.db:
        store.db.execute("UPDATE feeds SET kind='telegram',platform='telegram',adapter='telegram_web_adapter',telegram_username='fixture',url='https://t.me/s/fixture',config=? WHERE id='demo'",(json.dumps(binding),))
        store.db.execute('INSERT INTO telegram_bindings VALUES (?,?)',('demo',json.dumps(binding)))
        store.db.execute("INSERT INTO telegram_source_claims VALUES ('username','fixture')")
        store.db.execute("INSERT INTO telegram_claim_owners VALUES ('username','fixture','demo','public preview')")
        store.db.execute("INSERT INTO jobs(kind,item,status) VALUES ('delete',?,'done')",(ITEM,))
        store.db.execute("INSERT INTO jobs(kind,item,status) VALUES ('fetch',?,'cancelled')",(ITEM,))
        store.db.execute("INSERT INTO jobs(kind,item,status) VALUES ('download_media',?,'done')",(MEDIA,))
        store.db.execute("INSERT INTO history_members VALUES ('demo',?,'viewed')",(ITEM,))
    store.db.close()
    (root/'instance.lock').touch()
    return root


def test_legacy_copy_restores_proven_type_without_removing_any_rows(tmp_path):
    original=source(tmp_path/'source')
    digest=file_hash(original/'state.sqlite')
    with database(original) as db:
        before={name:rows(db,'SELECT * FROM '+name) for name in ('jobs','history_members','telegram_bindings')}
    with pytest.raises(MigrationRefused):prepare(original,tmp_path/'strict')
    report=prepare(original,tmp_path/'copy',legacy=True)
    assert file_hash(original/'state.sqlite')==digest
    assert report['compatibility']['retained']==dict(jobs=3,history_members=1)
    assert len(report['compatibility']['restored_types'])==1
    copied=Store(tmp_path/'copy',lambda _:None)
    assert json.loads(copied.db.execute("SELECT config FROM feeds WHERE id='demo'").fetchone()[0])['source_kind']=='channel'
    for table,values in before.items():
        current=rows(copied.db,'SELECT * FROM '+table)
        assert len(current)==len(values)
        assert all(all(row[k]==old[k] for k in old) for row,old in zip(current,values))
    assert len(certificates(copied.db))==4
    assert group_guard(copied.db)['group_data']=='absent'
    copied.db.close()
    # Audit survives a second normal preparation; no compatibility switch needed.
    assert prepare(tmp_path/'copy',tmp_path/'again')['verified']


@pytest.mark.parametrize('bad',['no_claim','wrong_owner','wrong_username','wrong_preview','group','client','config_changed'])
def test_missing_or_contradictory_channel_evidence_refuses_without_touching_source(tmp_path,bad):
    original=source(tmp_path/'source')
    with database(original) as db:
        if bad=='no_claim':db.execute('DELETE FROM telegram_claim_owners')
        elif bad=='wrong_owner':db.execute("UPDATE telegram_claim_owners SET feed='elsewhere'")
        elif bad=='wrong_username':db.execute("UPDATE feeds SET telegram_username='another'")
        elif bad=='wrong_preview':db.execute("UPDATE feeds SET url='https://t.me/s/another'")
        else:
            config=json.loads(db.execute('SELECT config FROM feeds').fetchone()[0])
            if bad=='group':config['source_kind']='group'
            elif bad=='client':config['kind']='client'
            else:config['unknown']='extra'
            db.execute('UPDATE feeds SET config=?',(json.dumps(config),))
    digest=file_hash(original/'state.sqlite')
    with pytest.raises(MigrationRefused):prepare(original,tmp_path/'copy',legacy=True)
    assert file_hash(original/'state.sqlite')==digest and not (tmp_path/'copy').exists()


@pytest.mark.parametrize('status',['pending','running','failed','unavailable'])
def test_nonterminal_or_unresolved_job_never_becomes_retired(tmp_path,status):
    original=source(tmp_path/'source')
    with database(original) as db:db.execute('UPDATE jobs SET status=? WHERE kind=\'delete\'',(status,))
    with pytest.raises(MigrationRefused,match='unproven_job_owner'):prepare(original,tmp_path/'copy',legacy=True)
    assert not (tmp_path/'copy').exists()


@pytest.mark.parametrize('change',["UPDATE jobs SET status='running' WHERE kind='delete'", "UPDATE migration_retired_rows SET sha256='bad'"])
def test_retained_audit_cannot_mask_changed_rows(tmp_path,change):
    original=source(tmp_path/'source')
    prepare(original,tmp_path/'copy',legacy=True)
    with database(tmp_path/'copy') as db:db.execute(change)
    with pytest.raises(MigrationRefused,match='[Rr]etained|group_or_unknown_data'):prepare(tmp_path/'copy',tmp_path/'again')
    assert not (tmp_path/'again').exists()


def test_explicit_group_job_and_pending_history_are_never_certified(tmp_path):
    original=source(tmp_path/'source')
    with database(original) as db:
        db.execute("INSERT INTO jobs(kind,item,status) VALUES ('group-fetch',?,'done')",('b'*32,))
        db.execute("UPDATE history_members SET outcome='pending'")
    with pytest.raises(MigrationRefused,match='group_or_unknown_data'):prepare(original,tmp_path/'copy',legacy=True)


def test_runtime_removal_keeps_audit_without_poisoning_next_migration(tmp_path):
    original=source(tmp_path/'source')
    prepare(original,tmp_path/'copy',legacy=True)
    with database(tmp_path/'copy') as db:
        db.execute('DELETE FROM history_members')
        db.execute('DELETE FROM jobs')
        assert db.execute('SELECT count(*) FROM migration_retired_rows').fetchone()[0]==4
    assert prepare(tmp_path/'copy',tmp_path/'again')['verified']


def test_audit_does_not_poison_a_recreated_valid_target(tmp_path):
    original=source(tmp_path/'source')
    prepare(original,tmp_path/'copy',legacy=True)
    with database(tmp_path/'copy') as db:
        db.execute("INSERT INTO items(id,platform,external_id,url,feed_id) VALUES (?,'telegram','https://t.me/fixture/1','https://t.me/fixture/1','demo')",(ITEM,))
        db.execute("UPDATE jobs SET status='pending',generation=generation+1 WHERE kind='fetch'")
        assert group_guard(db)['group_data']=='absent'
