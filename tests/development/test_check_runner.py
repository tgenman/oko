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
    for name in ('frontend/app.ts', 'tests/browser/test_material_list.ts',
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


def test_syntax_discovers_nested_frontend_modules(tmp_path):
    import shutil
    checkout = tmp_path / 'checkout'
    (checkout / 'scripts').mkdir(parents=True)
    (checkout / 'frontend/nested').mkdir(parents=True)
    shutil.copy(ROOT / 'scripts/check', checkout / 'scripts/check')
    (checkout / 'frontend/app.js').write_text('')
    nested = checkout / 'frontend/nested/new.mjs'
    nested.write_text('')
    env = runtimes(tmp_path)
    record = tmp_path / 'commands'
    env['RECORD'] = str(record)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nwith Path(os.environ['RECORD']).open('a') as output: output.write(repr(sys.argv) + '\\n')\nsys.exit(0)"))
    result = run_check_in_process([sys.executable, str(checkout / 'scripts/check'), 'syntax'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(nested) in record.read_text()


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


def test_missing_runtime_reports_unexecuted_checks(tmp_path):
    env = runtimes(tmp_path)
    env['OKO_NODE'] = str(tmp_path / 'missing-node')
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'ui'],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Node missing' in result.stderr
    assert 'NOT RUN browser' in result.stdout
    assert 'PASS' not in result.stdout


def test_unknown_mode_fails_before_running_checks():
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'other'],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert 'invalid choice' in result.stderr


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


def test_ui_checks_production_http_modules_before_browser(tmp_path):
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'ui'],
                            env=runtimes(tmp_path), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.index('RUN http') < result.stdout.index('RUN browser\n')
    assert 'PASS http' in result.stdout


def test_python_and_types_do_not_require_node(tmp_path):
    env = runtimes(tmp_path)
    env['OKO_NODE'] = str(tmp_path / 'absent-node')
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'python', 'types'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS python-tests' in result.stdout and 'PASS types' in result.stdout
    assert 'RUN browser' not in result.stdout and 'RUN syntax' not in result.stdout


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


def test_browser_types_failure_prevents_browser_execution(tmp_path):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace(
        'sys.exit(0)', "sys.exit(7 if any('tsconfig.browser-tests.json' in arg for arg in sys.argv) else 0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'browser'],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'FAIL browser-types' in result.stdout
    assert 'NOT RUN browser\n' in result.stdout
    assert '\nRUN browser\n' not in result.stdout


def test_tooling_types_failure_prevents_build_and_browser(tmp_path):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace(
        'sys.exit(0)', "sys.exit(7 if any('tsconfig.tooling.json' in arg for arg in sys.argv) else 0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'ui'],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'FAIL tooling-types' in result.stdout
    assert 'NOT RUN build\n' in result.stdout
    assert 'NOT RUN http\n' in result.stdout
    assert 'NOT RUN browser\n' in result.stdout
    assert '\nRUN build\n' not in result.stdout
    assert '\nRUN browser\n' not in result.stdout


def test_invalid_file_selection_fails_before_checks(tmp_path):
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'browser', '--files', 'tests/browser/missing.ts'], env=runtimes(tmp_path), capture_output=True, text=True)
    assert result.returncode != 0
    assert 'RUN browser' not in result.stdout


