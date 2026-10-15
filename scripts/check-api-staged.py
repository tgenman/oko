#!/usr/bin/env python3
"""Regenerate API libraries from exact index inputs in an isolated directory."""
import os
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import tomllib

INPUTS = ('api/', 'scripts/candidate', 'scripts/api', 'scripts/generate', 'scripts/check-api-staged.py',
          'frontend/src/api/generated/', 'frontend/src/api/legacy-generated/',
          'oko/reader/generated/', 'oko/reader/legacy_generated/', 'package.json', 'package-lock.json',
          'pyproject.toml', 'poetry.lock')


def digest(document):
    canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    return hashlib.sha256(canonical.encode()).hexdigest()


def reviewed_baseline(stage):
    previous = subprocess.run(['git', 'show', 'HEAD:api/baseline.json'], capture_output=True)
    if previous.returncode:
        return  # Initial baseline establishment; future updates require evidence.
    before = json.loads(previous.stdout)
    after = json.loads((stage / 'api/baseline.json').read_text())
    if before == after:
        return
    try:
        review = json.loads((stage / 'api/baseline.review.json').read_text())
    except (OSError, ValueError) as error:
        raise ValueError('Staged baseline update requires retained semantic review evidence') from error
    if (before['info']['version'] == after['info']['version']
            or review.get('from_version') != before['info']['version']
            or review.get('to_version') != after['info']['version']
            or review.get('from_digest') != digest(before)
            or review.get('to_digest') != digest(after)
            or not isinstance(review.get('semantic_review'), str)
            or not review['semantic_review'].strip()):
        raise ValueError('Staged baseline update requires changed version and matching semantic review evidence')


def pinned_tools(stage, python):
    package = json.loads((stage / 'package.json').read_text())
    lock = json.loads((stage / 'package-lock.json').read_text())
    for name in ('@hey-api/openapi-ts', 'typescript', 'tsx'):
        expected = package['devDependencies'][name]
        installed = json.loads((stage / 'node_modules' / name / 'package.json').read_text())['version']
        if installed != expected or lock['packages']['node_modules/' + name]['version'] != expected:
            raise ValueError(f'API index checks require the staged pinned npm tool: {name} {expected}')
    project = tomllib.loads((stage / 'pyproject.toml').read_text())
    pins = project['dependency-groups']['dev']
    for name in ('datamodel-code-generator', 'openapi-spec-validator', 'pyyaml'):
        expected = next(pin.split('==')[1].split(';')[0].strip() for pin in pins if pin.startswith(name + '=='))
        installed = subprocess.check_output([python, '-c',
                                            'import importlib.metadata; print(importlib.metadata.version("' + name + '"))'], text=True).strip()
        if installed != expected:
            raise ValueError(f'API index checks require the staged pinned Python tool: {name} {expected}')


def selected(name):
    return any(name.startswith(prefix) if prefix.endswith('/') or prefix.startswith('scripts/') else name == prefix
               for prefix in INPUTS)


def main():
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
    changed = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '-z']).decode().split('\0')
    if not any(selected(name) for name in changed if name):
        return 0
    entries = subprocess.check_output(['git', 'ls-files', '--stage', '-z']).split(b'\0')
    with tempfile.TemporaryDirectory(prefix='oko-api-index-') as directory:
        stage = Path(directory)
        for raw in entries:
            if not raw:
                continue
            metadata, raw_name = raw.split(b'\t', 1)
            name = os.fsdecode(raw_name)
            if not selected(name):
                continue
            mode, identity, index_stage = metadata.decode().split()
            if index_stage != '0' or mode not in {'100644', '100755'}:
                raise ValueError(f'Unsupported API index entry: {name}')
            if PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
                raise ValueError('Invalid API index path')
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(['git', 'cat-file', 'blob', identity]))
        reviewed_baseline(stage)
        # Dependencies are fixed development tools, never contract/source inputs.
        node_modules = root / 'node_modules'
        if not node_modules.exists():
            raise ValueError('API index checks require installed pinned npm dependencies')
        (stage / 'node_modules').symlink_to(node_modules.resolve(), target_is_directory=True)
        python = os.environ.get('OKO_PYTHON', sys.executable)
        pinned_tools(stage, python)
        node = shutil.which(os.environ.get('OKO_NODE', 'node'))
        if not node:
            raise ValueError('API index checks require Node')
        from candidate import staged_context
        inherited = staged_context(root, stage)
        environment = {**os.environ, 'OKO_PYTHON': python}
        if inherited:
            environment['OKO_CANDIDATE_CONTEXT'] = str(inherited)
        print('CHECK exact staged API contract and generated libraries', flush=True)
        return subprocess.run([node, '--import', 'tsx', str(stage / 'scripts/generate-api.mts'), 'check'], cwd=stage,
                              env=environment).returncode


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
