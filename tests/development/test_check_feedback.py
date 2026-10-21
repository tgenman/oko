"""A failed shard is visible before another shard is released."""
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]


def test_compact_runner_reports_failure_before_other_shard_finishes(tmp_path):
    root=tmp_path/'checkout'
    (root/'scripts').mkdir(parents=True)
    for name in ('check','pytest_events.py'):
        source=REPO/'scripts'/name
        if source.exists():shutil.copy2(source,root/'scripts'/name)
    (root/'tests').mkdir()
    release=tmp_path/'release'
    finished=tmp_path/'finished'
    (root/'tests/test_fast.py').write_text("def test_failure():\n    assert False, 'expected failure detail'\n")
    (root/'tests/test_hold.py').write_text("import time\nfrom pathlib import Path\ndef test_wait_for_release():\n    while not Path("+repr(str(release))+").exists(): time.sleep(.01)\n    Path("+repr(str(finished))+").touch()\n")
    (root/'.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    python=tmp_path/'python'
    python.write_text('#!'+sys.executable+'\nimport os,sys\nif any("check_test_contracts.py" in p for p in sys.argv):sys.exit(0)\nos.execv('+repr(sys.executable)+',['+repr(sys.executable)+',*sys.argv[1:]])\n')
    python.chmod(0o755)
    env=dict(os.environ,CI='true',OKO_PYTHON=str(python))
    process=subprocess.Popen([sys.executable,str(root/'scripts/check'),'python','--compact','--files','tests/test_fast.py','tests/test_hold.py'],cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
    captured=b''
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and b'FAIL NOW' not in captured:
            if select.select([process.stdout],[],[],.1)[0]:
                captured+=os.read(process.stdout.fileno(),65536)
            if process.poll() is not None:break
        assert b'FAIL NOW' in captured,captured.decode()
        assert b'tests/test_fast.py::test_failure' in captured
        assert b'expected failure detail' in captured
        assert b'Retry:' in captured and b'Log:' in captured
        assert not finished.exists()
        release.touch()
        rest=process.communicate(timeout=15)[0]
        assert process.returncode==1,(captured+rest).decode()
        assert (captured+rest).count(b'FAIL NOW')==1
        report=next((root/'.scratch/check-runs').glob('*/report.json'))
        data=json.loads(report.read_text())
        assert data['suites']['python-tests']['failures']==['tests/test_fast.py::test_failure']
        assert finished.exists()
        assert data['checkout']['status']=='PASS'
        details=data['suites']['python-tests']['failure_details']
        assert len(details)==1
        assert '/parallel/' in details[0]['log']
    finally:
        release.touch()
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        process.communicate(timeout=15)


def test_browser_failure_in_parallel_has_reason_and_scoped_retry(tmp_path):
    root=tmp_path/'checkout'
    (root/'scripts').mkdir(parents=True)
    for name in ('check','pytest_events.py'):
        shutil.copy2(REPO/'scripts'/name,root/'scripts'/name)
    (root/'tests/browser').mkdir(parents=True)
    (root/'tests/browser/test_feedback.ts').write_text('')
    release=tmp_path/'release'
    (root/'tests/test_hold.py').write_text("import time\nfrom pathlib import Path\ndef test_wait():\n    while not Path("+repr(str(release))+").exists():time.sleep(.01)\n")
    (root/'.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    python=tmp_path/'python'
    python.write_text('#!'+sys.executable+'\nimport os,sys\nif any("check_test_contracts.py" in p for p in sys.argv):sys.exit(0)\nos.execv('+repr(sys.executable)+',['+repr(sys.executable)+',*sys.argv[1:]])\n')
    python.chmod(0o755)
    node=tmp_path/'node'
    node.write_text('#!'+sys.executable+'''\nimport os,sys
if '-e' in sys.argv:print(sys.executable)
if '--test' in sys.argv:
    print('not ok 1 - browser scenario',flush=True)
    print('  location: "'+os.getcwd()+'/tests/browser/test_feedback.ts:1:1"',flush=True)
    print('  error: |-',flush=True)
    print('    expected browser detail',flush=True)
    print('  ...',flush=True)
    print('# pass 0',flush=True)
    sys.exit(1)
''')
    node.chmod(0o755)
    env=dict(os.environ,CI='true',OKO_PYTHON=str(python),OKO_NODE=str(node),OKO_BROWSER_EXECUTABLE=sys.executable)
    process=subprocess.Popen([sys.executable,str(root/'scripts/check'),'browser','python','--compact','--files','tests/browser/test_feedback.ts','tests/test_hold.py'],cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
    output=b''
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and b'Retry:' not in output:
            if select.select([process.stdout],[],[],.1)[0]:output+=os.read(process.stdout.fileno(),65536)
            if process.poll() is not None:break
        assert b'FAIL NOW browser scenario' in output,output.decode()
        assert b'expected browser detail' in output
        assert b'--files tests/browser/test_feedback.ts --match' in output
        release.touch()
        process.communicate(timeout=15)
        assert process.returncode==1
        data=json.loads(next((root/'.scratch/check-runs').glob('*/report.json')).read_text())
        assert data['suites']['python-tests']['status'].startswith('PASS')
        assert data['suites']['browser']['failure_details'][0]['reason']=='expected browser detail'
    finally:
        release.touch()
        if process.poll() is None:process.send_signal(signal.SIGINT)
        process.communicate(timeout=15)


def test_http_failure_retry_selects_the_python_scenario(tmp_path):
    root=tmp_path/'checkout'
    (root/'scripts').mkdir(parents=True)
    for name in ('check','pytest_events.py'):
        shutil.copy2(REPO/'scripts'/name,root/'scripts'/name)
    (root/'tests/reader').mkdir(parents=True)
    (root/'tests/reader/test_web_assets.py').write_text("def test_assets():\n    assert False, 'HTTP fixture failure'\n")
    for name in ('test_frontend_build.py','test_frontend_delivery.py','test_http_contracts.py'):
        (root/'tests/reader'/name).write_text('')
    (root/'.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    result=subprocess.run([sys.executable,str(root/'scripts/check'),'http','--compact'],cwd=root,env=dict(os.environ,CI='true',OKO_PYTHON=sys.executable),text=True,capture_output=True)
    assert result.returncode==1,result.stdout+result.stderr
    assert 'python --files tests/reader/test_web_assets.py::test_assets' in result.stdout
    assert 'browser --match' not in result.stdout
