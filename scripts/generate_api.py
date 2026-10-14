#!/usr/bin/env python3
"""Generate Python wire DTOs from a local canonical OpenAPI document.

Only the explicit generate command writes to the checkout. Checks regenerate in
a temporary directory, including a stable input filename, then compare bytes.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'oko' / 'reader' / 'generated'


def model_type(schema: dict) -> str:
    if '$ref' in schema:
        name = schema['$ref'].removeprefix('#/components/schemas/')
        if not re.fullmatch(r'[A-Za-z_]\w*', name):
            raise ValueError(f'Unsupported model reference: {name}')
        return 'models.' + name
    if 'anyOf' in schema or 'oneOf' in schema:
        return ' | '.join(model_type(part) for part in schema.get('anyOf', schema.get('oneOf')))
    kind = schema.get('type')
    if kind == 'array':
        return 'list[' + model_type(schema['items']) + ']'
    if kind in ('string', 'boolean', 'integer', 'number', 'null'):
        return {'string': 'str', 'boolean': 'bool', 'integer': 'int', 'number': 'float', 'null': 'None'}[kind]
    raise ValueError(f'Operation DTO must use a named component: {schema}')


def operation_types(document: dict) -> str:
    rows = ['# Generated from api/openapi.yaml; do not edit.',
            'from typing import Any', 'from . import models', '',
            'RESPONSES: dict[tuple[str, str], Any] = {']
    requests = []
    for path, methods in sorted(document.get('paths', {}).items()):
        for method, operation in sorted(methods.items()):
            if method not in {'get', 'post', 'patch', 'put', 'delete'}:
                continue
            key = repr((method.upper(), path))
            schema = operation['responses'].get('200', {}).get('content', {}).get('application/json', {}).get('schema')
            if schema:
                rows.append(f'    {key}: {model_type(schema)},')
            schema = operation.get('requestBody', {}).get('content', {}).get('application/json', {}).get('schema')
            if schema:
                requests.append(f'    {key}: {model_type(schema)},')
    return '\n'.join(rows + ['}', '', 'REQUESTS: dict[tuple[str, str], Any] = {'] + requests + ['}', ''])


def generate(schema: Path, output: Path) -> None:
    document = yaml.safe_load(schema.read_text())
    canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='oko-wire-') as directory:
        source = Path(directory) / 'openapi.json'
        source.write_text(canonical)
        subprocess.run([
            sys.executable, '-m', 'datamodel_code_generator',
            '--input', str(source), '--input-file-type', 'openapi',
            '--output', str(output / 'models.py'),
            '--output-model-type', 'pydantic_v2.BaseModel',
            '--output-datetime-class', 'datetime',
            '--base-class', 'oko.reader.wire.WireModel',
            '--disable-timestamp', '--use-standard-collections',
            '--use-union-operator', '--use-missing-sentinel', '--strict-nullable',
            '--strict-types', 'str', 'int', 'float', 'bool',
            '--enum-field-as-literal', 'all', '--field-constraints',
            '--use-default-kwarg',
            '--custom-template-dir', str(ROOT / 'scripts' / 'api-templates'),
            '--additional-imports', 'typing.Annotated',
            '--formatters', 'black', 'isort',
        ], check=True, cwd=ROOT)
    (output / '__init__.py').write_text('# Generated from api/openapi.yaml; do not edit.\n')
    (output / 'revision.py').write_text('# Generated; do not edit.\nCONTRACT_DIGEST = ' + repr(digest) + '\n')
    (output / 'openapi.json').write_text(canonical)
    (output / 'operations.py').write_text(operation_types(document))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['generate', 'check'])
    parser.add_argument('--schema', type=Path, default=ROOT / 'api' / 'openapi.yaml')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.command == 'generate':
        generate(args.schema, args.output)
        return
    with tempfile.TemporaryDirectory(prefix='oko-wire-check-') as directory:
        expected = Path(directory)
        generate(args.schema, expected)
        names = {p.name for p in expected.iterdir()}
        actual = {p.name for p in args.output.glob('*') if p.is_file()}
        changed = sorted(names ^ actual | {
            name for name in names & actual
            if (expected / name).read_bytes() != (args.output / name).read_bytes()
        })
        if changed:
            parser.exit(1, 'Stale Python API library: ' + ', '.join(changed) + '\nRun npm run api:generate.\n')
        print('Python API library is current')


if __name__ == '__main__':
    main()
