#!/usr/bin/env python3
"""Write a public, secret-free release manifest for one checked Linux image."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sha', required=True)
    parser.add_argument('--digest', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}', args.sha) or not re.fullmatch('sha256:[0-9a-f]{64}', args.digest):
        raise SystemExit('Invalid immutable release identity')
    from oko.processing.codex_bridge.protocol import VERSION
    from oko.storage.postgres_schema import MIGRATIONS
    pin = json.loads(Path('oko/processing/codex_runtime.json').read_text())
    value = dict(version=1, sha=args.sha, platform='linux/amd64',
                 image='ghcr.io/tgenman/oko@'+args.digest,
                 image_tag='ghcr.io/tgenman/oko:'+args.sha,
                 helper=dict(release=args.sha, protocol=VERSION), codex=pin,
                 schema=dict(minimum=len(MIGRATIONS), maximum=len(MIGRATIONS)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2)+'\n')


if __name__ == '__main__':
    main()
