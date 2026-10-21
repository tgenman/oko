#!/usr/bin/env python3
"""Git-bounded snapshots and explicit, task-only commit preparation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


class TaskError(Exception):
    pass


def git(root, *args, env=None, data=None):
    result = subprocess.run(['git', *args], cwd=root, env=env, input=data, capture_output=True)
    if result.returncode:
        raise TaskError(result.stderr.decode(errors='replace').strip() or 'Git command failed')
    return result.stdout


def relative(name):
    path = Path(name)
    if path.is_absolute() or not path.parts or any(p in ('.git', '..') for p in path.parts) or str(path) != name:
        raise TaskError('Unsafe repository path: ' + name)
    return path.parts


def read_file(root, name):
    """Walk named Git paths with no-follow directory descriptors."""
    parts = relative(name)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
        info = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            return ('symlink', '120000', os.fsencode(os.readlink(parts[-1], dir_fd=descriptor)))
        if not stat.S_ISREG(info.st_mode):
            raise TaskError('Unsupported Git file: ' + name)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(fd, 'rb') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise TaskError('Unsupported Git file: ' + name)
            return ('file', '100755' if os.fstat(handle.fileno()).st_mode & 0o111 else '100644', handle.read())
    except FileNotFoundError:
        return None
    except OSError as error:
        raise TaskError('Cannot safely read Git path: ' + name) from error
    finally:
        os.close(descriptor)


def inventory(root):
    names = git(root, 'ls-files', '-co', '--exclude-standard', '-z').split(b'\0')
    return {os.fsdecode(name) for name in names if name}


def index_entries(root):
    entries = {}
    if any(line and line[:1] != b'H' for line in git(root, 'ls-files', '-v', '-z').split(b'\0')):
        raise TaskError('Index flags such as skip-worktree/assume-unchanged are not supported')
    if git(root, 'diff', '--cached', '--name-only', '--ita-visible-in-index', '-z') != git(root, 'diff', '--cached', '--name-only', '--ita-invisible-in-index', '-z'):
        raise TaskError('Index intent-to-add entries are not supported')
    for line in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not line:
            continue
        header, name = line.split(b'\t', 1)
        mode, digest, stage = header.decode().split()
        if stage != '0' or mode == '160000':
            raise TaskError('Resolve index conflicts/submodules before taking a task snapshot')
        entries[os.fsdecode(name)] = [mode, digest]
    return entries


def external_directory(root, path):
    directory = path.resolve()
    if directory == root or root in directory.parents:
        raise TaskError('Snapshot/preparation directory must be outside the checkout')
    if directory.exists():
        raise TaskError('Output directory already exists')
    directory.mkdir(parents=True)
    return directory


def capture(root, names, directory):
    (directory / 'files').mkdir(exist_ok=True)
    files = {}
    for name in sorted(names):
        value = read_file(root, name)
        if value is None:
            continue
        kind, mode, data = value
        digest = hashlib.sha256(data).hexdigest()
        (directory / 'files' / digest).write_bytes(data)
        files[name] = dict(kind=kind, mode=mode, digest=digest)
    return files


def save_manifest(directory, value):
    (directory / 'manifest.json').write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n')


def snapshot(root, output):
    head, index = git(root, 'rev-parse', 'HEAD').decode().strip(), index_entries(root)
    directory = external_directory(root, output)
    files = capture(root, inventory(root), directory)
    if head != git(root, 'rev-parse', 'HEAD').decode().strip() or index != index_entries(root):
        raise TaskError('HEAD or index changed while taking snapshot; take a fresh snapshot')
    save_manifest(directory, dict(version=1, root=str(root), head=head, index=index, files=files))
    print(directory)


def load_snapshot(root, path):
    data = json.loads((path / 'manifest.json').read_text())
    if data['version'] != 1 or data['root'] != str(root):
        raise TaskError('Snapshot belongs to a different checkout or format')
    for name, entry in data['files'].items():
        relative(name)
        content(path, entry)
    return data


def content(directory, entry):
    if entry is None:
        return None
    digest = entry['digest']
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise TaskError('Invalid snapshot digest')
    stored = read_file(directory, 'files/' + digest)
    if stored is None or stored[0] != 'file':
        raise TaskError('Snapshot object must be a regular file')
    data = stored[2]
    if hashlib.sha256(data).hexdigest() != digest:
        raise TaskError('Snapshot file was changed')
    if entry['mode'] not in ('100644', '100755', '120000') or entry['kind'] != ('symlink' if entry['mode'] == '120000' else 'file'):
        raise TaskError('Invalid snapshot file mode')
    return entry['mode'], data


def write_file(directory, name, value):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if value is None:
        return
    mode, data = value
    if mode == '120000':
        path.symlink_to(os.fsdecode(data))
    else:
        path.write_bytes(data)
        path.chmod(0o755 if mode == '100755' else 0o644)


def task_diff(root, source):
    import tempfile
    baseline = load_snapshot(root, source)
    with tempfile.TemporaryDirectory(prefix='oko-task-diff-') as folder:
        directory = Path(folder)
        current_names = inventory(root)
        for name in sorted(set(baseline['files']) | current_names):
            write_file(directory / 'before', name, content(source, baseline['files'].get(name)))
            value = read_file(root, name) if name in current_names else None
            write_file(directory / 'after', name, (value[1], value[2]) if value else None)
        (directory / 'before').mkdir(exist_ok=True)
        (directory / 'after').mkdir(exist_ok=True)
        result = subprocess.run(['git', '-c', 'core.quotePath=false', 'diff', '--no-index', '--binary', 'before', 'after'], cwd=directory, capture_output=True)
        if result.returncode not in (0, 1):
            raise TaskError(result.stderr.decode(errors='replace'))
        for line in result.stdout.splitlines(keepends=True):
            if line.startswith((b'diff --git ', b'--- ', b'+++ ', b'Binary files ', b'rename from ', b'rename to ')):
                line = line.replace(b'a/before/', b'a/').replace(b'b/before/', b'b/').replace(b'a/after/', b'a/').replace(b'b/after/', b'b/')
                for prefix in (b'rename from ', b'rename to '):
                    for tree in (b'before/', b'after/'):
                        line = line.replace(prefix + tree, prefix).replace(prefix + b'"' + tree, prefix + b'"')
            sys.stdout.buffer.write(line)



def head_entries(root, revision):
    result = {}
    for line in git(root, 'ls-tree', '-r', '-z', revision).split(b'\0'):
        if not line:
            continue
        header, name = line.split(b'\t', 1)
        mode, kind, digest = header.decode().split()
        if kind != 'blob':
            raise TaskError('Submodules are not supported')
        result[os.fsdecode(name)] = [mode, digest]
    return result


def git_content(root, entry):
    return (entry[0], git(root, 'cat-file', 'blob', entry[1])) if entry else None


def merge(directory, name, current, base, other):
    if other == base or current == other:
        return current
    if current == base:
        return other
    if not all(value and value[0] in ('100644', '100755') and b'\0' not in value[1] for value in (current, base, other)):
        raise TaskError('Conflict (binary, symlink or deletion): ' + name)
    modes = [value[0] for value in (current, base, other)]
    if modes[0] != modes[1] and modes[2] != modes[1] and modes[0] != modes[2]:
        raise TaskError('Mode conflict: ' + name)
    paths = []
    for label, value in zip(('task', 'baseline', 'other'), (current, base, other)):
        path = directory / label
        path.write_bytes(value[1])
        paths.append(str(path))
    result = subprocess.run(['git', 'merge-file', '-p', '-L', 'task', '-L', 'snapshot', '-L', 'HEAD/index', *paths], capture_output=True)
    if result.returncode:
        write_file(directory / 'conflicts', name, ('100644', result.stdout))
        raise TaskError('Conflict: ' + name + '; inspect ' + str(directory / 'conflicts' / name))
    return (modes[2] if modes[0] == modes[1] else modes[0], result.stdout)


def make_index(root, path, entries):
    env = dict(os.environ, GIT_INDEX_FILE=str(path))
    git(root, 'read-tree', '--empty', env=env)
    records = b''.join((entry[0] + ' ' + entry[1] + '\t').encode() + os.fsencode(name) + b'\0' for name, entry in entries.items())
    git(root, 'update-index', '-z', '--index-info', env=env, data=records)
    return git(root, 'write-tree', env=env).decode().strip()


def blob_entry(root, value):
    if value is None:
        return None
    return [value[0], git(root, 'hash-object', '-w', '--stdin', data=value[1]).decode().strip()]


def prepare(root, source, output, names):
    baseline = load_snapshot(root, source)
    if git(root, 'rev-parse', 'HEAD').decode().strip() != baseline['head'] or index_entries(root) != baseline['index']:
        raise TaskError('HEAD or index changed since snapshot; take a fresh snapshot')
    current_names = inventory(root)
    allowed = set(baseline['files']) | current_names
    if not set(names) <= allowed:
        raise TaskError('Selected files must come from Git inventory/snapshot')
    directory = external_directory(root, output)
    heads = head_entries(root, baseline['head'])
    entries = dict(heads)
    initial = {}
    for name in names:
        relative(name)
        current = read_file(root, name) if name in current_names else None
        initial[name] = (current[1], current[2]) if current else None
        selected = merge(directory, name, initial[name],
                         content(source, baseline['files'].get(name)), git_content(root, heads.get(name)))
        write_file(directory / 'tree', name, selected)
        entry = blob_entry(root, selected)
        if entry is None:
            entries.pop(name, None)
        else:
            entries[name] = entry
    # Rebase the ORIGINAL staged changes onto the task-only result before
    # committing, so a conflict cannot surprise us after HEAD has changed.
    rebased = dict(entries)
    for name in set(heads) | set(baseline['index']):
        original = baseline['index'].get(name)
        if original == heads.get(name):
            continue
        selected = merge(directory, name, git_content(root, entries.get(name)),
                         git_content(root, heads.get(name)), git_content(root, original))
        entry = blob_entry(root, selected)
        if entry is None:
            rebased.pop(name, None)
        else:
            rebased[name] = entry
    tree = make_index(root, directory / 'index', entries)
    rebased_tree = make_index(root, directory / 'rebased-index', rebased)
    working = capture(root, set(names) & inventory(root), directory)
    if any(content(directory, working.get(name)) != initial[name] for name in names):
        raise TaskError('Working files changed during preparation')
    if index_entries(root) != baseline['index'] or git(root, 'rev-parse', 'HEAD').decode().strip() != baseline['head']:
        raise TaskError('HEAD or index changed during preparation')
    env = dict(os.environ, GIT_INDEX_FILE=str(directory / 'index'))
    git(root, 'diff', '--cached', '--check', env=env)
    save_manifest(directory, dict(version=1, root=str(root), head=baseline['head'], original_index=baseline['index'],
                                 task_tree=tree, rebased_tree=rebased_tree, rebased_index_digest=hashlib.sha256((directory / 'rebased-index').read_bytes()).hexdigest(), files=working, selected=names))
    sys.stdout.buffer.write(git(root, 'diff', '--cached', '--stat', env=env))
    print('Prepared:', directory)


def prepared_entries(root, directory, plan):
    env = dict(os.environ, GIT_INDEX_FILE=str(directory / 'index'))
    if git(root, 'write-tree', env=env).decode().strip() != plan['task_tree']:
        raise TaskError('Prepared index changed; prepare again')
    entries = head_entries(root, plan['task_tree'])
    for name in plan['selected']:
        shown = read_file(directory / 'tree', name)
        if ((shown[1], shown[2]) if shown else None) != git_content(root, entries.get(name)):
            raise TaskError('Prepared file changed: ' + name)
    return entries


def checkout(root, prepared, output):
    """Materialize the immutable task tree with its real base and an independent index."""
    plan = load_snapshot(root, prepared)
    prepared_entries(root, prepared, plan)
    checkout_tree(root, plan['head'], plan['task_tree'], output)
    reuse_dependencies(root, output)


def reuse_dependencies(root, target):
    """Reuse installed dependencies only; preserve any captured destination."""
    for name in ('node_modules', '.venv'):
        dependency = root / name
        destination = target / name
        if dependency.is_dir() and not os.path.lexists(destination):
            destination.symlink_to(dependency.resolve(), target_is_directory=True)

def checkout_tree(root, head, tree, output):
    """Materialize a captured tree without sharing an index or remotes."""
    directory = external_directory(root, output)
    env = dict(os.environ)
    env.pop('GIT_INDEX_FILE', None)
    git(root, 'clone', '--local', '--no-hardlinks', '--no-checkout', '--', str(root), str(directory), env=env)
    git(directory, 'update-ref', '--no-deref', 'HEAD', head, env=env)
    git(directory, 'read-tree', '--reset', '-u', tree, env=env)
    git(directory, 'remote', 'remove', 'origin', env=env)
    print('Checkout:', directory)
    print('Base:', head)
    print('Task tree:', tree)


def commit(root, directory, message):
    plan = load_snapshot(root, directory)
    if git(root, 'rev-parse', 'HEAD').decode().strip() != plan['head'] or index_entries(root) != plan['original_index']:
        raise TaskError('HEAD or index changed after preparation')
    current_names = inventory(root)
    for name in plan['selected']:
        current = read_file(root, name) if name in current_names else None
        expected = content(directory, plan['files'].get(name))
        if ((current[1], current[2]) if current else None) != expected:
            raise TaskError('Working file changed after preparation: ' + name)
    restored = read_file(directory, 'rebased-index')
    if restored is None or restored[0] != 'file' or hashlib.sha256(restored[2]).hexdigest() != plan['rebased_index_digest']:
        raise TaskError('Prepared restoration index changed')
    env = dict(os.environ, GIT_INDEX_FILE=str(directory / 'index'))
    prepared_entries(root, directory, plan)
    git(root, 'diff', '--cached', '--check', env=env)
    sys.stdout.buffer.write(git(root, 'commit', '-m', message, env=env))
    if git(root, 'rev-parse', 'HEAD^{tree}').decode().strip() != plan['task_tree']:
        raise TaskError('Commit hook changed the tree; original index retained for manual reconciliation')
    if git(root, 'rev-parse', 'HEAD^').decode().strip() != plan['head']:
        raise TaskError('Commit parent changed concurrently; original index retained')
    index = Path(git(root, 'rev-parse', '--git-path', 'index').decode().strip())
    if not index.is_absolute():
        index = root / index
    lock = Path(str(index) + '.lock')
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise TaskError('Commit created; index locked concurrently, retained for manual reconciliation') from None
    owns_lock = True
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            if index.exists():
                os.fchmod(handle.fileno(), stat.S_IMODE(index.stat().st_mode))
            if index_entries(root) != plan['original_index']:
                raise TaskError('Index changed concurrently; retained for manual reconciliation')
            handle.write(restored[2])
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(lock, index)
            owns_lock = False
    finally:
        if owns_lock:
            lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('snapshot', 'diff'):
        commands.add_parser(name).add_argument('directory', type=Path)
    command = commands.add_parser('prepare')
    command.add_argument('directory', type=Path)
    command.add_argument('--output', type=Path, required=True)
    command.add_argument('--files', nargs='+', required=True)
    command = commands.add_parser('checkout')
    command.add_argument('directory', type=Path)
    command.add_argument('--output', type=Path, required=True)
    command = commands.add_parser('commit')
    command.add_argument('directory', type=Path)
    command.add_argument('--message', required=True)
    args = parser.parse_args()
    try:
        root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').decode().strip()).resolve()
        if args.command == 'snapshot':
            snapshot(root, args.directory)
        elif args.command == 'diff':
            task_diff(root, args.directory)
        elif args.command == 'prepare':
            prepare(root, args.directory, args.output, list(dict.fromkeys(args.files)))
        elif args.command == 'checkout':
            checkout(root, args.directory, args.output)
        else:
            commit(root, args.directory, args.message)
    except (TaskError, OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
