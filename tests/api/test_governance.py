"""Contract governance through CLI commands and exact Git-index checks."""
import json
import os
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]


def run(*args, cwd=ROOT):
    return subprocess.run(args, cwd=cwd, env={**os.environ, 'OKO_PYTHON': sys.executable},
                          capture_output=True, text=True)


def probe():
    return {'type': 'object', 'required': ['value'], 'properties': {'value': {'type': 'string'}, 'title': {'type': 'string'}}, 'additionalProperties': False}


def documents(tmp_path):
    from tests.support.build_tools import api_document
    value = api_document()
    value['components']['schemas']['GovernanceProbe'] = probe()
    spec, baseline = tmp_path / 'spec.json', tmp_path / 'baseline.json'
    for path in (spec, baseline):
        path.write_text(json.dumps(value))
    return value, spec, baseline


def compat(command, spec, baseline, *extra):
    return run(sys.executable, str(ROOT / 'scripts/api-compat.py'), command,
               '--spec', str(spec), '--baseline', str(baseline), *extra)


def test_wire_change_fails_readonly_and_documentation_only_change_passes(tmp_path):
    value, spec, baseline = documents(tmp_path)
    before = baseline.read_bytes()
    value['components']['schemas']['GovernanceProbe']['properties']['title']['description'] = 'New documentation'
    spec.write_text(json.dumps(value))
    assert compat('check', spec, baseline).returncode == 0
    # A property called title remains wire data, rather than documentation.
    value['components']['schemas']['GovernanceProbe']['properties']['title']['type'] = 'integer'
    spec.write_text(json.dumps(value))
    result = compat('check', spec, baseline)
    assert result.returncode != 0
    assert 'potential' in (result.stdout + result.stderr).lower()
    assert 'title' in result.stdout + result.stderr
    assert baseline.read_bytes() == before
    assert json.loads(spec.read_text()) == value


def test_baseline_update_requires_version_and_semantic_review(tmp_path):
    value, spec, baseline = documents(tmp_path)
    value['components']['schemas']['GovernanceProbe']['properties']['value']['type'] = 'integer'
    spec.write_text(json.dumps(value))
    review = tmp_path / 'review.md'
    review.write_text('Semantic review: GovernanceProbe.value is now an integer; all clients migrate together.\n')
    assert compat('baseline', spec, baseline, '--review', str(review)).returncode != 0
    next_version = str(int(value['info']['version'].split('.')[0]) + 1) + '.0.0'
    value['info']['version'] = next_version
    spec.write_text(json.dumps(value))
    assert compat('baseline', spec, baseline).returncode != 0
    assert compat('baseline', spec, baseline, '--review', str(review)).returncode == 0
    assert json.loads(baseline.read_text()) == value
    evidence = json.loads(baseline.with_suffix('.review.json').read_text())
    assert evidence['to_version'] == next_version
    assert 'all clients migrate' in evidence['semantic_review']
    assert compat('check', spec, baseline).returncode == 0


def test_missing_and_dynamic_remote_references_fail_before_validation(tmp_path):
    value, spec, _ = documents(tmp_path)
    schema = value['components']['schemas']['GovernanceProbe']['properties']['value']
    for reference in [{'$ref': '#/components/schemas/Absent'},
                      {'$ref': 'https://example.invalid/private'},
                      {'$dynamicRef': 'https://example.invalid/private'}]:
        schema.clear()
        schema.update(reference)
        spec.write_text(json.dumps(value))
        result = run(sys.executable, str(ROOT / 'scripts/api.py'), 'validate', '--spec', str(spec))
        assert result.returncode != 0
        assert 'reference' in result.stderr.lower()


