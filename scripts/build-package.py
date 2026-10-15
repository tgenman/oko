#!/usr/bin/env python3
"""Build a complete wheel from validated assets in a temporary source tree."""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from candidate import context

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    candidate = context(ROOT)
    output = args.output.resolve()
    if candidate and not output.is_relative_to(ROOT.parent):
        parser.error('Unreviewed candidate wheels must remain inside the isolated candidate directory')
    if not candidate and (ROOT / 'oko/candidate.json').exists():
        parser.error('An unreviewed candidate package cannot be built as a reviewed release')
    # Check derived libraries without altering the checkout; runtime/package
    # freshness validation also checks canonical/backend/frontend digests.
    subprocess.run(['node', '--import', 'tsx', str(ROOT / 'scripts/generate-api.mts'), 'check'], cwd=ROOT, check=True)
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
        project = (ROOT / 'pyproject.toml').read_text()
        if candidate:
            project = re.sub(r'(?m)^version = "([^"]+)"$', lambda match: 'version = "' + match[1].split('+')[0] + '+candidate.' + candidate['contract_digest'][:12] + '"', project, count=1)
        if candidate:
            project = project.replace('include = [', 'include = [\n    { path = "oko/candidate.json", format = ["wheel"] },', 1)
        (stage / 'pyproject.toml').write_text(project)
        shutil.copytree(ROOT / 'oko', stage / 'oko', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'frontend_build'))
        if candidate:
            (stage / 'oko/candidate.json').write_text(json.dumps(dict(candidate, publication_allowed=False), indent=2) + '\n')
        built = stage / 'oko/frontend_build'
        shutil.copytree(generation, built / 'generations' / generation.name)
        shutil.copy2(ROOT / 'oko/frontend_build/current.json', built / 'current.json')
        environment = dict(os.environ, PYTHONPATH=str(stage))
        subprocess.run([sys.executable, '-c', 'from poetry.core.masonry.api import build_wheel; import sys; print(build_wheel(sys.argv[1]))', str(output)], cwd=stage, env=environment, check=True)
    print(f'Complete Oko wheel built in {output}')


if __name__ == '__main__':
    main()
