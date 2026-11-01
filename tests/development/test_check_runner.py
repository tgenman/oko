"""Runner orchestration in process; ownership/pipe/signal contracts through real CLI."""
import os
from pathlib import Path
import subprocess
import sys
import pytest

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO


@pytest.fixture(scope='module', autouse=True)
def runner_checkout(tmp_path_factory):
    """Exercise the real CLI against a tiny inventory, independent of API migration."""
    import shutil
    global ROOT
    checkout = tmp_path_factory.mktemp('runner-checkout')
    (checkout / 'scripts').mkdir()
    shutil.copy2(REPO / 'scripts/check', checkout / 'scripts/check')
    for name in ('frontend/app.ts', 'tests/node/test_owner.ts', 'tests/browser/test_material_list.ts',
                 'tests/browser/test_opened_material.ts', 'tests/browser/test_settings_navigation.ts',
                 'tests/browser/test_ui_lifecycle.ts', 'tests/reader/test_web_assets.py',
                 'tests/reader/test_frontend_build.py'):
        file = checkout / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('')
    ROOT = checkout
    try:
        yield
    finally:
        ROOT = REPO


def runtimes(tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.write_text('''#!/usr/bin/env python3
import os, sys
if "merge-base" in sys.argv:
    print("fixture-base")
if "-e" in sys.argv:
    print(os.environ["OKO_BROWSER_EXECUTABLE"])
sys.exit(0)
''')
    runtime.chmod(0o755)
    (tmp_path / 'git').symlink_to(runtime)
    return {**os.environ, 'CI': 'true', 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'], 'OKO_PYTHON': str(runtime), 'OKO_NODE': str(runtime),
            'OKO_BROWSER_EXECUTABLE': sys.executable}


def run_check_in_process(command, *, env, capture_output, text):
    """Exercise main/Report/parallel planning; only synthetic tool execution is inline.

    Process ownership, pipes, signals and real Git guards use subprocess tests.
    Loading a fresh namespace and restoring argv/env keeps these runs independent.
    """
    import contextlib
    import io
    from unittest.mock import patch

    assert capture_output and text
    script = Path(command[1])
    scope = {'__file__': str(script), '__name__': 'runner_under_test'}
    exec(compile(script.read_text(), str(script), 'exec'), scope)

    def execute_tool(arguments, environment, report=None, **output):
        # These tests already provide small Python executables as Node/Python/Git.
        # Execute those exact test programs without another interpreter startup.
        import shutil
        binary = shutil.which(arguments[0], path=environment['PATH'])
        assert binary is not None
        emitted = io.StringIO()
        with contextlib.chdir(scope['ROOT']), patch.dict(os.environ, environment, clear=True), patch.object(sys, 'argv', list(arguments)), contextlib.redirect_stdout(emitted), contextlib.redirect_stderr(emitted):
            try:
                exec(compile(Path(binary).read_text(), binary, 'exec'), {'__name__': '__main__', '__file__': binary})
                code = 0
            except SystemExit as error:
                code = error.code
        if report is not None:
            report.output(emitted.getvalue())
        else:
            output['stdout'].write(emitted.getvalue())
        return code

    scope.update(execute=execute_tool, descendants=lambda pid: set(),
                 tool_changes=lambda root, base: (False, []), checkout_state=lambda excluded: {})
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, env, clear=True), patch.object(sys, 'argv', list(command[1:])), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch.object(subprocess, 'Popen', side_effect=AssertionError('Planning tests must not launch CLI processes')):
        try:
            code = scope['main']()
        except SystemExit as error:
            code = error.code
    return subprocess.CompletedProcess(command, code, stdout.getvalue(), stderr.getvalue())


def test_ui_command_reports_complete_run(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'ui'],
                            env=runtimes(tmp_path), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS browser' in result.stdout
    assert 'PASS syntax' in result.stdout
    assert 'PASS diff' in result.stdout
    assert 'python-tests' not in result.stdout


def test_failed_browser_does_not_hide_other_results(tmp_path):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "sys.exit(7 if '--test' in sys.argv else 0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'all'],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'FAIL browser' in result.stdout
    assert 'PASS python-tests' in result.stdout
    assert 'PASS entrypoint' in result.stdout


def test_interruption_stops_runtime_and_descendant(tmp_path):
    import signal
    import time

    env = runtimes(tmp_path)
    marker = tmp_path / 'running'
    stopped = tmp_path / 'stopped'
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', '''
if '--test' in sys.argv:
    import subprocess, time
    child = subprocess.Popen([sys.executable, '-c', "import signal,time,pathlib; signal.signal(signal.SIGTERM, lambda *_: (pathlib.Path(" + repr(os.environ['STOPPED']) + ").write_text('stopped'), exit(0))); pathlib.Path(" + repr(os.environ['MARKER']) + ").write_text('ready'); time.sleep(60)"], start_new_session=True)
    child.wait()
