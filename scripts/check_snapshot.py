"""Run a full local check on a captured Git working tree, with external reports."""
import contextlib
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from task_changes import (TaskError, blob_entry, capture, checkout_tree, content,
                          git, index_entries, make_index, read_file, reuse_dependencies)


def run(root, arguments, report_dir, base):
    untracked = [os.fsdecode(name) for name in git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0') if name]
    code = [name for name in untracked if name.startswith(('frontend/', 'oko/', 'tests/', 'scripts/', 'api/'))]
    if code:
        raise TaskError('Stage new code before full checks: ' + ', '.join(code))
    directory = Path(tempfile.mkdtemp(prefix='oko-check-'))
    state = directory / 'state'
    state.mkdir()
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    index = index_entries(root)
    # Explicit Git index membership excludes private/untracked data. Staged new
    # files are included; worktree edits/deletions override their indexed bytes.
    files = capture(root, set(index), state)
    object_format = git(root, 'rev-parse', '--show-object-format').decode().strip()
    entries = {}
    for name, value in files.items():
        mode, payload = content(state, value)
        digest = hashlib.new(object_format, b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()
        # Existing index objects already exist in Git; only changed bytes need
        # hash-object subprocesses, rather than one process per repository file.
        entries[name] = [mode, digest] if index[name][1] == digest else blob_entry(root, (mode, payload))
    tree = make_index(root, state / 'index', entries)
    for name in index:
        current = read_file(root, name)
        if ((current[1], current[2]) if current else None) != content(state, files.get(name)):
            raise TaskError('Working files changed during capture: ' + name)
    if head != git(root, 'rev-parse', 'HEAD').decode().strip() or index != index_entries(root):
        raise TaskError('HEAD or index changed during capture; retry the check')
    target = directory / 'checkout'
    with contextlib.redirect_stdout(sys.stderr):
        checkout_tree(root, head, tree, target)
    env = dict(os.environ)
    env.pop('GIT_INDEX_FILE', None)
    reuse_dependencies(root, target)
    for key in ('OKO_NODE', 'OKO_PYTHON', 'OKO_BROWSER_EXECUTABLE'):
        if env.get(key):
            executable = shutil.which(env[key])
            if executable:
                env[key] = str(Path(executable).absolute())
    env['OKO_CHECK_SNAPSHOT'] = json.dumps(dict(source=str(root), checkout=str(target), head=head, tree=tree))
    if base is None:
        comparison = subprocess.run(['git', 'merge-base', 'HEAD', 'origin/main'], cwd=root, capture_output=True, text=True)
        if comparison.returncode == 0:
            base = comparison.stdout.strip()
    command = [sys.executable, str(target / 'scripts/check'), *arguments, '--in-place', '--report-dir', str(report_dir)]
    if base:
        command.extend(['--base', base])
    print('Snapshot:', target, flush=True)
    # Replace the launcher: the existing runner retains its PID and signal
    # ownership, including cleanup of separately started test processes.
    os.chdir(target)
    os.execve(sys.executable, command, env)
