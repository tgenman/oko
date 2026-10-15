"""One validated context for checks of an unpublished contract candidate."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def context(root):
    """A candidate can only run in its isolated tree with unchanged contract bytes."""
    filename = os.environ.get('OKO_CANDIDATE_CONTEXT')
    marker = root / '.oko-candidate.json'
    if not filename:
        if marker.exists():
            raise ValueError('Unreviewed candidate tree requires its candidate context')
        return None
    path = Path(filename).resolve()
    value = json.loads(path.read_text())
    if Path(value['root']).resolve() != root.resolve() and not marker.exists():
        # Synthetic fixture repositories inherit the process environment, but
        # remain subject to normal strict governance, never candidate approval.
        return None
    if (value.get('version') != 1 or value.get('status') != 'UNREVIEWED'
            or Path(value['root']).resolve() != root.resolve()
            or marker.read_bytes() != path.read_bytes()
            or digest(root / 'api/openapi.yaml') != value['contract_digest']
            or digest(root / 'api/baseline.json') != value['baseline_digest']):
        raise ValueError('Candidate context does not match this tree, contract or reviewed baseline')
    return value


def staged_context(root, stage):
    """Carry a validated parent context to exact staged bytes, never to other inputs."""
    parent = context(root)
    if parent is None:
        return None
    if (digest(stage / 'api/openapi.yaml') != parent['contract_digest']
            or digest(stage / 'api/baseline.json') != parent['baseline_digest']):
        raise ValueError('Staged candidate differs from the validated contract or reviewed baseline')
    value = dict(parent, root=str(stage.resolve()), source_root=str(root.resolve()),
                 compatibility_report=str((stage / 'compatibility.json').resolve()))
    marker = stage / '.oko-candidate.json'
    marker.write_text(json.dumps(value, indent=2) + '\n')
    return marker


def prepare(root, output, spec):
    # Reuse the tracker's safe Git inventory reader: no implicit ignored personal
    # files, directory symlink traversal, checkout resets, or index mutation.
    from task_changes import inventory, read_file, write_file
    tree = output / 'tree'
    tree.mkdir()
    state = {}
    for name in sorted(inventory(root)):
        value = read_file(root, name)
        if value is None:
            continue
        kind, mode, data = value
        if kind == 'symlink':
            target = (root / name).parent / os.fsdecode(data)
            if Path(os.fsdecode(data)).is_absolute() or not target.resolve().is_relative_to(root):
                raise ValueError('Candidate source symlink leaves checkout: ' + name)
        write_file(tree, name, (mode, data))
        state[name] = {'mode': mode, 'digest': hashlib.sha256(data).hexdigest()}
    source_digest = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    # Dependencies are read-only inputs; outputs, stores and generated libraries
    # belong to the isolated tree. Do not link user data or built frontend assets.
    for name in ('node_modules', '.venv'):
        if (root / name).exists():
            (tree / name).symlink_to(root / name, target_is_directory=True)
    (tree / 'api/openapi.yaml').write_bytes(spec.read_bytes())
    value = dict(version=1, status='UNREVIEWED', root=str(tree.resolve()),
                 source_root=str(root), source_digest=source_digest,
                 source_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
                 contract_digest=digest(tree / 'api/openapi.yaml'),
                 baseline_digest=digest(tree / 'api/baseline.json'),
                 compatibility_report=str((output / 'compatibility.json').resolve()))
    payload = json.dumps(value, indent=2) + '\n'
    path = output / 'candidate.json'
    path.write_text(payload)
    (tree / '.oko-candidate.json').write_text(payload)
    # git diff/check and mutation detection operate on the selected tree rather
    # than accidentally discovering the original repository from a parent path.
    subprocess.run(['git', 'init', '-q'], cwd=tree, check=True)
    # Directory ignore patterns do not match the dependency symlinks. Keep
    # those external inputs out of the selected source inventory and nested
    # synthetic candidate copies.
    (tree / '.git/info/exclude').write_text('/.venv\n/node_modules\n')
    for args in (['add', '-A'], ['-c', 'user.name=Candidate checks', '-c', 'user.email=candidate@localhost', 'commit', '-qm', 'Unreviewed candidate check tree']):
        subprocess.run(['git', *args], cwd=tree, check=True, stdout=subprocess.DEVNULL)
    return path, value


if __name__ == '__main__':
    try:
        value = context(Path(__file__).resolve().parents[1])
        print(json.dumps(value))
    except (ValueError, OSError, KeyError) as error:
        sys.exit(str(error))