sys.exit(0)
'''))
    env.update(MARKER=str(marker), STOPPED=str(stopped))
    process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/check'), 'ui'],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130, stdout + stderr
        assert stopped.read_text() == 'stopped'
        assert 'INTERRUPTED browser' in stdout
        assert 'NOT RUN diff' in stdout
        import json
        report_path = Path(stdout.split('Report: ', 1)[1].strip())
        report = json.loads(report_path.read_text())
        assert report['exit_code'] == 130
        assert report['suites']['browser']['status'] == 'INTERRUPTED'
        assert report['suites']['diff']['status'] == 'NOT RUN'
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_missing_browser_dependency_is_not_success(tmp_path):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "sys.exit(1 if '-e' in sys.argv else 0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'all'],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Browser dependencies unavailable' in result.stderr
    assert 'NOT RUN python-tests' in result.stdout
    assert 'PASS' not in result.stdout


def test_failed_http_module_check_fails_ui(tmp_path):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_PYTHON'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "sys.exit(7 if 'tests/reader/test_web_assets.py' in sys.argv else 0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'ui'],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'FAIL http' in result.stdout


def test_browser_file_selection_only_runs_requested_file(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'cpu_count', lambda: 4)
    env = runtimes(tmp_path)
    record = tmp_path / 'commands'
    env['RECORD'] = str(record)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nwith Path(os.environ['RECORD']).open('a') as f: f.write(repr(sys.argv) + '\\n')\nsys.exit(0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'browser', '--files', 'tests/browser/test_material_list.ts'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = record.read_text()
    assert '--test-concurrency=4' in commands
    assert 'test_material_list.ts' in commands
    assert 'test_opened_material.ts' not in commands
    assert 'PASS browser' in result.stdout


def test_browser_filter_matching_nothing_is_not_success(tmp_path):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "if '--test' in sys.argv: print('# pass 0')\nsys.exit(0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'browser', '--match', 'missing scenario'], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert 'No browser scenarios matched' in result.stdout


def test_python_guard_allows_dirty_baseline_and_owned_artifacts_but_rejects_new_files(tmp_path):
    import shutil
    checkout=tmp_path/'checkout'
    (checkout/'scripts').mkdir(parents=True)
    shutil.copy(ROOT/'scripts/check',checkout/'scripts/check')
    tracked=checkout/'source.txt';tracked.write_text('original\n')
    subprocess.run(['git','init','-q',str(checkout)],check=True)
    subprocess.run(['git','-C',str(checkout),'add','source.txt'],check=True)
    tracked.write_text('preexisting draft\n')
    env=runtimes(tmp_path);env['PATH']=os.environ['PATH']
    runtime=Path(env['OKO_PYTHON'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', '''
if 'pytest' in sys.argv:
    from pathlib import Path
    Path(os.environ['OKO_TEST_ARTIFACT_DIR'],'capture.png').write_bytes(b'synthetic artifact')
    if os.environ.get('CREATE_NEW'): Path('generated.txt').write_text('unexpected output')