def test_python_file_selection_supports_test_id_and_does_not_require_node(tmp_path):
    env = runtimes(tmp_path)
    env['OKO_NODE'] = str(tmp_path / 'missing-node')
    record = tmp_path / 'commands'
    env['RECORD'] = str(record)
    runtime = Path(env['OKO_PYTHON'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nwith Path(os.environ['RECORD']).open('a') as f: f.write(repr(sys.argv) + '\\n')\nsys.exit(0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'python', '--files', 'tests/reader/test_web_assets.py::test_reader_serves_entire_browser_module_graph_without_exposing_reader_data'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'test_web_assets.py::test_reader_serves_entire_browser_module_graph_without_exposing_reader_data' in record.read_text()
    assert 'PASS python-tests' in result.stdout


def test_selection_rejects_file_from_wrong_suite_and_outside_checkout(tmp_path):
    env = runtimes(tmp_path)
    for filename in ('tests/reader/test_web_assets.py', str(tmp_path / 'test_external.ts')):
        if filename.startswith(str(tmp_path)):
            Path(filename).write_text('')
        result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'browser', '--files', filename], env=env, capture_output=True, text=True)
        assert result.returncode == 2
        assert 'RUN browser' not in result.stdout


def test_browser_gets_resolved_python_in_checkout_without_virtualenv(tmp_path):
    import shutil
    checkout = tmp_path / 'checkout'
    (checkout / 'scripts').mkdir(parents=True)
    (checkout / 'tests/browser').mkdir(parents=True)
    shutil.copy(ROOT / 'scripts/check', checkout / 'scripts/check')
    (checkout / 'tests/browser/test_reader_http.ts').write_text('')
    env = runtimes(tmp_path)
    env.pop('OKO_PYTHON')
    (tmp_path / 'python3').symlink_to(sys.executable)
    record = tmp_path / 'python-choice'
    env['RECORD'] = str(record)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('#!/usr/bin/env python3', '#!' + sys.executable).replace('sys.exit(0)', "from pathlib import Path\nif '--test' in sys.argv: Path(os.environ['RECORD']).write_text(os.environ.get('OKO_PYTHON', 'missing'))\nsys.exit(0)"))
    result = subprocess.run([sys.executable, str(checkout / 'scripts/check'), 'browser'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert record.read_text() == str(tmp_path / 'python3')


def test_report_keeps_full_output_failures_and_separate_reruns(tmp_path):
    import json
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', '''
if '--test' in sys.argv:
    print('large diagnostic: ' + 'x' * 100000)
    print('stderr diagnostic', file=sys.stderr)
    print('not ok 1 - selected browser scenario')
    sys.exit(7)
if 'pytest' in sys.argv:
    print('FAILED tests/example.py::test_example[parameter with spaces] - assertion')
    print('ERROR tests/example.py::test_setup - setup')
    sys.exit(8)
sys.exit(0)
'''))
    reports = tmp_path / 'reports'
    command = [sys.executable, str(ROOT / 'scripts/check'), 'all', '--report-dir', str(reports)]
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    first = next(reports.iterdir())
    report = json.loads((first / 'report.json').read_text())
    assert report['exit_code'] == 1
    assert report['suites']['browser']['status'].startswith('FAIL')
    assert report['suites']['browser']['failures'] == ['selected browser scenario']
    assert report['suites']['python-tests']['failures'] == ['tests/example.py::test_example[parameter with spaces]', 'tests/example.py::test_setup']
    assert report['suites']['browser']['commands'][0]['exit_code'] == 7
    assert report['suites']['types']['status'].startswith('PASS')
    log = (first / 'output.log').read_text()
    assert 'x' * 100000 in log and 'stderr diagnostic' in log
    first_json = (first / 'report.json').read_text()
    runtime.write_text('#!/usr/bin/env python3\nimport os, sys\nif \"-e\" in sys.argv: print(os.environ[\"OKO_BROWSER_EXECUTABLE\"])\nsys.exit(0)\n')
    retry = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'browser', '--files', 'tests/browser/test_material_list.ts', '--report-dir', str(reports)], env=env, capture_output=True, text=True)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert (first / 'report.json').read_text() == first_json
    second = next(path for path in reports.iterdir() if path != first)
    rerun = json.loads((second / 'report.json').read_text())
    assert list(rerun['suites']) == ['tooling-types', 'browser-types', 'browser']
    assert rerun['selected_files']['browser'] == [str(ROOT / 'tests/browser/test_material_list.ts')]


def test_report_records_missing_dependency_without_claiming_success(tmp_path):
    import json
    env = runtimes(tmp_path)
    env['OKO_NODE'] = str(tmp_path / 'missing-node')
    reports = tmp_path / 'reports'
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'ui', '--report-dir', str(reports)], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    directory = next(reports.iterdir())
    report = json.loads((directory / 'report.json').read_text())
    assert all(suite['status'] == 'NOT RUN' for suite in report['suites'].values())
    assert 'Node missing' in (directory / 'output.log').read_text()


def test_report_does_not_wait_for_exited_commands_child_holding_stdout(tmp_path):
    import json
    import signal
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_PYTHON'])
    child_pid = tmp_path / 'child-pid'
    env['CHILD_PID'] = str(child_pid)
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', '''
if 'pytest' in sys.argv:
    import subprocess
    from pathlib import Path
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    Path(os.environ['CHILD_PID']).write_text(str(child.pid))
    os.write(1, 'FAILED tests/example.py::test_последняя'.encode('utf-8'))
    sys.exit(7)
sys.exit(0)
'''))
    reports = tmp_path / 'reports'
    try:
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'python', '--report-dir', str(reports)], env=env, capture_output=True, text=True, timeout=8)
        assert result.returncode == 1, result.stdout + result.stderr
        report = json.loads((next(reports.iterdir()) / 'report.json').read_text())
        assert report['suites']['python-tests']['failures'] == ['tests/example.py::test_последняя']
    finally:
        if child_pid.exists():
            try:
                os.kill(int(child_pid.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_browser_name_filter_is_passed_and_reported(tmp_path):
    import json
    env = runtimes(tmp_path)
    record = tmp_path / 'commands'
    env['RECORD'] = str(record)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nwith Path(os.environ['RECORD']).open('a') as output: output.write(repr(sys.argv) + '\\n')\nif '--test' in sys.argv: print('# pass 1' if '--test-reporter=tap' in sys.argv else 'ℹ pass 1')\nsys.exit(0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'browser', '--match', 'late.*draft', '--files', 'tests/browser/test_settings_navigation.ts', '--report-dir', str(tmp_path / 'reports')], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '--test-name-pattern=late.*draft' in record.read_text()
    report = json.loads((next((tmp_path / 'reports').iterdir()) / 'report.json').read_text())
    assert report['browser_name_pattern'] == 'late.*draft'
    assert report['suites']['browser']['passed_scenarios'] == 1


def test_failed_lint_skips_browser_but_keeps_other_results(tmp_path):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "sys.exit(7 if any('lint-js.cts' in value for value in sys.argv) else 0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'ui'], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert 'FAIL lint' in result.stdout and 'NOT RUN browser' in result.stdout
    assert '\nRUN browser\n' not in result.stdout
    assert 'PASS diff' in result.stdout


def test_browser_filter_matching_nothing_is_not_success(tmp_path):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "if '--test' in sys.argv: print('# pass 0')\nsys.exit(0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'browser', '--match', 'missing scenario'], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert 'No browser scenarios matched' in result.stdout


def test_browser_artifacts_are_scoped_to_run_and_checkout_mutation_fails(tmp_path):
    import json
    import shutil
    checkout = tmp_path / 'checkout'
    (checkout / 'scripts').mkdir(parents=True)
    (checkout / 'tests/browser').mkdir(parents=True)
    shutil.copy(ROOT / 'scripts/check', checkout / 'scripts/check')
    tracked = checkout / 'tests/browser/test_example.ts'
    tracked.write_text('original\n')
    subprocess.run(['git','init','-q',str(checkout)],check=True)
    subprocess.run(['git','-C',str(checkout),'add','tests/browser/test_example.ts'],check=True)
    env = runtimes(tmp_path)
    env['PATH'] = os.environ['PATH']  # This scenario inspects a real temporary Git inventory.
    env['MUTATE'] = str(tracked)
    env['ARTIFACT_RECORD'] = str(tmp_path / 'artifact-directory')
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', '''
if '--test' in sys.argv:
    from pathlib import Path
    Path(os.environ['MUTATE']).write_text('changed by test\\n')
    Path(os.environ['ARTIFACT_RECORD']).write_text(os.environ.get('OKO_TEST_ARTIFACT_DIR','missing'))
    print('# pass 1')
sys.exit(0)
'''))
    result = subprocess.run([sys.executable,str(checkout/'scripts/check'),'browser','--report-dir',str(tmp_path/'reports')],env=env,capture_output=True,text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'Checkout changed during checks' in result.stdout
    assert 'tests/browser/test_example.ts' in result.stdout
    assert tracked.read_text() == 'changed by test\n'  # The guard reports, it never resets user files.
    directory = next((tmp_path/'reports').iterdir())
    assert Path((tmp_path/'artifact-directory').read_text()) == directory/'artifacts/browser'
    report=json.loads((directory/'report.json').read_text())
    assert report['suites']['browser']['checkout_changes'] == ['tests/browser/test_example.ts']


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


def test_lifecycle_profile_selects_dedicated_scenarios(tmp_path):
    env=runtimes(tmp_path);record=tmp_path/'commands';env['RECORD']=str(record)
    runtime=Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nwith Path(os.environ['RECORD']).open('a') as output: output.write(repr(sys.argv) + '\\n')\nsys.exit(0)"))
    result=run_check_in_process([sys.executable,str(ROOT/'scripts/check'),'lifecycle'],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'test_ui_lifecycle.ts' in record.read_text()
    assert 'test_material_list.ts' not in record.read_text()


def test_explicit_suite_order_cannot_run_browser_before_selected_lint(tmp_path):
    env=runtimes(tmp_path)
    runtime=Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "sys.exit(7 if any('lint-js.cts' in value for value in sys.argv) else 0)"))
    result=run_check_in_process([sys.executable,str(ROOT/'scripts/check'),'browser','syntax','lint'],env=env,capture_output=True,text=True)
    assert result.returncode==1,result.stdout+result.stderr
    assert '\nRUN browser\n' not in result.stdout
    assert 'PASS syntax' in result.stdout and 'FAIL lint' in result.stdout and 'NOT RUN browser' in result.stdout


@pytest.mark.parametrize('sets', [('all',), ('browser','python','types'), ('python','types')])
def test_types_run_before_long_checks_and_failure_skips_them(tmp_path, sets):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_PYTHON'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "sys.exit(3 if 'mypy' in sys.argv else 0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), *sets],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'FAIL types' in result.stdout
    assert '\nRUN python-tests\n' not in result.stdout
    assert 'NOT RUN python-tests' in result.stdout
    if 'browser' in sets or 'all' in sets:
        assert '\nRUN browser\n' not in result.stdout
        assert 'NOT RUN browser' in result.stdout


def test_full_python_collection_excludes_only_already_selected_http_modules(tmp_path):
    import ast
    env = runtimes(tmp_path)
    record = tmp_path / 'commands'
    env['RECORD'] = str(record)
    runtime = Path(env['OKO_PYTHON'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nwith Path(os.environ['RECORD']).open('a') as output: output.write(repr(sys.argv) + '\\n')\nsys.exit(0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'all'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = [ast.literal_eval(line) for line in record.read_text().splitlines()]
    pytest = [command for command in commands if 'pytest' in command]
    assert len(pytest) == 2
    modules = pytest[0][pytest[0].index('-q') + 1:]
    assert set(pytest[1][pytest[1].index('-q') + 1:]) == {'--ignore=' + name for name in [*modules, 'tests/development/test_agent_tools.py', 'tests/development/test_update_checkout.py', 'tests/development/test_check_report.py', 'tests/development/test_check_snapshot.py', 'tests/development/test_api_generation.py', 'tests/development/test_browser_host.py', 'tests/development/test_build_speed.py', 'tests/development/test_check_runner.py', 'tests/development/test_frontend_builder.py', 'tests/development/test_github_issues.py', 'tests/development/test_hooks.py', 'tests/development/test_process_logs.py', 'tests/development/test_task_changes.py', 'tests/api/test_governance.py', 'tests/api/test_strict_generation.py']}
    record.write_text('')
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'python', '--files',
                             'tests/reader/test_frontend_build.py'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = [ast.literal_eval(line) for line in record.read_text().splitlines()]
    pytest = next(command for command in commands if 'pytest' in command)
    assert not any(value.startswith('--ignore=') for value in pytest)
    assert pytest[-1].endswith('tests/reader/test_frontend_build.py')


def test_parallel_groups_overlap_and_keep_separate_logs(tmp_path):
    import json
    env = runtimes(tmp_path)
    barrier = tmp_path / 'barrier'
    barrier.mkdir()
    env['BARRIER'] = str(barrier)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', """
if '--test' in sys.argv or ('pytest' in sys.argv and 'tests/reader/test_web_assets.py' not in sys.argv):
    import time
    from pathlib import Path
    name = 'browser' if '--test' in sys.argv else 'python-tests'
    directory = Path(os.environ['BARRIER'])
    (directory / name).write_text('started')
    deadline = time.monotonic() + 5
    while len(list(directory.iterdir())) < 2:
        if time.monotonic() > deadline:
            sys.exit(9)
        time.sleep(.01)
    print('owned-output-' + name)
sys.exit(0)
"""))
    reports = tmp_path / 'reports'
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'all', '--report-dir', str(reports)], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(next(reports.glob('*/report.json')).read_text())
    for name in ('browser', 'python-tests'):
        assert report['suites'][name]['status'].startswith('PASS')
        log = next(reports.glob('*/parallel/' + name + '/*/output.log')).read_text()
        assert 'owned-output-' + name in log
        other = 'python-tests' if name == 'browser' else 'browser'
        assert 'owned-output-' + other not in log


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


@pytest.mark.parametrize('shards', [False, True])
def test_parallel_interruption_reaps_both_groups_and_their_children(tmp_path, shards):
    import json
    import signal
    import time
    env = runtimes(tmp_path)
    markers = tmp_path / 'markers'
    markers.mkdir()
    env['MARKERS'] = str(markers)
    if shards:
        env['CHECK_SHARDS'] = '1'
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', """
if '--test' in sys.argv or ('pytest' in sys.argv and (os.environ.get('CHECK_SHARDS') or 'tests/reader/test_web_assets.py' not in sys.argv)):
    import subprocess
    from pathlib import Path
    name = Path(sys.argv[-1]).stem if os.environ.get('CHECK_SHARDS') else ('browser' if '--test' in sys.argv else 'python-tests')
    child_code = 'import os,signal,time; from pathlib import Path; root=Path(os.environ["MARKERS"]); name=' + repr(name) + '; signal.signal(signal.SIGTERM, lambda *_: (root.joinpath(name+"-stopped").write_text("stopped"), exit(0))); root.joinpath(name+"-ready").write_text(str(os.getpid())); time.sleep(60)'
    subprocess.run([sys.executable, '-c', child_code], start_new_session=True)
sys.exit(0)
"""))
    reports = tmp_path / 'reports'
    process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/check'), 'python' if shards else 'all', '--report-dir', str(reports)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while len(list(markers.glob('*-ready'))) < 2 and time.monotonic() < deadline:
            time.sleep(.02)
        assert len(list(markers.glob('*-ready'))) == 2
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130, stdout + stderr
        report = json.loads(next(reports.glob('*/report.json')).read_text())
        for name in (('test_frontend_build', 'test_web_assets') if shards else ('browser', 'python-tests')):
            assert report['suites']['python-tests' if shards else name]['status'] == 'INTERRUPTED'
            assert (markers / (name + '-stopped')).read_text() == 'stopped'
            pid = int((markers / (name + '-ready')).read_text())
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for ready in markers.glob('*-ready'):
            try:
                os.kill(int(ready.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_parallel_mutation_fails_both_groups_without_reverting_files(tmp_path):
    import shutil
    import json
    checkout = tmp_path / 'checkout'
    shutil.copytree(ROOT, checkout)
    tracked = checkout / 'frontend/app.ts'
    subprocess.run(['git', 'init', '-q'], cwd=checkout, check=True)
    subprocess.run(['git', 'add', '.'], cwd=checkout, check=True)
    env = runtimes(tmp_path)
    env['PATH'] = os.environ['PATH']
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nif '--test' in sys.argv: Path('frontend/app.ts').write_text('unexpected mutation')\nsys.exit(0)"))
    reports = tmp_path / 'reports'
    result = subprocess.run([sys.executable, str(checkout / 'scripts/check'), 'browser', 'python', '--report-dir', str(reports)], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads(next(reports.glob('*/report.json')).read_text())
    for name in ('browser', 'python-tests'):
        assert report['suites'][name]['status'].startswith('PASS')
        assert report['suites'][name]['checkout_changes'] == ['frontend/app.ts']
    assert report['checkout'] == {'status': 'FAIL', 'changed_files': ['frontend/app.ts']}
    assert tracked.read_text() == 'unexpected mutation'


def test_explicit_tools_are_not_duplicated_and_build_precedes_parallel_groups(tmp_path):
    import ast
    env = runtimes(tmp_path)
    record = tmp_path / 'commands'
    env['RECORD'] = str(record)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nwith Path(os.environ['RECORD']).open('a') as output: output.write(repr(sys.argv) + '\\n')\nsys.exit(0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'all', 'tools'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = [ast.literal_eval(line) for line in record.read_text().splitlines()]
    assert sum('tests/development/test_hooks.py' in command for command in commands) == 1
    record.write_text('')
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'browser', 'python', 'build'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = [ast.literal_eval(line) for line in record.read_text().splitlines()]
    last_build = max(i for i, command in enumerate(commands) if any('build-frontend.mts' in arg for arg in command))
    first_test = min(i for i, command in enumerate(commands) if '--test' in command or 'pytest' in command)
    assert last_build < first_test


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



def test_duplicate_tool_file_never_creates_empty_shard(tmp_path):
    import ast
    env = runtimes(tmp_path)
    record = tmp_path / 'commands'
    env['RECORD'] = str(record)
    runtime = Path(env['OKO_PYTHON'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nwith Path(os.environ['RECORD']).open('a') as output: output.write(repr(sys.argv) + '\\n')\nsys.exit(0)"))
    name = 'tests/reader/test_frontend_build.py'
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'tools', '--files', name, name],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = [ast.literal_eval(line) for line in record.read_text().splitlines()]
    selected = [command[command.index('-q') + 1:] for command in commands if 'pytest' in command]
    assert selected == [[str(ROOT / name)]]


def test_compact_console_preserves_verbose_output_in_report_log(tmp_path):
    import json
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "print('full tool detail')\nsys.exit(0)"))
    reports = tmp_path / 'reports'
    result = subprocess.run([sys.executable, str(ROOT/'scripts/check'), 'syntax', '--compact', '--report-dir', str(reports)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'full tool detail' not in result.stdout
    assert 'PASS syntax' in result.stdout
    report = json.loads(next(reports.glob('*/report.json')).read_text())
    assert 'full tool detail' in Path(report['log']).read_text()


@pytest.mark.parametrize('changed,expected',[
    ('tests/support/telegram_web.py',{'test_browser_host.py'}),
    ('scripts/check-report',{'test_check_report.py'}),
    ('scripts/task_changes.py',None),
    ('tests/support/build_tools.py',{'test_frontend_builder.py','test_build_speed.py','test_governance.py'}),
    ('scripts/unclassified-tool',None),
])
def test_cli_selects_affected_tool_families_and_falls_back_for_unknown_inputs(tmp_path,changed,expected):
    import json
    import shutil
    checkout=tmp_path/'selection';checkout.mkdir()
    (checkout/'scripts').mkdir()
    shutil.copy2(REPO/'scripts/check',checkout/'scripts/check')
    # The CLI executes synthetic runtimes; only Git selection and reporting are real.
    tests=__import__('runpy').run_path(str(REPO/'scripts/check'))['TOOL_TESTS']
    for name in tests:
        p=checkout/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('')
    def git(*args):return subprocess.check_output(['git',*args],cwd=checkout,text=True).strip()
    git('init','-q');git('config','user.name','Test');git('config','user.email','test@example.test')
    git('add','.');git('commit','-qm','base');base=git('rev-parse','HEAD')
    p=checkout/changed;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# changed\n')
    env=runtimes(tmp_path);(tmp_path/'git').unlink()
    result=subprocess.run([sys.executable,str(checkout/'scripts/check'),'tools','--if-changed','--base',base,'--report-dir',str(tmp_path/'reports')],env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads(next((tmp_path/'reports').glob('*/report.json')).read_text())
    selected={Path(arg).name for command in report['suites']['tool-tests']['commands'] for arg in command['command'][4:] if arg.endswith('.py')}
    assert selected==(expected or {Path(p).name for p in tests})


@pytest.mark.parametrize('failure',[False,True])
def test_pytest_cli_reports_test_and_file_timings(tmp_path,failure):
    import json
    import shutil
    checkout=tmp_path/'timing';(checkout/'scripts').mkdir(parents=True)
    shutil.copy2(REPO/'scripts/check',checkout/'scripts/check')
    (checkout/'tests').mkdir()
    subprocess.run(['git','init','-q'],cwd=checkout,check=True)
    (checkout/'.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (checkout/'scripts/check_test_contracts.py').write_text('pass\n')
    (checkout/'tests/test_first.py').write_text('def test_fast(): '+('assert False' if failure else 'pass')+'\n')
    (checkout/'tests/test_second.py').write_text('def test_other(): pass\n')
    result=subprocess.run([sys.executable,str(checkout/'scripts/check'),'python','--files','tests/test_first.py','tests/test_second.py','--report-dir',str(tmp_path/'reports')],env={**os.environ,'OKO_PYTHON':sys.executable},capture_output=True,text=True,timeout=15)
    assert result.returncode==int(failure),result.stdout+result.stderr
    report=json.loads(next((tmp_path/'reports').glob('*/report.json')).read_text())
    suite=report['suites']['python-tests']
    assert {entry['file'] for entry in suite['test_durations']}=={'tests/test_first.py','tests/test_second.py'}
    assert {entry['name'] for entry in suite['test_durations']}=={'test_fast','test_other'}
    assert all(entry['seconds']>=0 for entry in suite['test_durations'])
    assert suite['duration_seconds']>=max(suite['worker_durations'].values())


def test_worker_killed_without_report_keeps_measured_elapsed(tmp_path):
    import json
    env=runtimes(tmp_path)
    runtime=Path(env['OKO_PYTHON'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "if 'pytest' in sys.argv:\n    import signal,time\n    time.sleep(.15)\n    os.kill(os.getppid(),signal.SIGKILL)\nsys.exit(0)"))
    result=subprocess.run([sys.executable,str(ROOT/'scripts/check'),'python','--report-dir',str(tmp_path/'reports')],env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode==1
    report=json.loads(next((tmp_path/'reports').glob('*/report.json')).read_text())
    suite=report['suites']['python-tests']
    assert suite['status'].startswith('FAIL')
    assert suite['duration_seconds']>=.15
    assert all(seconds>=.15 for seconds in suite['worker_durations'].values())


@pytest.mark.parametrize('cpus, override, expected', [(None, None, 4), (4, None, 4), (12, None, 8), (12, 2, 2), (4, 8, 8)])
def test_browser_worker_budget_respects_host_and_explicit_override(tmp_path, monkeypatch, cpus, override, expected):
    monkeypatch.setattr(os, 'cpu_count', lambda: cpus)
    env = runtimes(tmp_path)
    record = tmp_path / 'commands'
    env['RECORD'] = str(record)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nwith Path(os.environ['RECORD']).open('a') as output: output.write(repr(sys.argv) + '\\n')\nsys.exit(0)"))
    result = run_check_in_process([sys.executable, str(ROOT / 'scripts/check'), 'browser',
                                  *(['--browser-workers', str(override)] if override else []),
                                  '--files', 'tests/browser/test_material_list.ts'],
                                 env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'--test-concurrency={expected}' in record.read_text()