def test_staged_codegen_rejects_index_drift_and_ignores_unstaged_drift(tmp_path):
    from tests.support.build_tools import api_repository
    api_repository(tmp_path)
    # This is a synthetic release repository for the staged-governance test.
    # Its initial reviewed fixture is its own contract, including when the
    # surrounding suite checks an unreviewed migration candidate.
    fixture = yaml.safe_load((tmp_path / 'api/openapi.yaml').read_text())
    fixture['components']['schemas']['GovernanceProbe'] = probe()
    for filename in ('openapi.yaml', 'baseline.json'):
        (tmp_path / 'api' / filename).write_text(json.dumps(fixture))
    assert run('node', '--import', 'tsx', 'scripts/generate-api.mts', 'generate', cwd=tmp_path).returncode == 0
    source = tmp_path / 'frontend/src/api/generated/revision.ts'
    valid = source.read_text()
    # Commit an invalid predecessor so both later staged versions are changes.
    source.write_text('// predecessor to repair\n')
    assert run('git', 'init', '-q', cwd=tmp_path).returncode == 0
    assert run('git', 'add', 'api', 'scripts', 'frontend', 'oko', 'package.json', 'package-lock.json', 'pyproject.toml', 'poetry.lock', cwd=tmp_path).returncode == 0
    assert run('git', '-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-qm', 'fixture', cwd=tmp_path).returncode == 0
    source.write_text(valid + '// staged drift\n')
    assert run('git', 'add', str(source), cwd=tmp_path).returncode == 0
    source.write_text(valid)
    failed = run(sys.executable, 'scripts/check-api-staged.py', cwd=tmp_path)
    assert failed.returncode != 0
    assert 'Stale TypeScript API library' in failed.stdout + failed.stderr
    assert run('git', 'add', str(source), cwd=tmp_path).returncode == 0
    source.write_text('invalid unstaged generated bytes\n')
    (tmp_path / 'api/openapi.yaml').write_text('invalid unstaged OpenAPI\n')
    before = run('git', 'diff', '--binary', cwd=tmp_path).stdout
    passed = run(sys.executable, 'scripts/check-api-staged.py', cwd=tmp_path)
    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert run('git', 'diff', '--binary', cwd=tmp_path).stdout == before
    baseline = tmp_path / 'api/baseline.json'
    previous = baseline.read_text()
    value = json.loads(previous)
    value['components']['schemas']['GovernanceProbe']['properties']['value']['type'] = 'integer'
    baseline.write_text(json.dumps(value))
    assert run('git', 'add', 'api/baseline.json', cwd=tmp_path).returncode == 0
    baseline.write_text(previous)
    failed = run(sys.executable, 'scripts/check-api-staged.py', cwd=tmp_path)
    assert failed.returncode != 0
    assert 'semantic review evidence' in failed.stdout + failed.stderr


def test_staged_baseline_requires_matching_review_evidence(tmp_path, monkeypatch):
    """The review boundary needs two documents, not another generated SDK."""
    import runpy
    import pytest
    checks = runpy.run_path(str(ROOT / 'scripts/check-api-staged.py'))
    value, spec, baseline = documents(tmp_path)
    (tmp_path / 'api').mkdir()
    baseline.rename(tmp_path / 'api/baseline.json')
    assert run('git', 'init', '-q', cwd=tmp_path).returncode == 0
    assert run('git', 'add', 'api', cwd=tmp_path).returncode == 0
    assert run('git', '-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-qm', 'baseline', cwd=tmp_path).returncode == 0
    stage = tmp_path / 'stage'
    (stage / 'api').mkdir(parents=True)
    previous = json.loads(json.dumps(value))
    value['info']['version'] = '2.0.0'
    value['components']['schemas']['GovernanceProbe']['properties']['value']['type'] = 'integer'
    (stage / 'api/baseline.json').write_text(json.dumps(value))
    evidence = dict(from_version='1.0.0', to_version='2.0.0',
                    from_digest=checks['digest'](previous), to_digest=checks['digest'](value),
                    semantic_review='The integer value requires client migration.')
    review = stage / 'api/baseline.review.json'
    monkeypatch.chdir(tmp_path)
    for change in ({'from_digest': 'wrong'}, {'to_digest': 'wrong'}, {'semantic_review': ''}, {'to_version': '1.0.0'}):
        review.write_text(json.dumps({**evidence, **change}))
        with pytest.raises(ValueError, match='semantic review evidence'):
            checks['reviewed_baseline'](stage)
    review.write_text(json.dumps(evidence))
    checks['reviewed_baseline'](stage)
