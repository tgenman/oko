"""Tiny source trees for tests of build tools, independent of the product API size."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def api_document() -> dict:
    return {'openapi': '3.1.0', 'info': {'title': 'Око Reader API', 'version': '1.0.0'},
                'paths': {'/api/probe': {'get': {'operationId': 'probe', 'responses': {
                    '200': {'description': 'ok', 'content': {'application/json': {
                        'schema': {'$ref': '#/components/schemas/Probe'}}}}}}}},
                'components': {'schemas': {'Probe': {'type': 'object', 'required': ['value'],
                    'properties': {'value': {'type': 'string'}}, 'additionalProperties': False}}}}


def api_repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / 'scripts', root / 'scripts')
    (root / 'api').mkdir()
    document = api_document()
    binary_response = {
        'description': 'binary fixture',
        'content': {'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary'}}},
    }
    document['paths']['/api/blob'] = {'get': {'operationId': 'blob', 'responses': {'200': binary_response}}}
    for name in ('openapi.yaml', 'baseline.json'):
        (root / 'api' / name).write_text(json.dumps(document, ensure_ascii=False))
    for name in ('package.json', 'package-lock.json', 'pyproject.toml', 'poetry.lock'):
        shutil.copy2(ROOT / name, root / name)
    (root / 'node_modules').symlink_to(ROOT / 'node_modules', target_is_directory=True)


def frontend_repository(root: Path) -> None:
    api_repository(root)
    files = {
        'frontend/index.html': '<html><head><link rel="stylesheet" href="/reader-surfaces.css"></head><body><script type="module" src="/src/main.ts"></script></body></html>',
        'frontend/src/main.ts': "document.body.dataset.ready = 'yes';\n",
        'frontend/src/api/transport.ts': 'export const send = () => fetch("/");\n',
        'frontend/style.css': 'body {color: black}\n',
        'frontend/public/reader-surfaces.css': 'body {background: white}\n',
        'frontend/docs/index.html': '<script src="initializer.js"></script>',
        'frontend/docs/initializer.ts': 'const docsReady = true;\n',
    }
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    (root / 'frontend/icons').mkdir()
    shutil.copy2(ROOT / 'vite.config.mts', root / 'vite.config.mts')
    # Build publication tests need a real type error, not every tool's type graph.
    config = {'compilerOptions': {'strict': True, 'noEmit': True, 'skipLibCheck': True,
                                 'target': 'ES2022', 'module': 'ESNext', 'moduleResolution': 'bundler',
                                 'types': []}, 'files': ['frontend/src/main.ts']}
    for name in ('tsconfig.json', 'tsconfig.tooling.json'):
        (root / name).write_text(json.dumps(config))
    result = subprocess.run(['node', '--import', 'tsx', 'scripts/generate-api.mts', 'generate'], cwd=root,
                            env={**os.environ, 'OKO_PYTHON': sys.executable}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
