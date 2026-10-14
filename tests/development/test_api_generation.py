"""Build command behavior: null/unset preservation and non-mutating drift checks."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]


def test_generated_library_preserves_wire_values_and_check_rejects_drift(tmp_path: Path) -> None:
    schema = tmp_path / 'contract.json'
    schema.write_text(json.dumps({
        'openapi': '3.1.0', 'info': {'title': 'Synthetic contract', 'version': '1'},
        'paths': {}, 'components': {'schemas': {'Patch': {
            'type': 'object', 'additionalProperties': False,
            'required': ['required_nullable'], 'properties': {
                'required_nullable': {'type': ['string', 'null']},
                'optional_value': {'type': 'string'},
                'reset': {'type': ['string', 'null']},
                'enabled': {'type': 'boolean'}, 'quantity': {'type': 'integer'},
                'choice': {'enum': ['first', 'second'], 'type': 'string'},
                'external-name': {'type': 'string'},
            },
        }}}}))
    output = tmp_path / 'library'
    command = [sys.executable, str(ROOT / 'scripts/generate_api.py')]
    arguments = ['--schema', str(schema), '--output', str(output)]
    subprocess.run(command + ['generate'] + arguments, check=True)
    spec = importlib.util.spec_from_file_location('synthetic_wire', output / 'models.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    model = module.Patch
    assert model.model_validate({'required_nullable': None}).model_dump(by_alias=True, exclude_unset=True) == {'required_nullable': None}
    assert model.model_validate({'required_nullable': 'value', 'reset': None, 'external-name': 'alias'}).model_dump(by_alias=True, exclude_unset=True) == {'required_nullable': 'value', 'reset': None, 'external-name': 'alias'}
    for invalid in [{}, {'optional_value': None}, {'enabled': 'false'}, {'quantity': True}, {'choice': 'third'}, {'unknown': 1}]:
        with pytest.raises(ValidationError):
            model.model_validate({'required_nullable': None, **invalid} if invalid else invalid)
    before = {p.name: p.read_bytes() for p in output.glob('*') if p.is_file()}
    subprocess.run(command + ['check'] + arguments, check=True)
    assert before == {p.name: p.read_bytes() for p in output.glob('*') if p.is_file()}
    schema.write_text(schema.read_text().replace('Synthetic contract', 'Changed contract'))
    failed = subprocess.run(command + ['check'] + arguments, capture_output=True, text=True)
    assert failed.returncode == 1
    assert 'Stale Python API library' in failed.stderr
    assert before == {p.name: p.read_bytes() for p in output.glob('*') if p.is_file()}
