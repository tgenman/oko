"""Check the developer command through its process interface and external runtimes."""
import os
from pathlib import Path
import subprocess
import sys

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
