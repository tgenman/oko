"""Full checks use a Git-bounded working snapshot, never the editable checkout."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.STDOUT).decode().strip()


def fixture(tmp_path):
    root = tmp_path / 'source'
    (root / 'scripts').mkdir(parents=True)
    for name in ('check', 'task_changes.py', 'check_snapshot.py'):
        if (SCRIPTS / name).exists():
            shutil.copy2(SCRIPTS / name, root / 'scripts' / name)
    for name in ('frontend/app.ts', 'tests/browser/test_example.ts'):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('')
    (root / '.gitignore').write_text('node_modules/\n.venv/\n.scratch/\n__pycache__/\n')
    (root / 'document.md').write_text('committed')
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.test')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'base')
    (root / 'document.md').write_text('captured')
    (root / 'new.txt').write_text('staged')
    git(root, 'add', 'new.txt')
    (root / 'private.txt').write_text('not part of the task')
    runtime = tmp_path / 'runtime'
    runtime.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
if '-e' in sys.argv: print(sys.executable)
if '--test' in sys.argv:
    assert pathlib.Path('document.md').read_text() == 'captured'
    assert pathlib.Path('new.txt').read_text() == 'staged'
    pathlib.Path(os.environ['SOURCE_DOCUMENT']).write_text('edited during checks')
    if os.environ.get('MUTATE_SNAPSHOT'): pathlib.Path('document.md').write_text('bad test mutation')
    print('# pass 1')
''')
    runtime.chmod(0o755)
    env = dict(os.environ, OKO_NODE=str(runtime), OKO_PYTHON=str(runtime), SOURCE_DOCUMENT=str(root/'document.md'))
    env.pop('CI', None)
    return root, env


def run(root, env, reports):
    return subprocess.run([sys.executable, str(root/'scripts/check'), 'all', '--report-dir', str(reports)], cwd=root, env=env, capture_output=True, text=True, timeout=30)


def test_all_isolates_working_files_and_excludes_untracked_data(tmp_path):
    root, env = fixture(tmp_path)
    index = git(root, 'write-tree')
    reports = tmp_path / 'reports'
    result = run(root, env, reports)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(next(reports.glob('*/report.json')).read_text())
    snapshot = Path(report['snapshot']['checkout'])
    assert snapshot != root
    assert (snapshot/'document.md').read_text() == 'captured'
    assert (root/'document.md').read_text() == 'edited during checks'
    assert not (snapshot/'private.txt').exists()
    assert git(root, 'write-tree') == index
    assert report['checkout']['status'] == 'PASS'
    assert not git(snapshot, 'remote')


def test_snapshot_still_detects_test_mutation(tmp_path):
    root, env = fixture(tmp_path)
    reports = tmp_path / 'reports'
    result = run(root, dict(env, MUTATE_SNAPSHOT='1'), reports)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads(next(reports.glob('*/report.json')).read_text())
    assert report['checkout'] == {'status': 'FAIL', 'changed_files': ['document.md']}
    assert report['suites']['browser']['status'].startswith('PASS')
    assert (root/'document.md').read_text() == 'edited during checks'


def test_all_requires_new_code_to_be_staged_instead_of_silently_omitting_it(tmp_path):
    root, env = fixture(tmp_path)
    (root/'frontend/new.ts').write_text('new source')
    result = run(root, env, tmp_path/'reports')
    assert result.returncode == 1
    assert 'frontend/new.ts' in result.stderr
    assert 'Stage' in result.stderr
    assert (root/'document.md').read_text() == 'captured'


def test_snapshot_termination_finishes_report_and_stops_owned_test_processes(tmp_path):
    import signal
    import time
    root, env = fixture(tmp_path)
    markers = tmp_path/'ready'
    markers.mkdir()
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace("if '--test' in sys.argv:", """if '--test' in sys.argv or ('pytest' in sys.argv and not any('test_web_assets.py' in arg for arg in sys.argv)):
    import time
    pathlib.Path(os.environ['READY'], str(os.getpid())).write_text('ready')
    while True: time.sleep(.05)
if '--test' in sys.argv:"""))
    reports = tmp_path/'reports'
    process = subprocess.Popen([sys.executable, str(root/'scripts/check'), 'all', '--compact', '--report-dir', str(reports)], cwd=root, env=dict(env, READY=str(markers)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while len(list(markers.iterdir())) < 2:
            if process.poll() is not None or time.monotonic() > deadline:
                raise AssertionError('Owned test processes did not start')
            time.sleep(.02)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130, stdout + stderr
        assert 'Traceback' not in stderr
        report = json.loads(next(reports.glob('*/report.json')).read_text())
        assert report['exit_code'] == 130
        for marker in markers.iterdir():
            try:
                os.kill(int(marker.name), 0)
            except ProcessLookupError:
                continue
            raise AssertionError('Owned process survived cancellation: ' + marker.name)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for marker in markers.iterdir():
            try:
                os.kill(int(marker.name), signal.SIGKILL)
            except ProcessLookupError:
                pass
