"""Public deployment CLI with synthetic operating-system executables."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
OLD = 'a' * 40
NEW = 'b' * 40


def release(sha, schema=3):
    return dict(version=1, sha=sha, platform='linux/amd64',
                image='ghcr.io/tgenman/oko-runtime@sha256:' + sha[0] * 64,
                helper=dict(release=sha, protocol=1),
                codex=dict(version='0.164.0', target='x86_64-unknown-linux-musl',
                           archive_url='https://github.com/openai/codex/releases/download/example/archive.tar.gz',
                           archive_sha256='c' * 64, binary_sha256='d' * 64, code_mode_host_sha256='e' * 64),
                schema=dict(minimum=schema, maximum=schema))


def invoke(root, *args, env=None, data=None):
    return subprocess.run([sys.executable, str(ROOT / 'deployment/deploy.py'), '--root', str(root), *args],
                          input=data, capture_output=True, text=True, env=env, timeout=30)


def test_invalid_release_is_rejected_before_any_operating_system_change(tmp_path):
    target = tmp_path / 'manifest.json'
    value = release(NEW)
    value['image'] = 'ghcr.io/tgenman/oko-runtime:latest'
    target.write_text(json.dumps(value))
    result = invoke(tmp_path, 'deploy', '--manifest', str(target))
    assert result.returncode != 0
    assert 'Invalid release manifest' in result.stderr
    assert not (tmp_path / 'release.json').exists()


def test_ssh_receiver_rejects_unbounded_or_shell_input(tmp_path):
    for payload in ('x' * 65537, json.dumps({'action': 'sh', 'command': 'echo unsafe'})):
        result = invoke(tmp_path, 'receive', data=payload)
        assert result.returncode != 0
        assert not (tmp_path / 'release.json').exists()
        assert 'Traceback' not in result.stderr


@pytest.fixture
def machine(tmp_path):
    root = tmp_path / 'oko'
    root.mkdir()
    state = dict(schema=3, reader=True, helper=True, selected=OLD)
    (root / 'synthetic-state.json').write_text(json.dumps(state))
    (root / 'release.json').write_text(json.dumps(release(OLD)))
    (root / 'system').mkdir()
    (root / 'system/helper.json').write_text(json.dumps(dict(release=OLD, token_file='/var/lib/oko-codex/bridge-token',
                                                           scratch_root='/run/oko-codex/jobs', socket_path='/srv/oko/ipc/helper.sock')))
    (root / 'system/oko-codex.service').write_text('original helper unit')
    (root / 'data/media').mkdir(parents=True)
    (root / 'data/media/image.bin').write_bytes(b'synthetic-persistent-media')
    (root / 'secrets').mkdir()
    for role in ('reader', 'migrator'):
        path = root / 'secrets' / ('database-' + role)
        path.write_text('postgresql://' + role + ':private-' + role + '-canary@postgres:5432/oko?options=-csearch_path%3Doko,public')
        path.chmod(0o600)
    (root / 'backup').mkdir()
    for name, content in [('password', 'synthetic-backup-key'), ('credentials', 'AWS_ACCESS_KEY_ID=test\nAWS_SECRET_ACCESS_KEY=test\n')]:
        path = root / 'backup' / name
        path.write_text(content)
        path.chmod(0o600)
    (root / 'backup/config.json').write_text(json.dumps(dict(repository=str(tmp_path / 'repository'),
        password_file=str(root / 'backup/password'), credentials_file=str(root / 'backup/credentials'))))
    binary = tmp_path / 'bin'
    binary.mkdir()
    program = binary / 'synthetic'
    program.write_text('#!' + sys.executable + '''
import json,os,pathlib,shutil,sys
r=pathlib.Path(os.environ['SYNTH_ROOT']); name=pathlib.Path(sys.argv[0]).name; args=sys.argv[1:]
state=json.loads((r/'synthetic-state.json').read_text())
with (r/'commands.jsonl').open('a') as log:log.write(json.dumps([name]+args)+'\\n')
fail=os.environ.get('FAIL_STAGE','')
def save(): (r/'synthetic-state.json').write_text(json.dumps(state))
if name=='git':
 if 'rev-parse' in args:print(pathlib.Path(args[args.index('-C')+1]).name)
elif name=='docker':
 if 'pull' in args and fail=='pull':sys.exit(1)
 if 'inspect' in args:
  if '--format' in args:
   print(('true' if state['reader'] else 'false') if '{{.State.Running}}' in args else 'stable-'+args[-1])
  else:
   digest=args[-1].split(':')[-1];sha=digest[0]*40
   print(json.dumps([{'Architecture':'amd64','Os':'linux','Config':{'Labels':{'org.opencontainers.image.revision':sha}}}]))
 if 'psql' in args:print(9 if state.get('schema_name','oko')!='oko' else state['schema'])
 if 'run' in args and '-c' in args and 'current_schema()' in args[-1]:
  from urllib.parse import parse_qs,urlsplit
  selected=[]
  for role in ('reader','migrator'):
   dsn=(r/'secrets'/('database-'+role)).read_text().strip()
   option=parse_qs(urlsplit(dsn).query)['options'][0]
   selected.append(option.split('search_path=',1)[1].split(',')[0])
  if selected[0]!=selected[1]:sys.exit(1)
  print(json.dumps({'database':'oko','schema':selected[0],'version':state['schema']}))
 if 'pg_dump' in args:sys.stdout.buffer.write(b'PGDMP-synthetic')
 if 'compose' in args:
  is_app=any('compose.app.yaml' in a for a in args)
  if is_app:
   if 'ps' in args:print('reader' if state['reader'] else '')
   if 'stop' in args:state['reader']=False;save()
   if 'up' in args:
    state['reader']=True;state['selected']=os.environ['OKO_IMAGE'].split(':')[-1][0]*40;save()
    if fail=='start' and state['selected']=='b'*40:sys.exit(1)
   if 'exec' in args and fail=='health' and state['selected']=='b'*40:sys.exit(1)
 if 'run' in args and 'oko.storage.postgres_migrations' in args and '--check' not in args:
  if fail=='migration':sys.exit(1)
  state['schema']=int(os.environ.get('TARGET_SCHEMA',3));save()
elif name=='systemctl':
 if 'stop' in args:state['helper']=False;save()
 if 'start' in args:state['helper']=True;save()
 if 'is-active' in args:sys.exit(0 if state['helper'] else 3)
elif name=='cat':
 path=r/'system'/pathlib.Path(args[-1]).name;print(path.read_text(),end='')
elif name=='install':
 shutil.copyfile(args[-2],r/'system'/pathlib.Path(args[-1]).name)
elif name=='id':print(os.environ.get('HELPER_UID','20002') if '-u' in args else os.environ.get('HELPER_PRIMARY_GID','20001'))
elif name=='getent':print('oko-ipc:x:'+os.environ.get('IPC_GID','20001')+':okocodex')
elif name=='restic':
 repo=pathlib.Path(os.environ['RESTIC_REPOSITORY']);repo.mkdir(exist_ok=True)
 if args[0]=='backup':
  if fail=='backup':sys.exit(1)
  shutil.copytree(pathlib.Path.cwd()/'snapshot',repo/'snapshot',dirs_exist_ok=True)
  print(json.dumps({'message_type':'summary','snapshot_id':'c'*64}))
 elif args[0]=='tag':
  print(json.dumps({'message_type':'changed','old_snapshot_id':'c'*64,'new_snapshot_id':'d'*64}))
 elif args[0]=='restore':
  target=pathlib.Path(args[args.index('--target')+1]);shutil.copytree(repo/'snapshot',target/'snapshot')
''')
    program.chmod(0o755)
    for name in ('git', 'docker', 'systemctl', 'cat', 'install', 'id', 'getent', 'uv', 'runuser', 'python3', 'restic'):
        (binary / name).symlink_to(program)
    for sha in (OLD, NEW):
        source = root / 'releases' / sha
        (source / 'deployment/systemd').mkdir(parents=True)
        (source / 'deployment/systemd/oko-codex.service').write_text('Environment=OKO_CODEX_BINARY=/opt/oko/codex/0.164.0/bin/codex\n')
        (source / 'deployment/compose.app.yaml').write_text('name: oko-app\n')
        (source / 'deployment/compose.infra.yaml').write_text('name: oko-infra\n')
        (source / 'oko/processing').mkdir(parents=True)
        (source / 'oko/processing/codex_runtime.json').write_text(json.dumps(release(sha)['codex']))
        (source / '.venv/bin').mkdir(parents=True)
        (source / '.venv/bin/python').symlink_to(program)
    (root / 'operator').symlink_to(root / 'releases' / OLD / 'deployment')
    (root / 'codex').mkdir()
    (root / 'codex/current').symlink_to(root / 'releases' / OLD)
    target = tmp_path / 'manifest.json'
    target.write_text(json.dumps(release(NEW)))
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'], SYNTH_ROOT=str(root))
    return root, target, env


def test_checked_release_switches_both_components_and_preserves_infrastructure(machine):
    root, manifest, env = machine
    result = invoke(root, 'deploy', '--manifest', str(manifest), env=env)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['success'] and receipt['sha'] == NEW
    assert receipt['schema_version'] == 3
    commands = [json.loads(line) for line in (root / 'commands.jsonl').read_text().splitlines()]
    pull = next(i for i, command in enumerate(commands) if 'pull' in command)
    stop = next(i for i, command in enumerate(commands) if 'stop' in command)
    assert pull < stop
    assert not any('compose.infra.yaml' in str(command) and 'up' in command for command in commands)
    assert not any('compose' in command and 'monitoring' in str(command) for command in commands)
    assert json.loads((root / 'release.json').read_text())['sha'] == NEW
    assert (root / 'codex/current').resolve().name == NEW
    rollback = invoke(root, 'rollback', env=env)
    assert rollback.returncode == 0, rollback.stderr
    assert json.loads(rollback.stdout)['sha'] == OLD


@pytest.mark.parametrize('failure', ['pull', 'start', 'health'])
def test_failed_release_never_reports_success_or_restores_database(machine, failure):
    root, manifest, env = machine
    env['FAIL_STAGE'] = failure
    result = invoke(root, 'deploy', '--manifest', str(manifest), env=env)
    assert result.returncode != 0
    assert 'Traceback' not in result.stderr
    assert json.loads((root / 'release.json').read_text())['sha'] == OLD
    commands = (root / 'commands.jsonl').read_text()
    assert 'pg_restore' not in commands
    if failure == 'pull':
        assert '"stop"' not in commands
    else:
        result_files = list((root / 'deployment-history').glob('*/result.json'))
        assert len(result_files) == 1
        receipt = json.loads(result_files[0].read_text())
        assert receipt['success'] is False and receipt['rolled_back'] is True


def test_pre_migration_backup_is_verified_before_schema_change(machine):
    root, manifest, env = machine
    manifest.write_text(json.dumps(release(NEW, schema=4)))
    env['TARGET_SCHEMA'] = '4'
    result = invoke(root, 'deploy', '--manifest', str(manifest), env=env)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['schema_version'] == 4 and receipt['migrated'] is True
    assert receipt['backup']['last_attempt_result'] == 'success'
    assert receipt['backup']['snapshot_id'] == 'd'*64
    commands = [json.loads(line) for line in (root / 'commands.jsonl').read_text().splitlines()]
    verified = next(i for i, command in enumerate(commands) if command[:2] == ['restic', 'tag'])
    migration = next(i for i, command in enumerate(commands) if 'oko.storage.postgres_migrations' in command)
    assert verified < migration
    assert not any(command[:2] == ['restic', 'forget'] for command in commands)
    rollback = invoke(root, 'rollback', env=env)
    assert rollback.returncode != 0
    assert 'incompatible database schema' in rollback.stderr
    assert json.loads((root / 'release.json').read_text())['sha'] == NEW


@pytest.mark.parametrize('failure', ['backup', 'migration'])
def test_failed_pre_migration_stage_preserves_prior_reader_and_data(machine, failure):
    root, manifest, env = machine
    manifest.write_text(json.dumps(release(NEW, schema=4)))
    env.update(TARGET_SCHEMA='4', FAIL_STAGE=failure)
    result = invoke(root, 'deploy', '--manifest', str(manifest), env=env)
    assert result.returncode != 0
    assert json.loads((root / 'release.json').read_text())['sha'] == OLD
    assert (root / 'data/media/image.bin').read_bytes() == b'synthetic-persistent-media'
    assert 'pg_restore' not in (root / 'commands.jsonl').read_text()
    assert json.loads(next((root / 'deployment-history').glob('*/result.json')).read_text())['rolled_back']


def test_health_failure_after_incompatible_migration_stops_without_old_image_or_restore(machine):
    root, manifest, env = machine
    manifest.write_text(json.dumps(release(NEW, schema=4)))
    env.update(TARGET_SCHEMA='4', FAIL_STAGE='health')
    result = invoke(root, 'deploy', '--manifest', str(manifest), env=env)
    assert result.returncode != 0
    receipt = json.loads(next((root / 'deployment-history').glob('*/result.json')).read_text())
    assert receipt['recovery_required'] and receipt['rolled_back'] is False
    state = json.loads((root / 'synthetic-state.json').read_text())
    assert state['schema'] == 4 and state['reader'] is False and state['helper'] is False
    assert 'pg_restore' not in (root / 'commands.jsonl').read_text()


def test_maintenance_contention_never_stops_the_reader(machine):
    import fcntl
    root, manifest, env = machine
    with (root / 'maintenance.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = invoke(root, 'deploy', '--manifest', str(manifest), env=env)
    assert result.returncode != 0
    assert 'Another maintenance operation' in result.stderr
    assert '"stop"' not in (root / 'commands.jsonl').read_text()


def test_ssh_authorization_refuses_a_command_instead_of_a_public_key(tmp_path):
    public_key = tmp_path / 'public-key'
    public_key.write_text('command="sh" ssh-ed25519 AAAAinvalid\n')
    result = invoke(tmp_path, 'authorize', '--public-key-file', str(public_key))
    assert result.returncode != 0
    assert 'dedicated Ed25519 public key' in result.stderr


def test_deploy_and_rollback_use_the_active_private_database_schema(machine):
    root, manifest, env = machine
    state = json.loads((root / 'synthetic-state.json').read_text())
    state['schema_name'] = 'oko_import146'
    (root / 'synthetic-state.json').write_text(json.dumps(state))
    for role in ('reader', 'migrator'):
        path = root / 'secrets' / ('database-' + role)
        path.write_text(path.read_text().replace('search_path%3Doko,public', 'search_path%3Doko_import146,public'))
    result = invoke(root, 'deploy', '--manifest', str(manifest), env=env)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['database_schema'] == 'oko_import146' and receipt['schema_version'] == 3
    rollback = invoke(root, 'rollback', env=env)
    assert rollback.returncode == 0, rollback.stderr
    assert json.loads(rollback.stdout)['database_schema'] == 'oko_import146'
    commands = (root / 'commands.jsonl').read_text()
    for role in ('reader', 'migrator'):
        assert 'private-' + role + '-canary' not in result.stdout + result.stderr + rollback.stdout + rollback.stderr + commands


def test_different_reader_and_migration_schemas_block_deploy_before_stop(machine):
    root, manifest, env = machine
    path = root / 'secrets/database-migrator'
    path.write_text(path.read_text().replace('search_path%3Doko,public', 'search_path%3Dwrong,public'))
    result = invoke(root, 'deploy', '--manifest', str(manifest), env=env)
    assert result.returncode != 0
    commands = (root / 'commands.jsonl').read_text()
    assert '"stop"' not in commands
    assert json.loads((root / 'release.json').read_text())['sha'] == OLD
    assert 'private-migrator-canary' not in result.stdout + result.stderr + commands


def test_helper_service_uses_named_ipc_group_independent_of_primary_gid(machine):
    root, manifest, env = machine
    result = invoke(root, 'deploy', '--manifest', str(manifest), env={**env, 'HELPER_PRIMARY_GID': '988'})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['success'] is True


@pytest.mark.parametrize('identity', [{'HELPER_UID':'20003'}, {'IPC_GID':'20002'}])
def test_invalid_helper_identity_is_rejected_before_service_stop(machine, identity):
    root, manifest, env = machine
    result = invoke(root, 'deploy', '--manifest', str(manifest), env={**env, **identity})
    assert result.returncode != 0
    assert 'Unexpected helper account identity' in result.stderr
    assert '"stop"' not in (root / 'commands.jsonl').read_text()
