"""Timing hints change scheduling, never the inventory or success decision."""
import json
from pathlib import Path
import runpy
import pytest

ROOT=Path(__file__).resolve().parents[2]


def runner():return runpy.run_path(str(ROOT/'scripts/check'))


def test_measured_schedule_covers_each_file_once_with_missing_and_invalid_hints(tmp_path):
    scope=runner();files=[]
    for i in range(6):
        name=f'tests/test_{i}.py';path=tmp_path/name;path.parent.mkdir(exist_ok=True);path.write_text('#'*(i+1)*100);files.append(name)
    hints={files[0]:20,files[1]:20,files[2]:float('nan'),files[3]:-2,files[4]:1e-300}
    buckets,plan=scope['timed_buckets'](files,4,tmp_path,hints)
    assert sorted(x for bucket in buckets for x in bucket)==files
    assert all(buckets) and len(buckets)==4
    full={name:(20 if name in files[:2] else 1) for name in files}
    measured,_=scope['timed_buckets'](files,4,tmp_path,full)
    assert not any(files[0] in b and files[1] in b for b in measured)
    assert plan['source']=='durations' and plan['known_files']==3
    fallback,plan=scope['timed_buckets'](files,4,tmp_path,{})
    assert sorted(x for b in fallback for x in b)==files and all(fallback)
    assert plan['source']=='file_sizes'


def test_history_uses_only_successful_clean_reports_and_median_of_three(tmp_path):
    history=runner()['TimingHistory'](tmp_path/'history.json',8)
    def report(cost,exit=0,checkout='PASS'):
        return dict(exit_code=exit,checkout=dict(status=checkout),suites={'python-tests':dict(status='PASS',file_durations={'tests/test_a.py':cost})})
    for cost in (1,100,3,5):assert history.learn(report(cost))
    assert history.estimates()['python-tests']['tests/test_a.py']==5
    assert not history.learn(report(999,exit=1))
    assert not history.learn(report(999,checkout='FAIL'))
    assert history.estimates()['python-tests']['tests/test_a.py']==5
    assert runner()['TimingHistory'](history.path,4).estimates()=={}


def test_real_runner_uses_history_and_does_not_learn_failures_or_partial_cases(tmp_path):
    import shutil,subprocess,sys,os
    checkout=tmp_path/'checkout';(checkout/'scripts').mkdir(parents=True);(checkout/'tests').mkdir()
    shutil.copy2(ROOT/'scripts/check',checkout/'scripts/check')
    (checkout/'scripts/check_test_contracts.py').write_text('pass\n')
    (checkout/'.gitignore').write_text('__pycache__/\n.pytest_cache/\n.scratch/\n')
    subprocess.run(['git','init','-q'],cwd=checkout,check=True)
    files=[f'tests/test_cost_{i}.py' for i in range(6)]
    for name in files:(checkout/name).write_text('def test_case(): pass\n')
    history=runner()['TimingHistory'](checkout/'.scratch/check-cache/test-timings/python-tests.json',4)
    hints={name:(20 if i in (0,4) else 1) for i,name in enumerate(files)}
    assert history.learn(dict(exit_code=0,checkout=dict(status='PASS'),suites={'python-tests':dict(status='PASS',file_durations=hints)}))
    def check(*args):
        command=[sys.executable,str(checkout/'scripts/check'),'python','--browser-workers','4','--report-dir',str(tmp_path/'reports'),*args]
        result=subprocess.run(command,cwd=checkout,env={**os.environ,'OKO_PYTHON':sys.executable},text=True,capture_output=True,timeout=20)
        report=json.loads(sorted((tmp_path/'reports').glob('*/report.json'))[-1].read_text())
        return result,report
    result,report=check()
    assert result.returncode==0,result.stdout+result.stderr
    suite=report['suites']['python-tests'];assert suite['counts']['passed']==6
    commands=[row['command'] for row in suite['commands']]
    assert len(commands)==4 and not any(any(files[0] in arg for arg in row) and any(files[4] in arg for arg in row) for row in commands)
    assert report['timing_plan']['python-tests']['source']=='durations' and report['timing_history']['updated']
    saved=history.path.read_bytes()
    result,report=check('--files',files[0]+'::test_case')
    assert result.returncode==0 and history.path.read_bytes()==saved
    (checkout/files[0]).write_text('def test_case(): assert False\n')
    result,report=check()
    assert result.returncode==1 and history.path.read_bytes()==saved
