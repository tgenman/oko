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


@pytest.mark.parametrize('data',['bad json','[]','{"version":99}', '{"version":1,"context":null}', 'null'])
def test_corrupted_or_foreign_history_is_only_a_cache_miss(tmp_path,data):
    path=tmp_path/'history.json';path.write_text(data)
    assert runner()['TimingHistory'](path,8).estimates()=={}


def test_timing_history_does_not_follow_a_symlink(tmp_path):
    target=tmp_path/'unrelated';target.write_text('original')
    path=tmp_path/'history.json';path.symlink_to(target)
    history=runner()['TimingHistory'](path,8)
    assert history.estimates()=={}
    history.learn(dict(exit_code=0,checkout=dict(status='PASS'),suites={}))
    assert target.read_text()=='original'


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


def test_ci_keeps_required_groups_with_seven_jobs_and_optional_timing_cache():
    import yaml
    document=yaml.safe_load((ROOT/'.github/workflows/check.yml').read_text())
    assert set(document['jobs'])=={'build','tests','postgres','check'}
    tests=document['jobs']['tests']
    entries=tests['strategy']['matrix']['include']
    assert {e['job']:e['suite'] for e in entries}=={'python-1':'python','python-2':'python','browser-1':'browser','browser-2':'browser','latency':'browser','node':'node','tools':'tools'}
    assert tests['strategy']['max-parallel']==7 and tests['strategy']['fail-fast'] is False
    assert sum(e['sharded'] for e in entries)==5
    caches=[step for step in tests['steps'] if str(step.get('uses','')).startswith('actions/cache@')]
    assert len(caches)==1 and caches[0]['continue-on-error'] is True
    assert caches[0]['with']['path']=='.scratch/check-cache/test-timings'
    assert document['jobs']['check']['needs']==['build','tests','postgres']
    postgres=document['jobs']['postgres']
    assert postgres['services']['postgres']['image']=='postgres:18-bookworm'
    integration=next(step for step in postgres['steps'] if 'test_postgres_migrations.py' in step.get('run',''))
    assert integration['env']['OKO_TEST_POSTGRES_URL'].startswith('postgresql://')
    gate=document['jobs']['check']['steps']
    required=next(step for step in gate if step.get('name')=='Require every check group to succeed')
    assert required['env']['POSTGRES_RESULT']=='${{ needs.postgres.result }}'
    assert 'test "$POSTGRES_RESULT" = success' in required['run']
    assert any('ci_shards.py verify' in step.get('run','') for step in gate)
    audit=next(i for i,step in enumerate(gate) if 'ci_shards.py verify' in step.get('run',''))
    assert all(i>audit and step['continue-on-error'] for i,step in enumerate(gate) if 'actions/cache/save@' in step.get('uses',''))


def test_invalid_numeric_hints_cannot_abort_or_empty_the_schedule(tmp_path):
    scope=runner();history=scope['TimingHistory'](tmp_path/'history.json',8)
    files=['tests/test_a.py','tests/test_b.py']
    for name in files:
        path=tmp_path/name;path.parent.mkdir(exist_ok=True);path.write_text('pass\n')
    huge=10**400
    history.path.write_text(json.dumps(dict(version=1,context=history.context,suites={'python-tests':{files[0]:[huge],files[1]:[True,-1,float('inf')]}})))
    history=scope['TimingHistory'](history.path,8)
    assert not history.estimates().get('python-tests')
    for value in (huge,True,-1,float('inf'),float('nan'),1e-300):
        buckets,_=scope['timed_buckets'](files,2,tmp_path,{files[0]:value})
        assert all(buckets) and sorted(x for b in buckets for x in b)==files


def test_standalone_and_full_runs_have_separate_advisory_histories(tmp_path):
    scope=runner();path=scope['timing_history_path']
    full=path(tmp_path,['browser','python-tests','tool-tests','node-tests'])
    alone=path(tmp_path,['python-tests'])
    assert full!=alone and path(tmp_path,['node-tests','tool-tests','python-tests','browser'])==full
    def record(seconds):return dict(exit_code=0,checkout=dict(status='PASS'),suites={'python-tests':dict(status='PASS',file_durations={'tests/test_a.py':seconds})})
    assert scope['TimingHistory'](full,8).learn(record(20))
    assert scope['TimingHistory'](alone,8).learn(record(1))
    assert scope['TimingHistory'](full,8).estimates()['python-tests']['tests/test_a.py']==20
