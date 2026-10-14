#!/usr/bin/env python3
"""Build a complete wheel from validated assets in a temporary source tree."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    # Check derived libraries without altering the checkout; runtime/package
    # freshness validation also checks canonical/backend/frontend digests.
    subprocess.run(['node', str(ROOT / 'scripts/generate-api.mjs'), 'check'], cwd=ROOT, check=True)
    sys.path.insert(0, str(ROOT))
    from oko.reader.static import validate_frontend
    generation = validate_frontend()
    manifest = json.loads((ROOT / 'oko/frontend_build/current.json').read_text())
    required = {'index.html', 'docs/index.html', 'docs/initializer.js', 'docs/swagger-ui.css', 'docs/swagger-ui-bundle.js'}
    if not required.issubset(manifest['assets']):
        raise RuntimeError('Package lacks built reader/API documentation. Run npm run build.')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='oko-package-') as directory:
        stage = Path(directory)
        shutil.copy2(ROOT / 'pyproject.toml', stage / 'pyproject.toml')
        shutil.copytree(ROOT / 'oko', stage / 'oko', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'frontend_build'))
        built = stage / 'oko/frontend_build'
        shutil.copytree(generation, built / 'generations' / generation.name)
        shutil.copy2(ROOT / 'oko/frontend_build/current.json', built / 'current.json')
        environment = dict(os.environ, PYTHONPATH=str(stage))
        subprocess.run([sys.executable, '-c', 'from setuptools.build_meta import build_wheel; import sys; print(build_wheel(sys.argv[1]))', str(output)], cwd=stage, env=environment, check=True)
    print(f'Complete Oko wheel built in {output}')


if __name__ == '__main__':
    main()
