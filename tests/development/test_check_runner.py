"""Check the developer command through its process interface and external runtimes."""
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]


def runtimes(tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.write_text('''#!/usr/bin/env python3
import os, sys
if "-e" in sys.argv:
    print(os.environ["OKO_BROWSER_EXECUTABLE"])
sys.exit(0)
''')
    runtime.chmod(0o755)
    (tmp_path / 'git').symlink_to(runtime)
    return {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'], 'OKO_PYTHON': str(runtime), 'OKO_NODE': str(runtime),
            'OKO_BROWSER_EXECUTABLE': sys.executable}


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
    result = subprocess.run([sys.executable, str(checkout / 'scripts/check'), 'syntax'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(nested) in record.read_text()


def test_failed_browser_does_not_hide_other_results(tmp_path):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "sys.exit(7 if '--test' in sys.argv else 0)"))
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'all'],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'FAIL browser' in result.stdout
    assert 'PASS python-tests' in result.stdout
    assert 'PASS entrypoint' in result.stdout


def test_missing_runtime_reports_unexecuted_checks(tmp_path):
    env = runtimes(tmp_path)
    env['OKO_NODE'] = str(tmp_path / 'missing-node')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'ui'],
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
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'all'],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Browser dependencies unavailable' in result.stderr
    assert 'NOT RUN python-tests' in result.stdout
    assert 'PASS' not in result.stdout


def test_ui_checks_production_http_modules_before_browser(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'ui'],
                            env=runtimes(tmp_path), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.index('RUN http') < result.stdout.index('RUN browser')
    assert 'PASS http' in result.stdout


def test_python_and_types_do_not_require_node(tmp_path):
    env = runtimes(tmp_path)
    env['OKO_NODE'] = str(tmp_path / 'absent-node')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'python', 'types'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS python-tests' in result.stdout and 'PASS types' in result.stdout
    assert 'RUN browser' not in result.stdout and 'RUN syntax' not in result.stdout


def test_failed_http_module_check_fails_ui(tmp_path):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_PYTHON'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "sys.exit(7 if 'tests/reader/test_web_assets.py' in sys.argv else 0)"))
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'ui'],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'FAIL http' in result.stdout


def test_browser_file_selection_only_runs_requested_file(tmp_path):
    env = runtimes(tmp_path)
    record = tmp_path / 'commands'
    env['RECORD'] = str(record)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nwith Path(os.environ['RECORD']).open('a') as f: f.write(repr(sys.argv) + '\\n')\nsys.exit(0)"))
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'browser', '--files', 'tests/browser/test_material_list.cjs'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = record.read_text()
    assert '--test-concurrency=2' in commands
    assert 'test_material_list.cjs' in commands
    assert 'test_opened_material.cjs' not in commands
    assert 'PASS browser' in result.stdout


def test_invalid_file_selection_fails_before_checks(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'browser', '--files', 'tests/browser/missing.cjs'], env=runtimes(tmp_path), capture_output=True, text=True)
    assert result.returncode != 0
    assert 'RUN browser' not in result.stdout


def test_python_file_selection_supports_test_id_and_does_not_require_node(tmp_path):
    env = runtimes(tmp_path)
    env['OKO_NODE'] = str(tmp_path / 'missing-node')
    record = tmp_path / 'commands'
    env['RECORD'] = str(record)
    runtime = Path(env['OKO_PYTHON'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nwith Path(os.environ['RECORD']).open('a') as f: f.write(repr(sys.argv) + '\\n')\nsys.exit(0)"))
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'python', '--files', 'tests/reader/test_web_assets.py::test_reader_serves_entire_browser_module_graph_without_exposing_reader_data'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'test_web_assets.py::test_reader_serves_entire_browser_module_graph_without_exposing_reader_data' in record.read_text()
    assert 'PASS python-tests' in result.stdout


def test_selection_rejects_file_from_wrong_suite_and_outside_checkout(tmp_path):
    env = runtimes(tmp_path)
    for filename in ('tests/reader/test_web_assets.py', str(tmp_path / 'test_external.cjs')):
        if filename.startswith(str(tmp_path)):
            Path(filename).write_text('')
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'browser', '--files', filename], env=env, capture_output=True, text=True)
        assert result.returncode == 2
        assert 'RUN browser' not in result.stdout


def test_browser_gets_resolved_python_in_checkout_without_virtualenv(tmp_path):
    import shutil
    checkout = tmp_path / 'checkout'
    (checkout / 'scripts').mkdir(parents=True)
    (checkout / 'tests/browser').mkdir(parents=True)
    shutil.copy(ROOT / 'scripts/check', checkout / 'scripts/check')
    (checkout / 'tests/browser/test_reader_http.cjs').write_text('')
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
    print('FAILED tests/example.py::test_example - assertion')
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
    assert report['suites']['python-tests']['failures'] == ['tests/example.py::test_example', 'tests/example.py::test_setup']
    assert report['suites']['browser']['commands'][0]['exit_code'] == 7
    assert report['suites']['types']['status'].startswith('PASS')
    log = (first / 'output.log').read_text()
    assert 'x' * 100000 in log and 'stderr diagnostic' in log
    first_json = (first / 'report.json').read_text()
    runtime.write_text('#!/usr/bin/env python3\nimport os, sys\nif \"-e\" in sys.argv: print(os.environ[\"OKO_BROWSER_EXECUTABLE\"])\nsys.exit(0)\n')
    retry = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'browser', '--files', 'tests/browser/test_material_list.cjs', '--report-dir', str(reports)], env=env, capture_output=True, text=True)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert (first / 'report.json').read_text() == first_json
    second = next(path for path in reports.iterdir() if path != first)
    rerun = json.loads((second / 'report.json').read_text())
    assert list(rerun['suites']) == ['browser']
    assert rerun['selected_files']['browser'] == [str(ROOT / 'tests/browser/test_material_list.cjs')]


