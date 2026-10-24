"""One CI plan covers the full inventory; a missing or incomplete shard fails the gate."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]


def invoke(root,*args):
    return subprocess.run([sys.executable,str(root/'scripts/ci_shards.py'),*map(str,args)],cwd=root,text=True,capture_output=True)


@pytest.fixture
def checkout(tmp_path):
    root=tmp_path/'checkout';(root/'scripts').mkdir(parents=True)
    for name in ('check','ci_shards.py','pytest_events.py'):
        source=ROOT/'scripts'/name
        if source.exists():shutil.copy2(source,root/'scripts'/name)
    (root/'scripts/check_test_contracts.py').write_text('pass\n')
    for i in range(8):
        p=root/f'tests/reader/test_{i}.py';p.parent.mkdir(parents=True,exist_ok=True);p.write_text('def test_case(): pass\n')
    for name in ('test_a.ts','test_b.ts','test_c.ts','test_d.ts','test_reading_performance_http.ts'):
        p=root/'tests/browser'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('// synthetic inventory\n')
    p=root/'tests/node/test_owner.ts';p.parent.mkdir(parents=True);p.write_text('// synthetic node\n')
    (root/'.gitignore').write_text('__pycache__/\n.pytest_cache/\n.scratch/\n')
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.test','commit','-qm','base'],check=True)
    return root


def plan(root,tmp_path):
    path=tmp_path/'plan.json'
    result=invoke(root,'plan','--output',path,'--browser-workers','4')
    assert result.returncode==0,result.stdout+result.stderr
    return path,json.loads(path.read_text())


def test_plan_partitions_inventory_deterministically_and_keeps_latency_alone(checkout,tmp_path):
    path,data=plan(checkout,tmp_path)
    again=tmp_path/'again.json';assert invoke(checkout,'plan','--output',again,'--browser-workers','4').returncode==0
    assert json.loads(again.read_text())==data
    assert set(data['jobs'])=={'python-1','python-2','browser-1','browser-2','latency'}
    for suite,prefix in [('python-tests','python'),('browser','browser')]:
        files=[name for key,j in data['jobs'].items() if key.startswith(prefix+'-') for name in j['files']]
        assert len(files)==len(set(files)) and sorted(files)==data['inventory'][suite]
    assert data['jobs']['latency']['files']==['tests/browser/test_reading_performance_http.ts']
    assert all(j['files'] for j in data['jobs'].values())
    assert data['browser_workers']==4 and path.is_file()


def test_actual_python_shards_run_all_cases_once_and_do_not_learn_partial_history(checkout,tmp_path):
    measured_hints(checkout)
    path,data=plan(checkout,tmp_path);seen=[]
    for job in ('python-1','python-2'):
        reportdir=tmp_path/job
        result=subprocess.run([sys.executable,str(checkout/'scripts/check'),'python','--ci-plan',str(path),'--ci-job',job,'--browser-workers','4','--compact','--report-dir',str(reportdir)],cwd=checkout,env={**os.environ,'OKO_PYTHON':sys.executable},text=True,capture_output=True,timeout=25)
        assert result.returncode==0,result.stdout+result.stderr
        report=json.loads(next(reportdir.glob('*/report.json')).read_text())
        assert report['ci_shard']=={'plan_id':data['id'],'job':job}
        assert report['suites']['python-tests']['counts']['passed']==len(data['jobs'][job]['files'])
        seen.extend(report['suites']['python-tests']['file_durations'])
        if len(data['jobs'][job]['files'])>1:
            assert report['timing_plan']['python-tests']['source']=='durations'
        assert not report.get('timing_history',{}).get('updated')
    assert sorted(seen)==data['inventory']['python-tests']


def fake_reports(root,output,data):
    for job,j in data['jobs'].items():
        group=j['group'];files=j['files']
        record=dict(exit_code=0,checkout=dict(status='PASS',changed_files=[]),ci_shard=dict(plan_id=data['id'],job=job),
            selected_files={group:[str(root/name) for name in files]},
            suites={group:dict(status='PASS',file_durations={name:1. for name in files},counts=dict(total=4 if job=='latency' else len(files),failed=0,errors=0,skipped=0))})
        target=output/('check-reports-'+job)/'run/report.json';target.parent.mkdir(parents=True);target.write_text(json.dumps(record))


def test_gate_requires_all_files_and_only_then_updates_history(checkout,tmp_path):
    path,data=plan(checkout,tmp_path);reports=tmp_path/'reports';fake_reports(checkout,reports,data)
    history=tmp_path/'timings.json'
    result=invoke(checkout,'verify','--plan',path,'--reports',reports,'--timings',history)
    assert result.returncode==0,result.stdout+result.stderr
    saved=history.read_bytes()
    record=next((reports/'check-reports-python-1').glob('*/report.json'))
    bad=json.loads(record.read_text());bad['suites']['python-tests']['file_durations'].pop(next(iter(bad['suites']['python-tests']['file_durations'])))
    record.write_text(json.dumps(bad))
    result=invoke(checkout,'verify','--plan',path,'--reports',reports,'--timings',history)
    assert result.returncode!=0 and history.read_bytes()==saved


@pytest.mark.parametrize('problem',['missing','duplicate','failed','dirty','wrong_plan','zero_tests','latency_count'])
def test_gate_rejects_incomplete_or_foreign_evidence(checkout,tmp_path,problem):
    path,data=plan(checkout,tmp_path);reports=tmp_path/'reports';fake_reports(checkout,reports,data)
    job='latency' if problem=='latency_count' else 'python-1'
    record=next((reports/('check-reports-'+job)).glob('*/report.json'));value=json.loads(record.read_text())
    if problem=='missing':record.unlink()
    elif problem=='duplicate':shutil.copytree(record.parent,record.parent.with_name('duplicate'))
    else:
        if problem=='failed':value['exit_code']=1
        if problem=='dirty':value['checkout']['status']='FAIL'
        if problem=='wrong_plan':value['ci_shard']['plan_id']='foreign'
        if problem in ('zero_tests','latency_count'):value['suites'][data['jobs'][job]['group']]['counts']['total']=0
        record.write_text(json.dumps(value))
    history=tmp_path/'timings.json'
    assert invoke(checkout,'verify','--plan',path,'--reports',reports,'--timings',history).returncode!=0
    assert not history.exists()


def test_runner_rejects_modified_inventory_and_case_filters(checkout,tmp_path):
    path,_=plan(checkout,tmp_path)
    command=[sys.executable,str(checkout/'scripts/check'),'python','--ci-plan',str(path),'--ci-job','python-1','--browser-workers','4']
    for extra in (['--match','case'],['--files','tests/reader/test_0.py']):
        result=subprocess.run([*command,*extra],cwd=checkout,capture_output=True,text=True)
        assert result.returncode!=0 and 'Traceback' not in result.stderr
    result=subprocess.run(command,cwd=checkout,env={**os.environ,'PYTEST_ADDOPTS':'-k missing'},capture_output=True,text=True)
    assert result.returncode!=0
    (checkout/'tests/reader/test_0.py').write_text('def test_changed(): pass\n')
    assert subprocess.run(command,cwd=checkout,capture_output=True,text=True).returncode!=0


def test_corrupt_hints_do_not_change_coverage(checkout,tmp_path):
    hints=checkout/'.scratch/check-cache/ci-timings.json';hints.parent.mkdir(parents=True);hints.write_text('[]')
    _,data=plan(checkout,tmp_path)
    assert data['balance']['python-tests']['source']=='file_sizes'
    assert len(data['inventory']['python-tests'])==8


def measured_hints(root):
    import runpy
    scope=runpy.run_path(str(root/'scripts/check'))
    history=scope['TimingHistory'](root/'.scratch/check-cache/ci-timings.json',4)
    history.path.parent.mkdir(parents=True,exist_ok=True)
    costs={f'tests/reader/test_{i}.py':[90 if i==0 else 80 if i==4 else 1] for i in range(8)}
    history.path.write_text(json.dumps(dict(version=1,context=history.context,suites={'python-tests':costs})))


def test_plan_balances_measured_costs_instead_of_equal_sizes(checkout,tmp_path):
    measured_hints(checkout)
    _,data=plan(checkout,tmp_path)
    assert data['balance']['python-tests']['source']=='durations'
    assert data['balance']['python-tests']['known_files']==8
    assert not any({'tests/reader/test_0.py','tests/reader/test_4.py'}<=set(j['files']) for k,j in data['jobs'].items() if k.startswith('python-'))


@pytest.mark.parametrize('mutation',['missing_file','duplicate_file','empty_shard','latency_duplicate','revision'])
def test_even_resigned_plan_cannot_change_coverage(checkout,tmp_path,mutation):
    import hashlib
    path,data=plan(checkout,tmp_path)
    if mutation=='missing_file':data['jobs']['python-1']['files'].pop()
    if mutation=='duplicate_file':data['jobs']['python-1']['files'].append(data['jobs']['python-2']['files'][0])
    if mutation=='empty_shard':data['jobs']['python-1']['files']=[]
    if mutation=='latency_duplicate':data['jobs']['browser-1']['files'].append(data['jobs']['latency']['files'][0])
    if mutation=='revision':data['revision']='0'*40
    data['id']=hashlib.sha256(json.dumps({k:v for k,v in data.items() if k!='id'},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    path.write_text(json.dumps(data))
    result=subprocess.run([sys.executable,str(checkout/'scripts/check'),'python','--ci-plan',str(path),'--ci-job','python-1','--browser-workers','4'],cwd=checkout,capture_output=True,text=True)
    assert result.returncode!=0 and 'Traceback' not in result.stderr
    assert not (checkout/'.scratch/check-runs').exists()
