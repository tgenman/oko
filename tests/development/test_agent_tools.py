"""Agent tools preserve exact Git bases and honest CI outcomes."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
PREPARE = ROOT/'scripts/prepare-integration'
CI = runpy.run_path(str(ROOT/'scripts/ci-status'))


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.STDOUT).strip()


def repository(tmp_path):
    remote = tmp_path/'remote.git'
    git(tmp_path, 'init', '--bare', '-q', str(remote))
    source = tmp_path/'source'
    git(tmp_path, 'clone', '-q', str(remote), str(source))
    git(source, 'config', 'user.name', 'Test')
    git(source, 'config', 'user.email', 'test@example.test')
    git(source, 'switch', '-c', 'main')
    (source/'base').write_text('base')
    git(source, 'add', '.')
    git(source, 'commit', '-qm', 'base')
    git(source, 'push', '-q', 'origin', 'main')
    stale = git(source, 'rev-parse', 'HEAD')
    writer = tmp_path/'writer'
    git(tmp_path, 'clone', '-qb', 'main', str(remote), str(writer))
    git(writer, 'config', 'user.name', 'Test')
    git(writer, 'config', 'user.email', 'test@example.test')
    (writer/'base').write_text('new remote main')
    git(writer, 'commit', '-qam', 'remote advance')
    git(writer, 'push', '-q', 'origin', 'main')
    return source, writer, stale


def test_integration_uses_fetched_sha_not_local_main_and_preserves_dirty_tree(tmp_path):
    source, writer, stale = repository(tmp_path)
    git(source, 'switch', '-c', 'codex/source')
    (source/'feature').write_text('feature')
    git(source, 'add', 'feature')
    git(source, 'commit', '-qm', 'feature')
    commit = git(source, 'rev-parse', 'HEAD')
    (source/'base').write_text('unsaved user edit')
    (source/'untracked').write_text('private')
    index = git(source, 'ls-files', '--stage')
    output = tmp_path/'integration'
    env = dict(os.environ, GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.test')
    result = subprocess.run([sys.executable, str(PREPARE), '--output', str(output), '--branch', 'codex/integration', '--commits', commit], cwd=source, env=env, capture_output=True, text=True)
    assert result.returncode==0, result.stdout+result.stderr
    expected = git(writer, 'rev-parse', 'HEAD')
    assert expected!=stale and git(output, 'rev-parse', 'HEAD^')==expected
    assert git(output, 'rev-parse', 'origin/main')==expected
    assert (output/'base').read_text()=='new remote main'
    assert (output/'feature').read_text()=='feature'
    assert not (output/'untracked').exists()
    assert (source/'base').read_text()=='unsaved user edit'
    assert git(source, 'ls-files', '--stage')==index
    assert git(source, 'rev-parse', 'HEAD')==commit
    assert git(output, 'remote', 'get-url', 'origin')==git(source, 'remote', 'get-url', 'origin')


def test_integration_rejects_existing_output_before_fetch(tmp_path):
    source, _, _ = repository(tmp_path)
    output=tmp_path/'existing';output.mkdir();(output/'keep').write_text('keep')
    result=subprocess.run([sys.executable,str(PREPARE),'--output',str(output)],cwd=source,capture_output=True,text=True)
    assert result.returncode==1
    assert (output/'keep').read_text()=='keep'


def test_ci_uses_latest_exact_sha_and_does_not_confuse_other_runs():
    class Client:
        def pages(self,path,key):
            if key=='workflow_runs':
                return [dict(id=1,head_sha='a'*40,name='Checks',status='completed',conclusion='success'),dict(id=2,head_sha='a'*40,name='Checks',status='in_progress',conclusion=None),dict(id=3,head_sha='b'*40,name='Checks',status='completed',conclusion='failure')]
            assert '/2/jobs?' in path
            return [dict(name='Python',status='in_progress',conclusion=None)]
    assert CI['status'](Client(),'owner/repo','a'*40,'Checks')==dict(state='pending',run=2,jobs=[dict(name='Python',status='in_progress',conclusion=None)])


@pytest.mark.parametrize('state,code',[('success',0),('failure',1),('pending',3)])
def test_ci_wait_emits_changes_only_and_preserves_failure_and_timeout(state,code):
    elapsed=[0];lines=[]
    def clock():return elapsed[0]
    def sleep(seconds):elapsed[0]+=seconds
    def fetch():return dict(state=state if elapsed[0]>=60 else 'pending',run=1,jobs=[])
    assert CI['watch'](fetch,60,30,lines.append,clock,sleep)==code
    assert [json.loads(line)['state'] for line in lines]==(['pending'] if state=='pending' else ['pending',state])


def test_ci_no_runs_is_pending():
    class Client:
        def pages(self,path,key):return []
    assert CI['status'](Client(),'owner/repo','a'*40,'Checks')['state']=='pending'


def test_ci_cli_rejects_mutable_ref_without_network():
    result=subprocess.run([sys.executable,str(ROOT/'scripts/ci-status'),'--repo','owner/repo','--sha','main'],capture_output=True,text=True)
    assert result.returncode==2
    assert 'full commit SHA' in result.stderr