sys.exit(0)
'''))
    command=[sys.executable,str(checkout/'scripts/check'),'python','--report-dir',str(checkout/'reports')]
    first=subprocess.run(command,env=env,capture_output=True,text=True)
    assert first.returncode==0,first.stdout+first.stderr
    assert tracked.read_text()=='preexisting draft\n'
    assert list((checkout/'reports').glob('*/artifacts/python-tests/capture.png'))
    second=subprocess.run(command,env={**env,'CREATE_NEW':'1'},capture_output=True,text=True)
    assert second.returncode==1,second.stdout+second.stderr
    assert 'Checkout changed during checks:\n  generated.txt' in second.stdout
    assert tracked.read_text()=='preexisting draft\n'


def test_tool_selection_covers_branch_index_worktree_untracked_and_deleted(tmp_path):
    import runpy
    scope = runpy.run_path(str(REPO / 'scripts/check'))['tool_changes']
    checkout = tmp_path / 'scope'
    checkout.mkdir()
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=checkout, text=True).strip()
    git('init', '-q')
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@example.test')
    (checkout / 'scripts').mkdir()
    file = checkout / 'scripts/check'
    file.write_text('original')
    git('add', '.')
    git('commit', '-qm', 'base')
    base = git('rev-parse', 'HEAD')
    assert scope(checkout)[0]  # Missing origin/main must fail closed.
    assert scope(checkout, base) == (False, [])
    (checkout / 'app.py').write_text('app change')
    assert scope(checkout, base) == (False, [])
    file.write_text('worktree change')
    assert scope(checkout, base) == (True, ['scripts/check'])
    git('add', 'scripts/check')
    file.write_text('original')
    # git diff base sees effective worktree; staged bytes must be checked too.
    assert scope(checkout, base)[0]
    file.write_text('worktree change')
    git('add', 'scripts/check')
    git('commit', '-qm', 'tool change')
    assert scope(checkout, base)[0]
    assert not scope(checkout, 'HEAD')[0]
    file.unlink()
    assert scope(checkout, 'HEAD')[0]
    git('restore', 'scripts/check')
    git('mv', 'scripts/check', 'moved-outside-tools.py')
    assert scope(checkout, 'HEAD') == (True, ['scripts/check'])
    git('commit', '-qm', 'move')
    lock = checkout / 'uv.lock'
    lock.write_text('version = 1\n')
    assert scope(checkout, 'HEAD') == (True, ['uv.lock'])
    git('add', 'uv.lock')
    lock.unlink()
    assert scope(checkout, 'HEAD') == (True, ['uv.lock'])
    git('reset', '-q', 'HEAD', '--', 'uv.lock')
    (checkout / 'scripts/new-tool').write_text('new')
    assert scope(checkout, 'HEAD')[0]
    assert scope(checkout, 'missing-reference')[0]


@pytest.mark.parametrize('sets, workers', [(['python'], 4), (['python', 'tools'], 4)])
def test_python_shards_cover_each_selected_file_once_and_report_failure(tmp_path, sets, workers):
    import ast
    import json
    env = runtimes(tmp_path)
    record = tmp_path / 'commands'
    env['RECORD'] = str(record)
    runtime = Path(env['OKO_PYTHON'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', """
if 'pytest' in sys.argv:
    from pathlib import Path
    with Path(os.environ['RECORD']).open('a') as output: output.write(repr(sys.argv) + '\\n')
    if any(value.endswith('test_frontend_build.py') for value in sys.argv):
        print('FAILED tests/reader/test_frontend_build.py::test_fixture')
        sys.exit(1)
sys.exit(0)
"""))
    files = ['tests/reader/test_web_assets.py', 'tests/reader/test_frontend_build.py',
             'tests/reader/test_security.py', 'tests/reader/test_frontend_delivery.py']
    reports = tmp_path / 'reports'
    extra = [ROOT / name for name in files[2:]]
    for file in extra:
        file.write_text('')
    try:
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), *sets, '--report-dir', str(reports),
                                 '--files', *files], env=env, capture_output=True, text=True)
    finally:
        for file in extra:
            file.unlink()
    assert result.returncode == 1
    commands = [ast.literal_eval(line) for line in record.read_text().splitlines()]
    report = json.loads(next(reports.glob('*/report.json')).read_text())
    group = report['suites']['python-tests']
    reported = [entry['command'] for entry in group['commands']]
    assert all(command in commands for command in reported)
    commands = reported
    assert len(commands) == workers
    assert all(command[command.index('-q') + 1:] for command in commands)
    selected = [value for command in commands for value in command[command.index('-q') + 1:] if value.endswith('.py')]
    assert sorted(selected) == sorted(str(ROOT / name) for name in files)
    assert len(group['workers']) == workers and group['status'].startswith('FAIL')
    assert group['failures'] == ['tests/reader/test_frontend_build.py::test_fixture']


def test_ci_tool_selection_does_not_turn_errors_into_skips(tmp_path):
    import yaml
    workflow = yaml.safe_load((REPO / '.github/workflows/check.yml').read_text())
    script = next(step['run'] for step in workflow['jobs']['tests']['steps'] if step.get('id') == 'selection')
    binary = tmp_path / 'python3'
    output = tmp_path / 'output'
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
           'GITHUB_OUTPUT': str(output), 'CHECK_BASE': 'base'}
    for body, success in [('exit 7', False), ('echo unexpected', False), ('echo true', True), ('echo false', True)]:
        binary.write_text('#!/bin/sh\n' + body + '\n')
        binary.chmod(0o755)
        result = subprocess.run(['bash', '-e', '-c', script], env=env, capture_output=True, text=True)
        assert (result.returncode == 0) == success, result.stdout + result.stderr


def test_node_selection_reports_counts_without_browser_probe(tmp_path):
    env=runtimes(tmp_path)
    env.pop('OKO_BROWSER_EXECUTABLE')
    env['OKO_PYTHON']=str(tmp_path/'missing-python')
    record=tmp_path/'commands'
    runtime=Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)',
        "from pathlib import Path\nwith Path("+repr(str(record))+").open('a') as out: out.write(repr(sys.argv)+'\\n')\nif '--test' in sys.argv: print('# tests 2\\n# pass 1\\n# fail 0\\n# skipped 1\\n# cancelled 0')\nsys.exit(0)"))
    result=run_check_in_process([sys.executable,str(ROOT/'scripts/check'),'node','--files','tests/node/test_owner.ts'],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    calls=record.read_text()
    assert 'run-browser-tests' not in calls and "'-e'" not in calls
    assert calls.count('test_owner.ts')==1
    import json
    report=json.loads(Path(result.stdout.split('Report: ')[-1].strip()).read_text())
    assert report['test_groups']['Node']['counts']==dict(total=2,passed=1,failed=0,skipped=1,cancelled=0)
    assert report['test_groups']['Node']['duration_seconds']>=0
    assert 'Playwright' not in report['test_groups']
