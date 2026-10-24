"""Create one file allocation for CI and audit all shard evidence before learning timings."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import runpy
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
TIMINGS=Path('.scratch/check-cache/ci-timings.json')
JOBS={'python-1':'python-tests','python-2':'python-tests','browser-1':'browser','browser-2':'browser','latency':'browser'}
PERFORMANCE='tests/browser/test_reading_performance_http.ts'


def runner(root):
    return runpy.run_path(str(root/'scripts/check'))


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def revision(root):
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()


def inventory(root,tools):
    python=sorted(p.relative_to(root).as_posix() for p in (root/'tests').rglob('*.py')
                  if (p.name.startswith('test_') or p.name.endswith('_test.py')) and p.relative_to(root).as_posix() not in tools)
    browser=sorted(p.relative_to(root).as_posix() for p in (root/'tests/browser').glob('test_*.ts') if p.relative_to(root).as_posix()!=PERFORMANCE)
    if len(python)<2 or len(browser)<2 or not (root/PERFORMANCE).is_file():
        raise ValueError('CI requires two nonempty Python/browser shards and the latency file')
    return {'python-tests':python,'browser':browser,'latency':[PERFORMANCE]}


def source_hashes(root,files):
    result={}
    for name in sorted(set(name for group in files.values() for name in group)):
        path=root/name
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('Test outside checkout or missing: '+name)
        result[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def make_plan(root,scope,workers):
    files=inventory(root,scope['TOOL_TESTS'])
    history=scope['TimingHistory'](root/TIMINGS,workers)
    # Bootstrap from the previous unsharded CI cache. Subsequent runs restore one
    # combined history saved only by the successful aggregate job.
    for group in ('python-tests','browser'):
        if not history.samples.get(group):
            old=scope['TimingHistory'](scope['timing_history_path'](root,[group]),workers)
            history.samples[group]=old.samples.get(group,{})
    hints=history.estimates();jobs={};balance={}
    for group,prefix in (('python-tests','python'),('browser','browser')):
        buckets,balance[group]=scope['timed_buckets'](files[group],2,root,hints.get(group,{}))
        for index,bucket in enumerate(buckets,1):jobs[f'{prefix}-{index}']={'group':group,'files':bucket}
    jobs['latency']={'group':'browser','files':[PERFORMANCE]}
    plan=dict(version=1,revision=revision(root),browser_workers=workers,inventory=files,
              sources=source_hashes(root,files),jobs=jobs,balance=balance,
              history=dict(version=1,context=history.context,suites=history.samples))
    plan['id']=digest(plan)
    return plan


def load_plan(path,root,tools):
    if path.stat().st_size>1024*1024:raise ValueError('CI plan is too large')
    plan=json.loads(path.read_text())
    if not isinstance(plan,dict) or plan.get('version')!=1:raise ValueError('Unsupported CI plan')
    if plan.get('id')!=digest({k:v for k,v in plan.items() if k!='id'}):raise ValueError('CI plan digest differs')
    files=inventory(root,tools)
    if plan.get('revision')!=revision(root) or plan.get('inventory')!=files or plan.get('sources')!=source_hashes(root,files):
        raise ValueError('CI plan does not match the current revision and test inventory')
    if plan.get('browser_workers') not in (2,4,8):raise ValueError('Invalid browser capacity')
    if not isinstance(plan.get('jobs'),dict) or set(plan['jobs'])!=set(JOBS):raise ValueError('Missing or unexpected CI shard')
    for job,group in JOBS.items():
        entry=plan['jobs'][job]
        if not isinstance(entry,dict) or entry.get('group')!=group or not isinstance(entry.get('files'),list) or not entry['files'] or not all(isinstance(n,str) for n in entry['files']):
            raise ValueError('Invalid or empty CI shard: '+job)
    for group,prefix in (('python-tests','python'),('browser','browser')):
        selected=[name for job,entry in plan['jobs'].items() if job.startswith(prefix+'-') for name in entry['files']]
        if sorted(selected)!=files[group]:raise ValueError('Missing or duplicated '+group+' files')
    if plan['jobs']['latency']['files']!=[PERFORMANCE]:raise ValueError('Latency must run exactly once, alone')
    return plan


def verify(plan,root,reports,scope,timings):
    merged={};counts={}
    for job,entry in plan['jobs'].items():
        paths=list((reports/('check-reports-'+job)).glob('*/report.json'))
        if len(paths)!=1:raise ValueError('Expected exactly one report for '+job)
        report=json.loads(paths[0].read_text());group=entry['group']
        if report.get('ci_shard')!={'plan_id':plan['id'],'job':job}:raise ValueError('Foreign shard evidence: '+job)
        if report.get('exit_code')!=0 or report.get('checkout',{}).get('status')!='PASS':raise ValueError('Failed or dirty shard: '+job)
        suite=report.get('suites',{}).get(group,{})
        if not suite.get('status','').startswith('PASS'):raise ValueError('Suite did not pass: '+job)
        observed=suite.get('file_durations',{})
        if not isinstance(observed,dict) or sorted(observed)!=sorted(entry['files']):raise ValueError('Executed files differ from plan: '+job)
        if any(type(v) not in (int,float) or not 0<=v<=86400 or not math.isfinite(v) for v in observed.values()):raise ValueError('Invalid measured duration: '+job)
        selected=report.get('selected_files',{}).get(group,[])
        try:selected=[Path(name).relative_to(root).as_posix() for name in selected]
        except ValueError:raise ValueError('Selected file outside checkout: '+job) from None
        if sorted(selected)!=sorted(entry['files']):raise ValueError('Selected files differ from plan: '+job)
        total=suite.get('counts',{}).get('total')
        if type(total) is not int or total<=0 or any(suite.get('counts',{}).get(k,0) for k in ('failed','errors','cancelled')):raise ValueError('Missing or failed test cases: '+job)
        if job=='latency' and (total!=4 or suite['counts'].get('skipped',0)):raise ValueError('All four latency cases are required')
        files=merged.setdefault(group,{})
        if set(files)&set(observed):raise ValueError('A test file ran in two shards')
        files.update(observed)
        for key,value in suite['counts'].items():counts[key]=counts.get(key,0)+value
    expected=set(plan['inventory']['python-tests']+plan['inventory']['browser']+plan['inventory']['latency'])
    if {name for files in merged.values() for name in files}!=expected:raise ValueError('Incomplete total CI coverage')
    summary=dict(plan_id=plan['id'],jobs=list(plan['jobs']),files=len(expected),counts=counts)
    # Timing data is advisory. An unavailable cache must never override test success.
    if timings is not None:
        try:
            history=scope['TimingHistory'](timings,plan['browser_workers'])
            if plan['history']['context']==history.context:
                if not history.samples:
                    timings.parent.mkdir(parents=True,exist_ok=True)
                    timings.write_text(json.dumps(plan['history'])+'\n')
                history.learn(dict(exit_code=0,checkout=dict(status='PASS'),suites={group:dict(status='PASS',file_durations=files) for group,files in merged.items()}))
        except (OSError,ValueError,TypeError,KeyError) as error:
            summary['timing_warning']=str(error)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    command=commands.add_parser('plan');command.add_argument('--output',type=Path,required=True);command.add_argument('--browser-workers',type=int,choices=(2,4,8),default=4)
    command=commands.add_parser('verify');command.add_argument('--plan',type=Path,required=True);command.add_argument('--reports',type=Path,required=True);command.add_argument('--timings',type=Path)
    args=parser.parse_args()
    try:
        scope=runner(ROOT)
        if args.command=='plan':
            data=make_plan(ROOT,scope,args.browser_workers);args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')
            print(json.dumps(dict(plan_id=data['id'],files={g:len(f) for g,f in data['inventory'].items()},balance=data['balance'])))
        else:
            data=load_plan(args.plan,ROOT,scope['TOOL_TESTS']);print(json.dumps(verify(data,ROOT,args.reports,scope,args.timings)))
        return 0
    except (OSError,ValueError,TypeError,KeyError,AttributeError,subprocess.CalledProcessError) as error:
        print('CI shard check failed: '+str(error),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
