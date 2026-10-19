"""Release commands use synthetic Git/data directories and fake external tools."""
import json
import os
import platform
import pytest
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/oko-release'


def invoke(repo, home, *args, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), '--repo', str(repo), '--home', str(home), *args],
                          text=True, capture_output=True, env=env)


def repository(tmp_path):
    repo = tmp_path / 'checkout'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'synthetic@example.invalid'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Synthetic'], check=True)
    (repo / 'source.txt').write_text('fixed release')
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'synthetic'], check=True)
    return repo


def test_prepare_refuses_dirty_checkout_before_build_or_home_creation(tmp_path):
    repo = repository(tmp_path)
    (repo / 'source.txt').write_text('work in progress')
    home = tmp_path / 'installed'
    result = invoke(repo, home, 'prepare', '--ref', 'HEAD')
    assert result.returncode != 0
    assert 'clean' in result.stderr.lower()
    assert not home.exists()


def tools(tmp_path):
    binary = tmp_path / 'bin'
    binary.mkdir()
    log = tmp_path / 'tools.jsonl'
    tool = '''#!{python}
import json, os, pathlib, sys
with open(os.environ['TOOL_LOG'], 'a') as f: f.write(json.dumps([pathlib.Path(sys.argv[0]).name, *sys.argv[1:]])+'\\n')
if pathlib.Path(sys.argv[0]).name == 'docker':
    if 'inspect' in sys.argv: print('sha256:' + 'a' * 64)
    elif 'ps' in sys.argv: print(os.environ.get('RUNNING_CONTAINER', ''))
if pathlib.Path(sys.argv[0]).name == 'launchctl' and 'print' in sys.argv: sys.exit(1)
if pathlib.Path(sys.argv[0]).name == 'uv':
    target=pathlib.Path('.venv/bin'); target.mkdir(parents=True); (target/'python').symlink_to(sys.executable)
'''.format(python=sys.executable)
    for name in ('docker', 'uv', 'launchctl'):
        path = binary / name
        path.write_text(tool)
        path.chmod(0o755)
    return dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'], TOOL_LOG=str(log)), log


def prepared(tmp_path):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        pytest.skip('Mac helper installation requires Darwin arm64; executable tool seam is checked on Mac')
    repo = repository(tmp_path)
    home = tmp_path / 'installed'
    env, log = tools(tmp_path)
    result = invoke(repo, home, 'prepare', '--ref', 'HEAD', env=env)
    assert result.returncode == 0, result.stderr
    commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    return repo, home, env, log, commit


def test_prepare_freezes_source_and_uses_desktop_context_without_development_mount(tmp_path):
    repo, home, env, log, commit = prepared(tmp_path)
    (repo / 'source.txt').write_text('new development')
    assert (home / 'releases' / commit / 'source/source.txt').read_text() == 'fixed release'
    manifest = json.loads((home / 'releases' / commit / 'manifest.json').read_text())
    assert manifest['image'] == 'sha256:' + 'a' * 64
    assert manifest['protocol'] == 1
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert ['docker', '--context', 'desktop-linux', 'build', '--platform', 'linux/arm64',
            '--tag', 'oko-release:' + commit, str(home / 'releases' / commit / 'source')] in calls
    assert not (home / 'current.json').exists()


def test_install_creates_private_paired_helper_without_starting_or_changing_hosts(tmp_path):
    import plistlib
    repo, home, env, log, commit = prepared(tmp_path)
    before = log.read_text()
    result = invoke(repo, home, 'install', '--release', commit, env=env)
    assert result.returncode == 0, result.stderr
    assert log.read_text() == before
    config = json.loads((home / 'codex.json').read_text())
    assert config['host'] == '127.0.0.1'
    assert config['release'] == commit
    assert (home / 'secrets/codex').stat().st_mode & 0o777 == 0o600
    assert (home / 'codex.json').stat().st_mode & 0o777 == 0o600
    plist = plistlib.loads(next((home / 'launchd').glob('local.oko.codex.*.plist')).read_bytes())
    assert plist['WorkingDirectory'] == str(home / 'releases' / commit / 'source')
    assert plist['ProgramArguments'][0] == str(home / 'releases' / commit / 'source/.venv/bin/python')
    assert not (home / 'data/state.sqlite').exists()


