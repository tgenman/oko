"""Candidate migration checks use an isolated tree and never approve a release."""
import json
import os
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_candidate_diff_run_is_isolated_and_keeps_baseline_unreviewed(tmp_path):
    candidate = tmp_path / 'candidate.yaml'
    document = yaml.safe_load((ROOT / 'api/openapi.yaml').read_text())
    document['info']['version'] = '99.0.0'
    document['components']['schemas']['GovernanceProbe'] = {'type': 'object', 'required': ['value'], 'properties': {'value': {'type': 'integer'}}, 'additionalProperties': False}
    candidate.write_text(yaml.safe_dump(document))
    baseline = (ROOT / 'api/baseline.json').read_bytes()
    canonical = (ROOT / 'api/openapi.yaml').read_bytes()
    reports = tmp_path / 'reports'
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'diff', '--candidate', str(candidate), '--report-dir', str(reports)], env={**os.environ, 'OKO_PYTHON': sys.executable}, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    report_path = next(reports.glob('*/report.json'))
    report = json.loads(report_path.read_text())
    assert report['candidate']['status'] == 'UNREVIEWED'
    assert report['candidate']['full_check'] is False
    assert report['suites']['compatibility']['status'] == 'PENDING_REVIEW'
    evidence = json.loads((report_path.parent / 'compatibility.json').read_text())
    assert evidence['status'] == 'PENDING_REVIEW'
    assert '/components/schemas/GovernanceProbe' in evidence['changes']
    assert 'GovernanceProbe' not in evidence['before']['components']['schemas']
    assert evidence['after']['components']['schemas']['GovernanceProbe']['properties']['value']['type'] == 'integer'
    isolated = Path(report['candidate']['root'])
    assert isolated != ROOT
    assert (isolated / 'api/openapi.yaml').read_bytes() == candidate.read_bytes()
    assert (isolated / 'api/baseline.json').read_bytes() == baseline
    assert (ROOT / 'api/openapi.yaml').read_bytes() == canonical
    assert (ROOT / 'api/baseline.json').read_bytes() == baseline


def test_candidate_rejects_tampering_baseline_approval_and_bad_spec(tmp_path):
    candidate = tmp_path / 'candidate.yaml'
    candidate.write_bytes((ROOT / 'api/openapi.yaml').read_bytes())
    reports = tmp_path / 'reports'
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'diff', '--candidate', str(candidate), '--report-dir', str(reports)], env={**os.environ, 'OKO_PYTHON': sys.executable}, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(next(reports.glob('*/report.json')).read_text())
    tree = Path(report['candidate']['root'])
    context_file = tree.parent / 'candidate.json'
    environment = {**os.environ, 'OKO_PYTHON': sys.executable, 'OKO_CANDIDATE_CONTEXT': str(context_file)}
    def command(*args):
        return subprocess.run([sys.executable, str(tree / 'scripts/api-compat.py'), *args], cwd=tree, env=environment, capture_output=True, text=True)
    refused = command('baseline')
    assert refused.returncode != 0
    assert 'cannot approve' in refused.stdout + refused.stderr
    escaped = command('baseline', '--spec', str(ROOT / 'api/openapi.yaml'), '--baseline', str(ROOT / 'api/baseline.json'))
    assert escaped.returncode != 0
    assert 'cannot approve' in escaped.stdout + escaped.stderr
    (tree / 'api/openapi.yaml').write_text('changed after selection')
    refused = command('check')
    assert refused.returncode != 0
    assert 'context does not match' in refused.stdout + refused.stderr
    candidate.write_text('invalid OpenAPI')
    failed = subprocess.run([sys.executable, str(ROOT / 'scripts/check'), 'diff', '--candidate', str(candidate), '--report-dir', str(tmp_path / 'invalid')], env={**os.environ, 'OKO_PYTHON': sys.executable}, capture_output=True, text=True)
    assert failed.returncode != 0
    assert 'FAIL compatibility' in failed.stdout


def test_staged_candidate_context_requires_exact_contract_and_baseline(tmp_path, monkeypatch):
    import importlib.util
    import pytest
    spec = importlib.util.spec_from_file_location('staged_candidate', ROOT / 'scripts/candidate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source, stage = tmp_path / 'source', tmp_path / 'stage'
    for directory in [source, stage]:
        (directory / 'api').mkdir(parents=True)
        (directory / 'api/openapi.yaml').write_text('same contract')
        (directory / 'api/baseline.json').write_text('same baseline')
    value = dict(version=1, status='UNREVIEWED', root=str(source),
                 contract_digest=module.digest(source / 'api/openapi.yaml'),
                 baseline_digest=module.digest(source / 'api/baseline.json'))
    marker = source / '.oko-candidate.json'
    marker.write_text(json.dumps(value))
    monkeypatch.setenv('OKO_CANDIDATE_CONTEXT', str(marker))
    inherited = module.staged_context(source, stage)
    monkeypatch.setenv('OKO_CANDIDATE_CONTEXT', str(inherited))
    assert module.context(stage)['root'] == str(stage)
    monkeypatch.setenv('OKO_CANDIDATE_CONTEXT', str(marker))
    for path in ['api/openapi.yaml', 'api/baseline.json']:
        saved = (stage / path).read_text()
        (stage / path).write_text('changed staged input')
        with pytest.raises(ValueError, match='Staged candidate differs'):
            module.staged_context(source, stage)
        (stage / path).write_text(saved)
    monkeypatch.delenv('OKO_CANDIDATE_CONTEXT')
    marker.unlink()
    assert module.staged_context(source, stage) is None
