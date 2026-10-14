#!/usr/bin/env python3
"""Generate Python wire DTOs from a local canonical OpenAPI document.

Only the explicit generate command writes to the checkout. Checks regenerate in
a temporary directory, including a stable input filename, then compare bytes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'oko' / 'reader' / 'generated'


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
            '--base-class', 'oko.reader.wire.WireModel',
            '--disable-timestamp', '--use-standard-collections',
            '--use-union-operator', '--use-missing-sentinel', '--strict-nullable',
            '--strict-types', 'str', 'int', 'float', 'bool',
            '--enum-field-as-literal', 'all', '--use-annotated',
            '--formatters', 'black', 'isort',
        ], check=True, cwd=ROOT)
    (output / '__init__.py').write_text('# Generated from api/openapi.yaml; do not edit.\n')
    (output / 'revision.py').write_text('# Generated; do not edit.\nCONTRACT_DIGEST = ' + repr(digest) + '\n')
    (output / 'openapi.json').write_text(canonical)


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