def test_backup_refuses_running_container_and_preserves_data(tmp_path):
    repo, home, env, log, commit = prepared(tmp_path)
    assert invoke(repo, home, 'install', '--release', commit, env=env).returncode == 0
    (home / 'data/material').write_text('saved material')
    env['RUNNING_CONTAINER'] = 'running-container'
    result = invoke(repo, home, 'backup', env=env)
    assert result.returncode != 0
    assert 'stop' in result.stderr.lower()
    assert not (home / 'backups').exists()
    assert (home / 'data/material').read_text() == 'saved material'


def test_backup_succeeds_only_stopped_and_preserves_material_and_release(tmp_path):
    repo, home, env, log, commit = prepared(tmp_path)
    assert invoke(repo, home, 'install', '--release', commit, env=env).returncode == 0
    (home / 'data/material').write_text('saved material')
    result = invoke(repo, home, 'backup', env=env)
    assert result.returncode == 0, result.stderr
    target = Path(result.stdout.strip())
    assert (target / 'data/material').read_text() == 'saved material'
    assert json.loads((target / 'backup.json').read_text())['release'] == commit


def test_migrate_refuses_an_owned_old_store_without_copying(tmp_path):
    import fcntl
    repo, home, env, log, commit = prepared(tmp_path)
    assert invoke(repo, home, 'install', '--release', commit, env=env).returncode == 0
    source = tmp_path / 'old-data'
    source.mkdir()
    (source / 'state.sqlite').write_text('synthetic store')
    with (source / 'instance.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = invoke(repo, home, 'migrate', '--source', str(source), env=env)
    assert result.returncode != 0
    assert 'stop the old instance' in result.stderr.lower()
    assert not (home / 'data/state.sqlite').exists()
    assert (source / 'state.sqlite').read_text() == 'synthetic store'


def test_restore_requires_explicit_data_loss_acknowledgement_before_stopping(tmp_path):
    repo, home, env, log, commit = prepared(tmp_path)
    assert invoke(repo, home, 'install', '--release', commit, env=env).returncode == 0
    before = log.read_text()
    result = invoke(repo, home, 'restore', '--backup', 'old-backup', '--acknowledge-data-loss', 'different', env=env)
    assert result.returncode != 0
    assert 'approve discarding later changes' in result.stderr
    assert log.read_text() == before


def test_prepare_excludes_untracked_private_files_from_release(tmp_path):
    repo = repository(tmp_path)
    (repo / 'private-session.session').write_text('private synthetic credential')
    home = tmp_path / 'installed'
    env, _ = tools(tmp_path)
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        pytest.skip('Mac release preparation')
    result = invoke(repo, home, 'prepare', '--ref', 'HEAD', env=env)
    assert result.returncode == 0, result.stderr
    assert not list((home / 'releases').rglob('*.session'))


def test_status_supports_compose_ndjson(tmp_path):
    repo, home, env, log, commit = prepared(tmp_path)
    assert invoke(repo, home, 'install', '--release', commit, env=env).returncode == 0
    env['RUNNING_CONTAINER'] = '{"Name":"reader"}\n{"Name":"other"}'
    result = invoke(repo, home, 'status', env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['containers'] == [{'Name': 'reader'}, {'Name': 'other'}]


def test_rollback_preserves_modified_data_and_does_not_start_old_version(tmp_path):
    repo, home, env, log, commit = prepared(tmp_path)
    assert invoke(repo, home, 'install', '--release', commit, env=env).returncode == 0
    (home / 'previous.json').write_text(json.dumps({'release': commit, 'digest': 'original-data'}))
    (home / 'data/material').write_text('new user content')
    result = invoke(repo, home, 'rollback', '--release', commit, env=env)
    assert result.returncode != 0
    assert 'Rollback blocked' in result.stderr
    assert (home / 'data/material').read_text() == 'new user content'
    assert any((home / 'backups').glob('*/data/material'))
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any('bootstrap' in call or 'up' in call for call in calls)
