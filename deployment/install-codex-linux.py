#!/usr/bin/env python3
"""Install the exact public Codex Linux package; never handles account secrets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, help='Previously downloaded package, still checksum verified')
    parser.add_argument('--prefix', type=Path, default=Path('/opt/oko/codex'))
    args = parser.parse_args()
    pin = json.loads((Path(__file__).resolve().parents[1] / 'oko/processing/codex_runtime.json').read_text())
    args.prefix.mkdir(parents=True, exist_ok=True)
    target = args.prefix / pin['version']
    with tempfile.TemporaryDirectory(prefix='.install-', dir=args.prefix) as temporary:
        temporary = Path(temporary)
        archive = args.archive or temporary / 'package.tar.gz'
        if args.archive is None:
            with urllib.request.urlopen(pin['archive_url'], timeout=60) as source, archive.open('wb') as output:
                shutil.copyfileobj(source, output)
        if digest(archive) != pin['archive_sha256']:
            raise SystemExit('Codex package checksum mismatch')
        unpacked = temporary / 'runtime'
        unpacked.mkdir()
        with tarfile.open(archive) as package:
            package.extractall(unpacked, filter='data')
        metadata = json.loads((unpacked / 'codex-package.json').read_text())
        if metadata['version'] != pin['version'] or metadata['target'] != pin['target']:
            raise SystemExit('Codex package identity mismatch')
        for name, key in [('bin/codex', 'binary_sha256'), ('bin/codex-code-mode-host', 'code_mode_host_sha256')]:
            if digest(unpacked / name) != pin[key]:
                raise SystemExit('Codex component checksum mismatch')
        if target.exists():
            for name, key in [('bin/codex', 'binary_sha256'), ('bin/codex-code-mode-host', 'code_mode_host_sha256')]:
                if digest(target / name) != pin[key]:
                    raise SystemExit('Existing immutable Codex runtime differs')
        else:
            for path in unpacked.rglob('*'):
                if not path.is_symlink():
                    path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
            os.replace(unpacked, target)
    print(json.dumps({'version': pin['version'], 'binary': str(target / 'bin/codex'), 'sha256': pin['binary_sha256']}))


if __name__ == '__main__':
    main()
