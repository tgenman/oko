"""Generated wire guarantees exercised through the generator command."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def generated_library(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp('strict-wire')
    document = {'openapi': '3.1.0', 'info': {'title': 'Strict primitives', 'version': '3.0.0'},
                'paths': {}, 'components': {'schemas': {
                    'EventTime': {'type': 'object', 'additionalProperties': False, 'required': ['occurred_at'], 'properties': {'occurred_at': {'type': 'string', 'format': 'date-time'}}},
                    'Timestamp': {'type': 'string', 'format': 'date-time'},
                    'NullableTimestamp': {'anyOf': [{'type': 'string', 'format': 'date-time'}, {'type': 'null'}]},
                    'TimestampList': {'type': 'array', 'items': {'type': 'string', 'format': 'date-time'}},
                    'BooleanConstant': {'type': 'boolean', 'const': True},
                    'LocalDate': {'type': 'string', 'format': 'date'},
                    'Acknowledgement': {'type': 'object', 'additionalProperties': False,
                                        'required': ['ok'], 'properties': {'ok': {'type': 'boolean', 'const': True}}},
                }}}
    definitions = {}
    for name, tag, field in [('CountSelection', 'count', 'count'), ('DateSelection', 'date', 'date')]:
        definitions[name] = {'type': 'object', 'additionalProperties': False, 'required': ['mode', field],
                             'properties': {'mode': {'type': 'string', 'const': tag}, field: {'type': 'string'}}}
    definitions['Selection'] = {'oneOf': [{'$ref': '#/components/schemas/CountSelection'},
                                         {'$ref': '#/components/schemas/DateSelection'}],
                                'discriminator': {'propertyName': 'mode'}}
    document['components']['schemas'].update(definitions)
    source = tmp_path / 'wire.json'
    source.write_text(json.dumps(document))
    generated = subprocess.run([sys.executable, str(ROOT / 'scripts/generate_api.py'), 'generate',
                                '--schema', str(source), '--output', str(tmp_path / 'generated')],
                               cwd=ROOT, capture_output=True, text=True)
    assert generated.returncode == 0, generated.stdout + generated.stderr
    return tmp_path


def test_generated_formats_and_boolean_constants_do_not_coerce_json(generated_library):
    tmp_path = generated_library
    probe = subprocess.run([sys.executable, '-c', '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from generated.models import EventTime
from pydantic import ValidationError
for timestamp in ('2026-10-15T09:00:00Z', '2026-10-15T12:00:00+03:00'):
    assert EventTime.model_validate_json('{"occurred_at":"'+timestamp+'"}').model_dump(mode='json')['occurred_at'] == '2026-10-15T09:00:00Z'
try:
    EventTime.model_validate_json('{"occurred_at":"2026-10-15T12:00:00"}')
except ValidationError:
    pass
else:
    raise AssertionError('A wire timestamp without timezone was accepted')

import sys
sys.path.insert(0, sys.argv[1])
from generated.models import Timestamp, LocalDate, Acknowledgement, NullableTimestamp, TimestampList, BooleanConstant
from pydantic import ValidationError
for model, payload in ((Timestamp, '1'), (Timestamp, '"123"'), (LocalDate, '0'), (Acknowledgement, '{"ok":1}'), (NullableTimestamp, '1'), (TimestampList, '[1]'), (BooleanConstant, '1')):
    try:
        model.model_validate_json(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError(f'{model.__name__} accepted {payload}')
assert Timestamp.model_validate_json('"2026-10-15T12:00:00+03:00"').model_dump_json() == '"2026-10-15T09:00:00Z"'
assert LocalDate.model_validate_json('"2026-10-15"').model_dump_json() == '"2026-10-15"'
assert Acknowledgement.model_validate_json('{"ok":true}').ok is True
assert NullableTimestamp.model_validate_json('null').root is None
assert TimestampList.model_validate_json('["2026-10-15T12:00:00+03:00"]').model_dump_json() == '["2026-10-15T09:00:00Z"]'
''', str(tmp_path)], cwd=ROOT, capture_output=True, text=True)
    assert probe.returncode == 0, probe.stdout + probe.stderr


def test_generated_dto_types_catch_constructor_return_and_exhaustive_errors(tmp_path, generated_library):
    import shutil
    shutil.copytree(generated_library / 'generated', tmp_path / 'generated', ignore=shutil.ignore_patterns('__pycache__'))
    valid = tmp_path / 'positive.py'
    valid.write_text('''from typing import assert_never
from generated.models import CountSelection, DateSelection

def describe(value: CountSelection | DateSelection) -> str:
    if value.mode == 'count':
        return value.count
    if value.mode == 'date':
        return value.date
    assert_never(value)

selection = CountSelection(mode='count', count='20')
''')
    command = [sys.executable, '-m', 'mypy', '--strict', '--no-incremental', '--follow-imports=silent']
    negative = tmp_path / 'negative.py'
    negative.write_text('''from typing import assert_never
from generated.models import CountSelection, DateSelection

CountSelection(mode='count', count='20', wrong=True)
CountSelection(mode='count')
CountSelection(mode='count', count=20)

def response() -> CountSelection:
    return {'mode': 'count', 'count': '20'}

def describe(value: CountSelection | DateSelection) -> str:
    if value.mode == 'count':
        return value.count
    assert_never(value)
''')
    failed = subprocess.run([*command, str(valid), str(negative)], cwd=ROOT, capture_output=True, text=True)
    assert failed.returncode != 0
    assert 'positive.py:' not in failed.stdout, failed.stdout
    for expected in ('Unexpected keyword argument "wrong"', 'Missing named argument "count"',
                     'Argument "count"', 'Incompatible return value type', 'Argument 1 to "assert_never"'):
        assert expected in failed.stdout, failed.stdout + failed.stderr


def test_actual_v3_typescript_unions_narrow_and_reject_contradictions(tmp_path):
    types = (ROOT / 'frontend/src/api/generated/types.gen').as_posix()
    fixture = tmp_path / 'narrowing.ts'
    fixture.write_text(f'''import type {{LifetimePolicy, ClientSession, RuntimeStatus}} from '{types}';
function never(value: never): never {{throw new Error(String(value));}}
function interval(policy: LifetimePolicy): number {{
  switch(policy.kind) {{
    case 'explicit': return 0;
    case 'client_presence': return policy.renew_after_seconds;
    default: return never(policy);
  }}
}}
const explicit: LifetimePolicy = {{kind:'explicit'}};
const leased: LifetimePolicy = {{kind:'client_presence',lease_seconds:6,grace_seconds:15,renew_after_seconds:2}};
interval(explicit); interval(leased);
// @ts-expect-error Required lease timers cannot disappear from a presence policy.
const missing: LifetimePolicy = {{kind:'client_presence'}};
// @ts-expect-error Closed explicit branch cannot carry presence timers.
const mixed: LifetimePolicy = {{kind:'explicit',lease_seconds:6}};
// @ts-expect-error Session handler cannot return a stop acknowledgement.
function wrongHandler(): ClientSession {{return {{ok:true}};}}
function incomplete(policy: LifetimePolicy): number {{
  if(policy.kind === 'explicit') return 0;
  // @ts-expect-error Unhandled presence branch is not never.
  return never(policy);
}}
function wrongField(status: RuntimeStatus) {{
  // @ts-expect-error Runtime contract does not contain browser tabs.
  return status.tabs;
}}
''')
    result = subprocess.run(['node', str(ROOT / 'node_modules/typescript/bin/tsc'), '--noEmit', '--strict',
                             '--skipLibCheck', '--target', 'ES2022', '--module', 'Node16', '--moduleResolution', 'Node16', str(fixture)],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_generation_is_identical_without_checkout_package_discovery(tmp_path, generated_library):
    import shutil
    source = generated_library / 'wire.json'
    minimal = tmp_path / 'minimal'
    (minimal / 'scripts').mkdir(parents=True)
    shutil.copy2(ROOT / 'scripts/generate_api.py', minimal / 'scripts/generate_api.py')
    shutil.copytree(ROOT / 'scripts/api-templates', minimal / 'scripts/api-templates')
    result = subprocess.run([sys.executable, str(minimal / 'scripts/generate_api.py'), 'generate', '--schema', str(source), '--output', str(tmp_path / 'index-output')], cwd=minimal, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (generated_library / 'generated/models.py').read_bytes() == (tmp_path / 'index-output/models.py').read_bytes()
