"""The installed backup command preserves an offline library and successful history."""
import json
import os
from pathlib import Path
import subprocess
import pytest
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_check_rejects_public_credentials_without_disclosing_contents(tmp_path):
    backup = tmp_path / 'backup'
    backup.mkdir()
    password = tmp_path / 'password'
    password.write_text('private-backup-canary')
    password.chmod(0o644)
    credentials = tmp_path / 'credentials'
    credentials.write_text('AWS_ACCESS_KEY_ID=synthetic\nAWS_SECRET_ACCESS_KEY=private-s3-canary\n')
    credentials.chmod(0o600)
    (backup / 'config.json').write_text(json.dumps(dict(repository='s3:https://example.invalid/bucket/oko', password_file=str(password), credentials_file=str(credentials))))
    result = subprocess.run([sys.executable, str(ROOT / 'deployment/backup.py'), '--root', str(tmp_path), 'check'], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'private regular file' in result.stderr
    assert 'private-backup-canary' not in result.stdout + result.stderr
    assert 'private-s3-canary' not in result.stdout + result.stderr


def installation(tmp_path):
    root = tmp_path / 'installation'
    (root / 'backup').mkdir(parents=True)
    (root / 'data/media').mkdir(parents=True)
    (root / 'data/media/image.bin').write_bytes(b'synthetic-media')
    (root / 'secrets').mkdir()
    (root / 'secrets/database-reader').write_text('synthetic-private-dsn')
    (root / 'secrets/docker').mkdir()
    (root / 'secrets/docker/config.json').write_text('registry-token-do-not-copy')
    (root / 'release.json').write_text(json.dumps(dict(sha='a' * 40, image='synthetic-image')))
    (root / 'operator').mkdir()
    (root / 'operator/compose.app.yaml').write_text('synthetic-compose')
    for name, value in [('password', 'private-backup-canary'), ('credentials', 'AWS_ACCESS_KEY_ID=synthetic\nAWS_SECRET_ACCESS_KEY=private-s3-canary\n')]:
        path = root / name
        path.write_text(value)
        path.chmod(0o600)
    (root / 'backup/config.json').write_text(json.dumps(dict(repository=str(tmp_path / 'repository'), password_file=str(root / 'password'), credentials_file=str(root / 'credentials'))))
    tools = tmp_path / 'bin'
    tools.mkdir()
    source = tools / 'external.py'
    source.write_text('''#!/usr/bin/env python3
import json,os,pathlib,shutil,sys,time
root=pathlib.Path(os.environ['SYNTHETIC_ROOT']); args=sys.argv[1:]; name=pathlib.Path(sys.argv[0]).name
state=root/'running'; running=not state.exists() or state.read_text()=='yes'
with (root/'events').open('a') as f:f.write(name+' '+ ' '.join(args)+'\\n')
if name=='docker':
 if args[0]=='inspect':
  if '--format' in args:print('true' if running else 'false')
  else:print(json.dumps([{'Config':{'Labels':{'org.oko.restore.root':os.environ.get('RESTORE_TARGET','')}},'State':{'Running':True}}]))
 elif 'psql' in args:
  if '-Atc' in args:print('0')
  else:(root/'restore-work-disabled.sql').write_bytes(sys.stdin.buffer.read())
 elif 'stop' in args:state.write_text('no')
 elif 'up' in args:state.write_text('yes')
 elif 'pg_dump' in args:
  if os.environ.get('PAUSE_DUMP'):
   (root/'paused').write_text('yes');time.sleep(60)
  sys.stdout.buffer.write(b'PGDMP-synthetic')
elif name=='systemctl':
 if args[0]=='start' and os.environ.get('FAIL_HELPER_START'):sys.exit(1)
 if args[0]=='is-active':sys.exit(0 if running else 3)
elif name=='restic':
 (root/'observed-cache').write_text(os.environ.get('RESTIC_CACHE_DIR',''))
 repo=pathlib.Path(os.environ['RESTIC_REPOSITORY']);repo.mkdir(exist_ok=True)
 if args[0]=='backup':
  if os.environ.get('FAIL_UPLOAD'):sys.exit(2)
  shutil.copytree(pathlib.Path.cwd()/'snapshot',repo/'snapshot',dirs_exist_ok=True)
  print(json.dumps({'message_type':'summary','snapshot_id':'b'*64}))
 elif args[0]=='restore':
  if os.environ.get('FAIL_RESTORE'):sys.exit(1)
  if args[1]=='b'*64 and (repo/'retagged').exists():sys.exit(10)
  target=pathlib.Path(args[args.index('--target')+1]);shutil.copytree(repo/'snapshot',target/'snapshot')
  if os.environ.get('CORRUPT_RESTORE'):(target/'snapshot/data/media/image.bin').write_bytes(b'corrupt')
 elif args[0]=='tag':
  (repo/'retagged').write_text('yes')
  print(json.dumps({'message_type':'changed','old_snapshot_id':'b'*64,'new_snapshot_id':'c'*64}))
  print(json.dumps({'message_type':'summary','changed_snapshots':1}))
 elif args[0]=='check' and os.environ.get('FAIL_CHECK'):sys.exit(1)
''')
    source.chmod(0o755)
    for name in ('docker', 'systemctl', 'restic'):
        (tools / name).symlink_to(source)
    environment = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'], SYNTHETIC_ROOT=str(root))
    return root, environment


def run_backup(root, environment, *args):
    return subprocess.run([sys.executable, str(ROOT / 'deployment/backup.py'), '--root', str(root), *args], capture_output=True, text=True, env=environment)


def test_backup_resumes_before_upload_verifies_files_and_excludes_registry(tmp_path):
    root, env = installation(tmp_path)
    result = run_backup(root, env, 'backup', '--kind', 'nightly')
    assert result.returncode == 0, result.stderr
    status = json.loads((root / 'backup/status.json').read_text())
    assert status['last_attempt_result'] == 'success'
    assert status['last_success_timestamp_seconds'] > 0
    assert status['last_success_size_bytes'] > 0
    events = (root / 'events').read_text().splitlines()
    assert next(i for i, line in enumerate(events) if ' up ' in line) < next(i for i, line in enumerate(events) if line.startswith('restic backup'))
    saved = tmp_path / 'repository/snapshot'
    assert (saved / 'data/media/image.bin').read_bytes() == b'synthetic-media'
    assert not (saved / 'secrets/docker').exists()
    assert not (saved / 'password').exists()
    assert (saved / 'checksums.json').is_file()
    assert 'verified' in next(line for line in events if line.startswith('restic tag'))
    assert 'restic forget' in '\n'.join(events)


def test_failed_upload_preserves_last_success_and_never_prunes(tmp_path):
    root, env = installation(tmp_path)
    (root / 'backup/status.json').write_text(json.dumps(dict(last_success_timestamp_seconds=123, last_success_size_bytes=456)))
    result = run_backup(root, dict(env, FAIL_UPLOAD='1'), 'backup')
    assert result.returncode != 0
    state = json.loads((root / 'backup/status.json').read_text())
    assert state['last_attempt_result'] == 'error'
    assert state['last_success_timestamp_seconds'] == 123
    assert state['last_success_size_bytes'] == 456
    assert 'restic forget' not in (root / 'events').read_text()
    assert (root / 'running').read_text() == 'yes'


def test_restore_refuses_existing_target_before_remote_access(tmp_path):
    root, env = installation(tmp_path)
    target = tmp_path / 'recovery'
    target.mkdir()
    (target / 'valuable.txt').write_text('keep me')
    result = run_backup(root, env, 'restore', '--snapshot', 'b' * 64, '--target', str(target), '--postgres-container', 'synthetic-recovery')
    assert result.returncode != 0
    assert 'empty separate target' in result.stderr
    assert (target / 'valuable.txt').read_text() == 'keep me'
    assert not (root / 'events').exists()


@pytest.mark.parametrize('layout', ['legacy', 'state-text', 'state-jsonb'])
def test_restore_recovers_verified_snapshot_into_dedicated_target_with_work_disabled(tmp_path, postgres_url, layout):
    root, env = installation(tmp_path)
    assert run_backup(root, env, 'backup').returncode == 0
    target = tmp_path / 'recovered'
    result = run_backup(root, dict(env, RESTORE_TARGET=str(target)), 'restore', '--snapshot', json.loads((root / 'backup/status.json').read_text())['snapshot_id'],
                        '--target', str(target), '--postgres-container', 'synthetic-recovery')
    assert result.returncode == 0, result.stderr
    assert (target / 'data/media/image.bin').read_bytes() == b'synthetic-media'
    assert json.loads((target / 'recovery.json').read_text())['work_enabled'] is False
    assert 'work-enabled' in (root / 'restore-work-disabled.sql').read_text()
    assert "'false'" in (root / 'restore-work-disabled.sql').read_text()
    assert (root / 'data/media/image.bin').read_bytes() == b'synthetic-media'
    # Restore operates on a dedicated database, not merely an isolated schema:
    # the emitted SQL intentionally updates every restored library in that DB.
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from uuid import uuid4
    name = 'test_restore_' + uuid4().hex
    with psycopg.connect(postgres_url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            dsn = make_conninfo(postgres_url, dbname=name, options='')
            with psycopg.connect(dsn, autocommit=True) as restored:
                value_type = 'jsonb' if layout == 'state-jsonb' else 'text'
                restored.execute(sql.SQL('CREATE TABLE state(key text PRIMARY KEY,value {})').format(sql.SQL(value_type)))
                restored.execute('CREATE TABLE reader_entities(kind text,identity text,version bigint)')
                restored.execute("INSERT INTO state VALUES ('work-enabled','true')")
                if layout == 'legacy':
                    restored.execute('CREATE TABLE reader_identity(id integer PRIMARY KEY,value text)')
                    restored.execute("INSERT INTO reader_identity VALUES (1,'previous-timeline')")
                else:
                    restored.execute("INSERT INTO state VALUES ('storage-id',%s)", ('"previous-timeline"',))
                restored.execute("INSERT INTO reader_entities VALUES ('card','saved',42)")
                restored.execute((root / 'restore-work-disabled.sql').read_text())
                if layout == 'legacy':
                    identity = restored.execute('SELECT value FROM reader_identity').fetchone()[0]
                else:
                    identity = restored.execute("SELECT value::jsonb #>> '{}' FROM state WHERE key='storage-id'").fetchone()[0]
                assert identity != 'previous-timeline'
                assert restored.execute('SELECT version FROM reader_entities').fetchone()[0] == 42
                assert restored.execute("SELECT value::text FROM state WHERE key='work-enabled'").fetchone()[0] == 'false'
        finally:
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def test_corrupt_download_is_not_successful_or_pruned(tmp_path):
    root, env = installation(tmp_path)
    result = run_backup(root, dict(env, CORRUPT_RESTORE='1'), 'backup')
    assert result.returncode != 0
    state = json.loads((root / 'backup/status.json').read_text())
    assert state['last_success_timestamp_seconds'] == 0
    assert state['last_attempt_result'] == 'error'
    assert 'restic forget' not in (root / 'events').read_text()
    assert 'restic tag' not in (root / 'events').read_text()


def test_pre_migration_preserves_stopped_services_and_does_not_prune(tmp_path):
    root, env = installation(tmp_path)
    (root / 'running').write_text('no')
    result = run_backup(root, env, 'backup', '--kind', 'pre-migration')
    assert result.returncode == 0, result.stderr
    assert (root / 'running').read_text() == 'no'
    assert 'restic forget' not in (root / 'events').read_text()


def test_wrong_key_download_keeps_restore_target_absent(tmp_path):
    root, env = installation(tmp_path)
    assert run_backup(root, env, 'backup').returncode == 0
    target = tmp_path / 'not-restored'
    result = run_backup(root, dict(env, RESTORE_TARGET=str(target), FAIL_RESTORE='1'), 'restore',
                        '--snapshot', 'b' * 64, '--target', str(target), '--postgres-container', 'synthetic-recovery')
    assert result.returncode != 0
    assert not target.exists()
    assert not (root / 'restore-work-disabled.sql').exists()


def test_common_maintenance_lock_blocks_backup_without_changing_heartbeat(tmp_path):
    import fcntl
    root, env = installation(tmp_path)
    with (root / 'maintenance.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = run_backup(root, env, 'backup')
    assert result.returncode != 0
    assert not (root / 'events').exists()
    assert not (root / 'backup/status.json').exists()


def test_backup_returns_the_retagged_snapshot_identity_for_restore(tmp_path):
    root, env = installation(tmp_path)
    result = run_backup(root, env, 'backup')
    assert result.returncode == 0, result.stderr
    identity = json.loads(result.stdout)['snapshot_id']
    assert identity == 'c' * 64
    assert json.loads((root / 'backup/status.json').read_text())['snapshot_id'] == identity
    target = tmp_path / 'retagged-recovery'
    restored = run_backup(root, dict(env, RESTORE_TARGET=str(target)), 'restore', '--snapshot', identity,
                          '--target', str(target), '--postgres-container', 'synthetic-recovery')
    assert restored.returncode == 0, restored.stderr
    assert (target / 'data/media/image.bin').read_bytes() == b'synthetic-media'


def test_check_uses_backup_cache_inside_systemd_writable_root(tmp_path):
    root, env = installation(tmp_path)
    result = run_backup(root, env, 'check')
    assert result.returncode == 0, result.stderr
    assert (root / 'observed-cache').read_text() == str(root / 'backup/cache')


def test_invalid_credentials_records_failed_attempt_and_preserves_success(tmp_path):
    root, env = installation(tmp_path)
    (root / 'backup/status.json').write_text(json.dumps(dict(last_success_timestamp_seconds=123, last_success_size_bytes=456, last_attempt_result='success')))
    (root / 'password').unlink()
    result = run_backup(root, env, 'backup')
    assert result.returncode != 0
    status = json.loads((root / 'backup/status.json').read_text())
    assert status['last_attempt_result'] == 'error'
    assert status['last_success_timestamp_seconds'] == 123
    assert status['last_success_size_bytes'] == 456
    assert status['last_attempt_timestamp_seconds'] > 123
    assert not (root / 'events').exists()


def test_recover_resumes_saved_reader_intent_without_starting_stopped_helper(tmp_path):
    root, env = installation(tmp_path)
    (root / 'running').write_text('no')
    receipt = root / 'backup/resume.json'
    receipt.write_text(json.dumps(dict(version=1, release='a' * 40, reader=True, helper=False)))
    result = run_backup(root, env, 'recover')
    assert result.returncode == 0, result.stderr
    assert (root / 'running').read_text() == 'yes'
    assert not receipt.exists()
    assert 'systemctl start' not in (root / 'events').read_text()


def test_recover_keeps_intentionally_stopped_installation_stopped(tmp_path):
    root, env = installation(tmp_path)
    (root / 'running').write_text('no')
    receipt = root / 'backup/resume.json'
    receipt.write_text(json.dumps(dict(version=1, release='a' * 40, reader=False, helper=False)))
    result = run_backup(root, env, 'recover')
    assert result.returncode == 0, result.stderr
    assert (root / 'running').read_text() == 'no'
    assert not receipt.exists()
    assert not (root / 'events').exists()


def test_backup_copies_required_codex_credentials_without_session_logs(tmp_path):
    root, env = installation(tmp_path)
    home = root / 'host-codex'
    (home / 'auth/sessions').mkdir(parents=True)
    (home / 'auth/auth.json').write_text('{"synthetic-login":true}')
    (home / 'auth/config.toml').write_text('synthetic=true')
    (home / 'auth/sessions/private.jsonl').write_text('private-session-do-not-copy')
    (home / 'auth/history.jsonl').write_text('private-history-do-not-copy')
    (home / 'auth/logs').mkdir()
    (home / 'auth/logs/codex.log').write_text('private-log-do-not-copy')
    (home / 'helper.json').write_text('{"synthetic":true}')
    (home / 'bridge-token').write_text('synthetic-bridge-token')
    result = run_backup(root, env, 'backup')
    assert result.returncode == 0, result.stderr
    saved = tmp_path / 'repository/snapshot/codex-host'
    assert (saved / 'auth/auth.json').read_text() == '{"synthetic-login":true}'
    assert (saved / 'auth/config.toml').read_text() == 'synthetic=true'
    assert (saved / 'helper.json').is_file()
    assert (saved / 'bridge-token').is_file()
    assert not (saved / 'auth/sessions').exists()
    assert not (saved / 'auth/history.jsonl').exists()
    assert not (saved / 'auth/logs').exists()


def test_helper_restart_failure_still_resumes_reader_and_retains_recovery_intent(tmp_path):
    root, env = installation(tmp_path)
    result = run_backup(root, dict(env, FAIL_HELPER_START='1'), 'backup')
    assert result.returncode != 0
    assert (root / 'running').read_text() == 'yes'
    assert (root / 'backup/resume.json').exists()
    assert json.loads((root / 'backup/status.json').read_text())['last_attempt_result'] == 'error'
    assert 'restic backup' not in (root / 'events').read_text()
    assert run_backup(root, env, 'recover').returncode == 0
    assert not (root / 'backup/resume.json').exists()


def test_sigkill_during_dump_can_recover_original_services_from_durable_receipt(tmp_path):
    import signal
    import time
    root, env = installation(tmp_path)
    process = subprocess.Popen([sys.executable, str(ROOT / 'deployment/backup.py'), '--root', str(root), 'backup'],
                               env=dict(env, PAUSE_DUMP='1'), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while not (root / 'paused').exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.02)
        assert (root / 'running').read_text() == 'no'
        assert (root / 'backup/resume.json').is_file()
    finally:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)
    result = run_backup(root, env, 'recover')
    assert result.returncode == 0, result.stderr
    assert (root / 'running').read_text() == 'yes'
    assert not (root / 'backup/resume.json').exists()


def test_backup_preserves_pending_refreshed_codex_login_without_private_logs(tmp_path):
    import hashlib
    from oko.processing.codex_linux import credentials
    root, env = installation(tmp_path)
    auth = root / 'host-codex/auth'
    pending = auth / ('.pending-' + 'a' * 32)
    (pending / 'home/sessions').mkdir(parents=True)
    auth.chmod(0o700)
    original = json.dumps({'auth_mode': 'chatgpt', 'tokens': {'refresh_token': 'synthetic-old'}}).encode()
    refreshed = json.dumps({'auth_mode': 'chatgpt', 'tokens': {'refresh_token': 'synthetic-refreshed'}}).encode()
    (auth / 'auth.json').write_bytes(original)
    (pending / 'recovery.json').write_text(json.dumps({'version': 1, 'base': hashlib.sha256(original).hexdigest()}))
    (pending / 'home/auth.json').write_bytes(refreshed)
    (pending / 'home/installation_id').write_text('synthetic-installation')
    (pending / 'home/models_cache.json').write_text('{"models":[]}')
    (pending / 'home/sessions/private.jsonl').write_text('never-back-up-session-content')
    (pending / 'home/history.jsonl').write_text('never-back-up-history')
    result = run_backup(root, env, 'backup')
    assert result.returncode == 0, result.stderr
    target = tmp_path / 'pending-login-recovery'
    identity = json.loads(result.stdout)['snapshot_id']
    restored = run_backup(root, dict(env, RESTORE_TARGET=str(target)), 'restore', '--snapshot', identity,
                          '--target', str(target), '--postgres-container', 'synthetic-recovery')
    assert restored.returncode == 0, restored.stderr
    restored_auth = target / 'codex-host/auth'
    restored_pending = restored_auth / pending.name
    assert (restored_pending / 'recovery.json').is_file()
    assert not (restored_pending / 'home/sessions').exists()
    assert not (restored_pending / 'home/history.jsonl').exists()
    with credentials(restored_auth) as view:
        assert json.loads((view / 'auth.json').read_text())['tokens']['refresh_token'] == 'synthetic-refreshed'
        assert (view / 'installation_id').read_text() == 'synthetic-installation'
        assert json.loads((view / 'models_cache.json').read_text()) == {'models': []}
    assert (auth / 'auth.json').read_bytes() == original
    assert (pending / 'home/auth.json').read_bytes() == refreshed
