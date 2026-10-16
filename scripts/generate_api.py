#!/usr/bin/env python3
"""Generate Python wire DTOs from a local canonical OpenAPI document.

Only the explicit generate command writes to the checkout. Checks regenerate in
a temporary directory, including a stable input filename, then compare bytes.
"""
import argparse
import ast
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
            success = next((reply for code, reply in operation['responses'].items() if str(code).startswith('2')), {})
            schema = success.get('content', {}).get('application/json', {}).get('schema')
            if schema:
                rows.append(f'    {key}: {model_type(schema)},')
            schema = operation.get('requestBody', {}).get('content', {}).get('application/json', {}).get('schema')
            if schema:
                requests.append(f'    {key}: {model_type(schema)},')
    return '\n'.join(rows + ['}', '', 'REQUESTS: dict[tuple[str, str], Any] = {'] + requests + ['}', ''])


def strict_format_types(output: Path) -> None:
    """Compose strict JSON primitives at every generated nesting level.

    The pinned generator emits native datetime/date/URL parsers, which accept
    non-string JSON and conflict with string patterns. Rebind those imported
    annotations to validated wire strings; no model names or API fields are
    hardcoded in this deterministic generation step.
    """
    class WireAnnotations(ast.NodeTransformer):
        def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST | list[ast.stmt]:
            replacements = {'datetime': {'datetime': 'TimestampValue', 'date': 'DateValue'},
                            'pydantic': {'AnyUrl': 'UriValue'}}.get(node.module or '', {})
            kept, changed = [], []
            for alias in node.names:
                if alias.name in replacements:
                    changed.append(ast.alias(name=replacements[alias.name], asname=alias.asname or alias.name))
                else:
                    kept.append(alias)
            result: list[ast.stmt] = []
            if kept:
                result.append(ast.ImportFrom(module=node.module, names=kept, level=node.level))
            if changed:
                result.append(ast.ImportFrom(module='oko.reader.wire', names=changed, level=0))
            return result

        def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
            self.generic_visit(node)
            if not isinstance(node.value, ast.Name) or node.value.id != 'Literal':
                return node
            values = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            scalar_types = {type(value.value) for value in values if isinstance(value, ast.Constant)}
            function = 'boolean_constant' if scalar_types == {bool} else 'integer_constant' if scalar_types == {int} else None
            if not function:
                return node
            return ast.Subscript(value=ast.Name(id='Annotated', ctx=ast.Load()),
                                 slice=ast.Tuple(elts=[node, ast.Call(func=ast.Name(id='BeforeValidator', ctx=ast.Load()), args=[ast.Name(id=function, ctx=ast.Load())], keywords=[])], ctx=ast.Load()), ctx=ast.Load())

        def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
            self.generic_visit(node)
            if any(isinstance(base, ast.Subscript) and isinstance(base.value, ast.Name) and base.value.id == 'RootModel' for base in node.bases):
                for statement in node.body:
                    if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'model_config' for target in statement.targets) and isinstance(statement.value, ast.Call):
                        statement.value.keywords.append(ast.keyword(arg='allow_inf_nan', value=ast.Constant(value=False)))
                        break
                else:
                    node.body.insert(0, ast.Assign(targets=[ast.Name(id='model_config', ctx=ast.Store())], value=ast.Call(func=ast.Name(id='ConfigDict', ctx=ast.Load()), args=[], keywords=[ast.keyword(arg='allow_inf_nan', value=ast.Constant(value=False))])))
            return node

    path = output / 'models.py'
    tree = WireAnnotations().visit(ast.parse(path.read_text()))
    # Insert after the future import; AST rendering is stable for pinned Python.
    tree.body[1:1] = ast.parse('from typing import Annotated\nfrom pydantic import BeforeValidator, ConfigDict\nfrom oko.reader.wire import boolean_constant, integer_constant\n').body
    ast.fix_missing_locations(tree)
    path.write_text('# Generated from api/openapi.yaml; do not edit.\n' + ast.unparse(tree) + '\n')
    subprocess.run([sys.executable, '-m', 'isort', '--profile', 'black', '--project', 'oko', str(path)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([sys.executable, '-m', 'black', '--quiet', str(path)], check=True)


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
    strict_format_types(output)
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
