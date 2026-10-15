#!/usr/bin/env python3
"""Conservative wire-change gate; semantic compatibility still needs review."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from api import contract
from candidate import context

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTATION = {'description', 'summary', 'title', 'example', 'examples', 'externalDocs', '$comment'}
MAPS = {'properties', 'patternProperties', '$defs', 'schemas', 'paths', 'responses',
        'content', 'headers', 'securitySchemes', 'callbacks', 'encoding', 'links'}


def wire(value, mapping=False):
    if isinstance(value, dict):
        return {key: child if key in {'default', 'const', 'enum'} else wire(child, key in MAPS)
                for key, child in value.items() if mapping or key not in DOCUMENTATION}
    if isinstance(value, list):
        return [wire(child) for child in value]
    return value


def semantic(document):
    return wire({key: value for key, value in document.items() if key not in {'info', 'tags', 'externalDocs'}})


def digest(document):
    canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    return hashlib.sha256(canonical.encode()).hexdigest()


def changes(before, after, pointer=''):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            path = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
            result.extend([path] if key not in before or key not in after else changes(before[key], after[key], path))
        return result
    return [pointer or '/']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check', 'baseline'])
    parser.add_argument('--spec', type=Path, default=ROOT / 'api/openapi.yaml')
    parser.add_argument('--baseline', type=Path, default=ROOT / 'api/baseline.json')
    parser.add_argument('--review', type=Path)
    args = parser.parse_args()
    candidate = context(ROOT)
    if candidate and args.spec.resolve().is_relative_to(ROOT.parent / 'tmp') and args.baseline.resolve().is_relative_to(ROOT.parent / 'tmp'):
        candidate = None  # Explicit synthetic documents still use the strict gate.
    if candidate and (args.command != 'check' or args.spec.resolve() != ROOT / 'api/openapi.yaml' or args.baseline.resolve() != ROOT / 'api/baseline.json'):
        parser.exit(1, 'Candidate context cannot approve/update a baseline or select different contract bytes.\n')
    current = contract(args.spec)
    previous = contract(args.baseline)
    modified = changes(semantic(previous), semantic(current))
    if args.command == 'check':
        if candidate:
            status = 'PENDING_REVIEW' if modified else 'PASS'
            Path(candidate['compatibility_report']).write_text(json.dumps(dict(status=status, changes=modified,
                before=semantic(previous), after=semantic(current), from_digest=digest(previous), to_digest=digest(current),
                source_digest=candidate['source_digest']), ensure_ascii=False, indent=2) + '\n')
            print(f'{status}: candidate compatibility evidence saved; artifacts remain UNREVIEWED')
            return
        if modified:
            parser.exit(1, 'Potential incompatible wire changes require semantic review and an explicit baseline update:\n'
                        + '\n'.join(modified[:40]) + ('\n... additional changes omitted' if len(modified) > 40 else '')
                        + '\nRun npm run api:baseline -- --review <review.md> after changing info.version.\n')
        print('API wire contract matches reviewed baseline (documentation changes ignored)')
        return
    old_version, new_version = previous['info']['version'], current['info']['version']
    if old_version == new_version:
        parser.exit(1, 'Baseline update requires a changed info.version.\n')
    if not args.review or not args.review.is_file() or not args.review.read_text().strip():
        parser.exit(1, 'Baseline update requires a nonempty semantic review document via --review.\n')
    args.baseline.write_text(json.dumps(current, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    args.baseline.with_suffix('.review.json').write_text(json.dumps({
        'from_version': old_version, 'to_version': new_version,
        'from_digest': digest(previous), 'to_digest': digest(current),
        'semantic_review': args.review.read_text(),
    }, ensure_ascii=False, indent=2) + '\n')
    print(f'Explicitly updated API baseline {old_version} -> {new_version}; review: {args.review}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        sys.exit(str(error))