def test_report_records_missing_dependency_without_claiming_success(tmp_path):
    import json
    env = runtimes(tmp_path)
    env['OKO_NODE'] = str(tmp_path / 'missing-node')
    reports = tmp_path / 'reports'
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'ui', '--report-dir', str(reports)], env=env, capture_output=True, text=True)
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
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nwith Path(os.environ['RECORD']).open('a') as output: output.write(repr(sys.argv) + '\\n')\nif '--test' in sys.argv: print('# pass 1')\nsys.exit(0)"))
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'browser', '--match', 'late.*draft', '--files', 'tests/browser/test_settings_navigation.cjs', '--report-dir', str(tmp_path / 'reports')], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '--test-name-pattern=late.*draft' in record.read_text()
    report = json.loads((next((tmp_path / 'reports').iterdir()) / 'report.json').read_text())
    assert report['browser_name_pattern'] == 'late.*draft'


def test_failed_lint_skips_browser_but_keeps_other_results(tmp_path):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "sys.exit(7 if any('lint-js.cjs' in value for value in sys.argv) else 0)"))
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'ui'], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert 'FAIL lint' in result.stdout and 'NOT RUN browser' in result.stdout
    assert '\nRUN browser\n' not in result.stdout
    assert 'PASS diff' in result.stdout


def test_browser_filter_matching_nothing_is_not_success(tmp_path):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "if '--test' in sys.argv: print('# pass 0')\nsys.exit(0)"))
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'browser', '--match', 'missing scenario'], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert 'No browser scenarios matched' in result.stdout


def test_browser_artifacts_are_scoped_to_run_and_checkout_mutation_fails(tmp_path):
    import json
    import shutil
    checkout = tmp_path / 'checkout'
    (checkout / 'scripts').mkdir(parents=True)
    (checkout / 'tests/browser').mkdir(parents=True)
    shutil.copy(ROOT / 'scripts/check', checkout / 'scripts/check')
    tracked = checkout / 'tests/browser/test_example.cjs'
    tracked.write_text('original\n')
    subprocess.run(['git','init','-q',str(checkout)],check=True)
    subprocess.run(['git','-C',str(checkout),'add','tests/browser/test_example.cjs'],check=True)
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
    assert 'Tests changed checkout files' in result.stdout
    assert 'tests/browser/test_example.cjs' in result.stdout
    assert tracked.read_text() == 'changed by test\n'  # The guard reports, it never resets user files.
    directory = next((tmp_path/'reports').iterdir())
    assert Path((tmp_path/'artifact-directory').read_text()) == directory/'artifacts/browser'
    report=json.loads((directory/'report.json').read_text())
    assert report['suites']['browser']['checkout_changes'] == ['tests/browser/test_example.cjs']


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
    assert 'Tests changed checkout files:\n  generated.txt' in second.stdout
    assert tracked.read_text()=='preexisting draft\n'


def test_lifecycle_profile_selects_dedicated_scenarios(tmp_path):
    env=runtimes(tmp_path);record=tmp_path/'commands';env['RECORD']=str(record)
    runtime=Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "from pathlib import Path\nwith Path(os.environ['RECORD']).open('a') as output: output.write(repr(sys.argv) + '\\n')\nsys.exit(0)"))
    result=subprocess.run([sys.executable,str(ROOT/'scripts/check'),'lifecycle'],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'test_ui_lifecycle.cjs' in record.read_text()
    assert 'test_material_list.cjs' not in record.read_text()


def test_explicit_suite_order_cannot_run_browser_before_selected_lint(tmp_path):
    env=runtimes(tmp_path)
    runtime=Path(env['OKO_NODE'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "sys.exit(7 if any('lint-js.cjs' in value for value in sys.argv) else 0)"))
    result=subprocess.run([sys.executable,str(ROOT/'scripts/check'),'browser','syntax','lint'],env=env,capture_output=True,text=True)
    assert result.returncode==1,result.stdout+result.stderr
    assert '\nRUN browser\n' not in result.stdout
    assert 'PASS syntax' in result.stdout and 'FAIL lint' in result.stdout and 'NOT RUN browser' in result.stdout


@pytest.mark.parametrize('sets', [('all',), ('browser','python','types'), ('python','types')])
def test_types_run_before_long_checks_and_failure_skips_them(tmp_path, sets):
    env = runtimes(tmp_path)
    runtime = Path(env['OKO_PYTHON'])
    runtime.write_text(runtime.read_text().replace('sys.exit(0)', "sys.exit(3 if 'mypy' in sys.argv else 0)"))
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), *sets],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'FAIL types' in result.stdout
    assert '\nRUN python-tests\n' not in result.stdout
    assert 'NOT RUN python-tests' in result.stdout
    if 'browser' in sets or 'all' in sets:
        assert '\nRUN browser\n' not in result.stdout
        assert 'NOT RUN browser' in result.stdout
