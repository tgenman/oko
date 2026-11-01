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
    for name in ('docker', 'uv', 'launchctl', 'open'):
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


def test_retired_backup_refuses_even_when_stopped(tmp_path):
    repo, home, env, log, commit = prepared(tmp_path)
    assert invoke(repo, home, 'install', '--release', commit, env=env).returncode == 0
    (home / 'data/material').write_text('saved material')
    before = log.read_text()
    result = invoke(repo, home, 'backup', env=env)
    assert result.returncode != 0 and 'retired' in result.stderr
    assert (home / 'data/material').read_text() == 'saved material'
    assert not (home / 'backups').exists()
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


def test_open_launches_localhost_8765_without_logging_reader_credential(tmp_path):
    repo, home, env, log, commit = prepared(tmp_path)
    assert invoke(repo, home, 'install', '--release', commit, env=env).returncode == 0
    (home / 'data/runtime.json').write_text(json.dumps({'token': 'synthetic-private-token'}))
    result = invoke(repo, home, 'open', env=env)
    assert result.returncode == 0, result.stderr
    assert 'synthetic-private-token' not in result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[-1] == ['open', 'http://localhost:8765/']


def test_unreachable_helper_exits_at_deadline_and_stops_new_reader(tmp_path,monkeypatch):
    import builtins
    import runpy
    import urllib.request
    import urllib.error
    from types import SimpleNamespace
    maintenance=runpy.run_path(str(SCRIPT));scope=maintenance['start'].__globals__
    home=tmp_path/'installation';(home/'secrets').mkdir(parents=True)
    (home/'secrets/codex').write_text('synthetic-token')
    (home/'current.json').write_text(json.dumps(dict(release='a'*40)))
    (home/'installation.json').write_text(json.dumps(dict(launchagents=str(tmp_path/'agents'))))
    ticks=iter([0,20]);attempts=[];stopped=[]
    clock=SimpleNamespace(monotonic=lambda:next(ticks),sleep=lambda seconds:pytest.fail('Deadline exceeded: no more retries allowed'))
    def importing(name,*args,**kwargs):return clock if name=='time' else builtins.__import__(name,*args,**kwargs)
    def unavailable(*args,**kwargs):
        attempts.append(True)
        raise urllib.error.URLError(ConnectionRefusedError('synthetic unreachable helper'))
    monkeypatch.setattr(urllib.request.OpenerDirector,'open',unavailable)
    for name,fn in {'helper_loaded':lambda home:False,'available':lambda *args,**kwargs:None,'run':lambda *args,**kwargs:None,'readiness':lambda home:None,'stop':lambda home:stopped.append(True)}.items():monkeypatch.setitem(scope,name,fn)
    def compose(home,*args,**kwargs):
        if args[0]=='ps':return ''
        if args[0]=='exec':exec(args[-1].replace('/run/secrets/codex',str(home/'secrets/codex')),{'__builtins__':dict(vars(builtins),__import__=importing)})
    monkeypatch.setitem(scope,'compose',compose)
    with pytest.raises(urllib.error.URLError):maintenance['start'](home)
    assert attempts==[True] and stopped==[True]
