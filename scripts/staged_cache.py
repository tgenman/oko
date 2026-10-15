"""Successful exact-index checks, invalidated by index, HEAD, checkers and tools."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def key():
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
    digest = hashlib.sha256(b'staged-checks-v1')
    def add(value):
        digest.update(value)
        digest.update(b'\0')
    add(str(root).encode())
    add(subprocess.check_output(['git', 'ls-files', '--stage', '-z']))
    head = subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], capture_output=True)
    add(head.stdout)
    # Actual executors may be unstaged; changing them must invalidate success too.
    for file in sorted((root / 'scripts').rglob('*')):
        if file.is_file() and '__pycache__' not in file.parts and file.suffix != '.pyc':
            add(str(file.relative_to(root)).encode())
            add(file.read_bytes())
    for name in ('eslint.config.ts', '.githooks/pre-commit', 'package.json', 'package-lock.json',
                 'node_modules/.package-lock.json'):
        file = root / name
        add(name.encode())
        add(file.read_bytes() if file.is_file() else b'missing')
    add(json.dumps([sys.version, sys.executable, sys.prefix,
                   sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions())]).encode())
    node = shutil.which(os.environ.get('OKO_NODE', 'node'))
    add((node or 'missing-node').encode())
    if node:
        add(subprocess.check_output([node, '--version']))
    for name in ('typescript', 'tsx', 'eslint', 'jiti', '@hey-api/openapi-ts'):
        file = root / 'node_modules' / name / 'package.json'
        add(str(file.resolve()).encode())
        add(file.read_bytes() if file.is_file() else b'missing')
    ignored = {'GIT_INDEX_FILE', 'PWD', 'OLDPWD', 'SHLVL', '_', 'INIT_CWD',
               'PYTEST_CURRENT_TEST', 'PYTEST_VERSION', 'OKO_TEST_ARTIFACT_DIR'}
    add(json.dumps(sorted((k, v) for k, v in os.environ.items()
                          if k not in ignored and not k.startswith('npm_'))).encode())
    return root / '.scratch/check-cache/staged' / (digest.hexdigest() + '.json')


def hit(receipt):
    try:
        return json.loads(receipt.read_text()) == {'key': receipt.stem, 'version': 1}
    except (OSError, ValueError):
        return False


def remember(receipt):
    receipt.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt.with_suffix('.' + str(os.getpid()) + '.tmp')
    temporary.write_text(json.dumps({'key': receipt.stem, 'version': 1}))
    temporary.replace(receipt)
