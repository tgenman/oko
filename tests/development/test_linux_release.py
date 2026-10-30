"""Publication keeps checked-main authority and immutable, secret-free identities."""
import json
from pathlib import Path
import subprocess
import sys

import yaml
import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_publication_requires_successful_main_push_and_never_deploys():
    workflow = yaml.safe_load((ROOT/'.github/workflows/publish.yml').read_text())
    # YAML 1.1 readers parse the key 'on' as True.
    events = workflow.get('on', workflow.get(True))
    assert events['workflow_run'] == dict(workflows=['Checks'], types=['completed'], branches=['main'])
    job = workflow['jobs']['publish']
    assert "conclusion == 'success'" in job['if']
    assert "event == 'push'" in job['if']
    assert 'head_repository.full_name == github.repository' in job['if']
    assert job['permissions'] == {'contents': 'read', 'packages': 'write'}
    text = (ROOT/'.github/workflows/publish.yml').read_text()
    assert 'secrets.' not in text.replace('secrets.GITHUB_TOKEN', '')
    assert 'ssh ' not in text and 'DEPLOY_' not in text
    steps = job['steps']
    build = next(step for step in steps if step.get('uses','').startswith('docker/build-push-action@'))
    assert build['with']['platforms'] == 'linux/amd64' and build['with']['push'] is False
    smoke = next(index for index,step in enumerate(steps) if '--name oko-release-smoke' in step.get('run',''))
    push = next(index for index,step in enumerate(steps) if 'docker push ' in step.get('run',''))
    assert smoke < push
    assert 'head_sha' in steps[0]['with']['ref']


def test_manifest_pins_every_component_without_credentials(tmp_path):
    output = tmp_path/'manifest.json'
    sha, digest = 'a'*40, 'sha256:'+'b'*64
    result = subprocess.run([sys.executable,str(ROOT/'deployment/release-manifest.py'),'--sha',sha,'--digest',digest,'--output',str(output)],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text())
    assert manifest['sha'] == manifest['helper']['release'] == sha
    assert manifest['image'] == 'ghcr.io/tgenman/oko-runtime@'+digest
    assert manifest['platform'] == 'linux/amd64'
    assert manifest['helper']['protocol'] == 1
    assert manifest['codex']['version'] == '0.164.0'
    assert manifest['schema'] == {'minimum':65,'maximum':65}
    assert not {'token','password','api_key','auth'} & manifest.keys()


def registry_module():
    import runpy
    return runpy.run_path(str(ROOT/'deployment/registry-state.py'))


def test_public_package_is_rejected_before_any_registry_write_or_lookup(monkeypatch):
    import pytest
    scope=registry_module();calls=[]
    def request(url,headers):
        calls.append(url)
        return 200,{'visibility':'public'},{}
    scope['probe'].__globals__['request']=request
    with pytest.raises(RuntimeError,match='non-private'):
        scope['probe']('a'*40,'synthetic-token','owner')
    assert calls == [scope['PACKAGE']]


@pytest.mark.parametrize('status,code',[(401,'UNAUTHORIZED'),(403,'DENIED'),(429,'TOOMANYREQUESTS'),(500,'UNKNOWN'),(404,'DENIED')])
def test_unknown_tag_inspection_never_becomes_permission_to_publish(status,code):
    scope=registry_module()
    replies=iter([(200,{'visibility':'private'},{}),(200,{'token':'synthetic-registry-token'},{}),(status,{'errors':[{'code':code}]},{})])
    scope['probe'].__globals__['request']=lambda *_: next(replies)
    import pytest
    with pytest.raises(RuntimeError,match='could not be verified'):
        scope['probe']('a'*40,'synthetic-token','owner')


def test_only_confirmed_missing_manifest_in_known_private_package_can_be_published():
    scope=registry_module()
    replies=iter([(200,{'visibility':'private'},{}),(200,{'token':'synthetic-registry-token'},{}),(404,{'errors':[{'code':'MANIFEST_UNKNOWN'}]}, {})])
    scope['probe'].__globals__['request']=lambda *_:next(replies)
    assert scope['probe']('a'*40,'synthetic-token','owner') == dict(package='private',present=False,digest=None)


def test_unknown_package_metadata_never_authorizes_publication():
    scope=registry_module();calls=[]
    def request(url,headers):
        calls.append(url)
        return 404,{},{}
    scope['probe'].__globals__['request']=request
    with pytest.raises(RuntimeError,match='visibility is unknown'):
        scope['probe']('a'*40,'synthetic-token','owner')
    assert calls == [scope['PACKAGE']]
