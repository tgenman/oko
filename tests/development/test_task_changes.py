"""Task CLI in a temporary Git repo, observing files, index and commit contents."""
import json
import os
from pathlib import Path
import subprocess
import sys

CLI = Path(__file__).resolve().parents[2] / 'scripts/task_changes.py'


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root).decode().strip()


def repository(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.test')
    (root / '.gitignore').write_text('data/\n')
    (root / 'source.txt').write_text('first\nsecond\nthird\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'base')
    return root


def run(root, *args):
    return subprocess.run([sys.executable, str(CLI), *map(str, args)], cwd=root, capture_output=True, text=True)


def test_snapshot_uses_git_inventory_and_never_follows_symlinks(tmp_path):
    root = repository(tmp_path)
    (root / 'data').mkdir()
    (root / 'data/private.txt').write_text('private content')
    (root / 'new file.txt').write_text('new content')
    (root / 'link').symlink_to(root / 'data/private.txt')
    snapshot = tmp_path / 'snapshot'
    result = run(root, 'snapshot', snapshot)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    assert set(manifest['files']) == {'.gitignore', 'source.txt', 'new file.txt', 'link'}
    assert manifest['files']['link']['kind'] == 'symlink'
    assert not (snapshot / 'files/data').exists()
    assert not any(b'private content' in p.read_bytes() for p in (snapshot / 'files').rglob('*') if p.is_file())


def test_prepare_and_commit_preserve_staged_and_unstaged_user_changes(tmp_path):
    root = repository(tmp_path)
    (root / 'staged.txt').write_text('staged user change\n')
    git(root, 'add', 'staged.txt')
    (root / 'source.txt').write_text('user first\nsecond\nthird\n')
    snapshot = tmp_path / 'snapshot'
    assert run(root, 'snapshot', snapshot).returncode == 0
    (root / 'source.txt').write_text('user first\nsecond\ntask third\n')
    (root / 'new file.txt').write_bytes(b'\x00task binary\n')
    prepared = tmp_path / 'prepared'
    result = run(root, 'prepare', snapshot, '--output', prepared, '--files', 'source.txt', 'new file.txt')
    assert result.returncode == 0, result.stderr
    assert git(root, 'diff', '--cached', '--name-only') == 'staged.txt'
    assert git(root, 'show', 'HEAD:source.txt') == 'first\nsecond\nthird'
    assert run(root, 'commit', prepared, '--message', 'task').returncode == 0
    assert git(root, 'show', 'HEAD:source.txt') == 'first\nsecond\ntask third'
    assert git(root, 'diff', '--cached', '--name-only') == 'staged.txt'
    assert (root / 'source.txt').read_text() == 'user first\nsecond\ntask third\n'
    assert subprocess.check_output(['git', 'show', 'HEAD:new file.txt'], cwd=root) == b'\x00task binary\n'
    assert git(root, 'show', '--format=', '--name-only', 'HEAD') == 'new file.txt\nsource.txt'


def test_snapshot_objects_cannot_be_redirected_outside_the_snapshot(tmp_path):
    root = repository(tmp_path)
    snapshot = tmp_path / 'snapshot'
    assert run(root, 'snapshot', snapshot).returncode == 0
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    digest = manifest['files']['source.txt']['digest']
    outside = tmp_path / 'outside'
    outside.write_text('first\nsecond\nthird\n')
    stored = snapshot / 'files' / digest
    stored.unlink()
    stored.symlink_to(outside)
    result = run(root, 'diff', snapshot)
    assert result.returncode == 1
    assert 'Snapshot' in result.stderr


def test_diff_and_preparation_handle_rename_deletion_and_binary_files(tmp_path):
    root = repository(tmp_path)
    snapshot = tmp_path / 'snapshot'
    assert run(root, 'snapshot', snapshot).returncode == 0
    (root / 'source.txt').rename(root / 'renamed source.txt')
    (root / 'binary').write_bytes(b'\x00\xffnew')
    result = run(root, 'diff', snapshot)
    assert result.returncode == 0
    assert 'before/' not in result.stdout and 'after/' not in result.stdout
    prepared = tmp_path / 'prepared'
    assert run(root, 'prepare', snapshot, '--output', prepared, '--files', 'source.txt', 'renamed source.txt', 'binary').returncode == 0
    assert run(root, 'commit', prepared, '--message', 'rename').returncode == 0
    assert git(root, 'ls-tree', '--name-only', 'HEAD') == '.gitignore\nbinary\nrenamed source.txt'


def test_conflict_stops_before_changing_head_or_index(tmp_path):
    root = repository(tmp_path)
    (root / 'source.txt').write_text('user first\nsecond\nthird\n')
    snapshot = tmp_path / 'snapshot'
    assert run(root, 'snapshot', snapshot).returncode == 0
    (root / 'source.txt').write_text('task first\nsecond\nthird\n')
    before = git(root, 'rev-parse', 'HEAD')
    index = git(root, 'ls-files', '--stage')
    prepared = tmp_path / 'prepared'
    result = run(root, 'prepare', snapshot, '--output', prepared, '--files', 'source.txt')
    assert result.returncode == 1 and 'Conflict' in result.stderr
    assert (prepared / 'conflicts/source.txt').is_file()
    assert git(root, 'rev-parse', 'HEAD') == before and git(root, 'ls-files', '--stage') == index
    assert (root / 'source.txt').read_text() == 'task first\nsecond\nthird\n'


import pytest


@pytest.mark.parametrize('drift', ['head', 'index', 'work', 'prepared', 'payload'])
def test_commit_refuses_drift_without_overwriting_current_state(tmp_path, drift):
    root = repository(tmp_path)
    snapshot = tmp_path / 'snapshot'
    assert run(root, 'snapshot', snapshot).returncode == 0
    (root / 'source.txt').write_text('first\nsecond\ntask third\n')
    prepared = tmp_path / 'prepared'
    assert run(root, 'prepare', snapshot, '--output', prepared, '--files', 'source.txt').returncode == 0
    if drift == 'head':
        git(root, 'commit', '--allow-empty', '-qm', 'concurrent')
    elif drift == 'index':
        (root / 'other').write_text('other staged')
        git(root, 'add', 'other')
    elif drift == 'work':
        (root / 'source.txt').write_text('newer input')
    elif drift == 'payload':
        (prepared / 'tree/source.txt').write_text('edited preview')
    else:
        env = dict(os.environ, GIT_INDEX_FILE=str(prepared / 'index'))
        subprocess.run(['git', 'read-tree', '--empty'], cwd=root, env=env, check=True)
    before, index = git(root, 'rev-parse', 'HEAD'), git(root, 'ls-files', '--stage')
    result = run(root, 'commit', prepared, '--message', 'task')
    assert result.returncode == 1 and 'changed' in result.stderr
    assert git(root, 'rev-parse', 'HEAD') == before
    assert git(root, 'ls-files', '--stage') == index


def test_snapshot_refuses_symlink_ancestors_and_internal_output(tmp_path):
    root = repository(tmp_path)
    nested = root / 'folder'
    nested.mkdir()
    (nested / 'code.py').write_text('value = 1')
    git(root, 'add', 'folder/code.py')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'code.py').write_text('private')
    (nested / 'code.py').unlink()
    nested.rmdir()
    nested.symlink_to(outside, target_is_directory=True)
    assert run(root, 'snapshot', tmp_path / 'snapshot').returncode == 1
    assert run(root, 'snapshot', root / 'snapshot').returncode == 1


def test_commit_keeps_staged_and_unstaged_changes_on_the_same_file(tmp_path):
    root = repository(tmp_path)
    original = ''.join(str(i) + '\n' for i in range(20))
    (root / 'source.txt').write_text(original)
    git(root, 'add', 'source.txt')
    git(root, 'commit', '-qm', 'larger base')
    staged = original.replace('0\n', 'staged zero\n', 1)
    (root / 'source.txt').write_text(staged)
    git(root, 'add', 'source.txt')
    working = staged.replace('6\n', 'unstaged six\n', 1)
    (root / 'source.txt').write_text(working)
    snapshot = tmp_path / 'snapshot'
    assert run(root, 'snapshot', snapshot).returncode == 0
    changed = working.replace('15\n', 'task fifteen\n', 1)
    (root / 'source.txt').write_text(changed)
    prepared = tmp_path / 'prepared'
    result = run(root, 'prepare', snapshot, '--output', prepared, '--files', 'source.txt')
    assert result.returncode == 0, result.stderr
    assert run(root, 'commit', prepared, '--message', 'task').returncode == 0
    assert git(root, 'show', 'HEAD:source.txt') == original.replace('15\n','task fifteen\n',1).strip()
    assert git(root, 'show', ':source.txt') == staged.replace('15\n','task fifteen\n',1).strip()
    assert (root / 'source.txt').read_text() == changed


def test_index_restoration_excludes_concurrent_git_add(tmp_path):
    import shutil
    root = repository(tmp_path)
    snapshot = tmp_path / 'snapshot'
    assert run(root, 'snapshot', snapshot).returncode == 0
    (root / 'source.txt').write_text('first\nsecond\ntask third\n')
    prepared = tmp_path / 'prepared'
    assert run(root, 'prepare', snapshot, '--output', prepared, '--files', 'source.txt').returncode == 0
    (root / 'other').write_text('concurrent user file')
    trigger, result_path = tmp_path / 'after-commit', tmp_path / 'git-add-result'
    hook = root / '.git/hooks/post-commit'
    hook.write_text('#!' + sys.executable + '\nfrom pathlib import Path\nPath(' + repr(str(trigger)) + ').touch()\n')
    hook.chmod(0o755)
    binary = tmp_path / 'bin'
    binary.mkdir()
    real_git = shutil.which('git')
    wrapper = binary / 'git'
    wrapper.write_text('#!' + sys.executable + '\n' + f'''import subprocess,sys
from pathlib import Path
args=sys.argv[1:]
result=subprocess.run([{real_git!r},*args],capture_output=True)
if args==['ls-files','--stage','-z'] and Path({str(trigger)!r}).exists() and not Path({str(result_path)!r}).exists():
    added=subprocess.run([{real_git!r},'add','other'],capture_output=True)
    Path({str(result_path)!r}).write_text(str(added.returncode))
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
''')
    wrapper.chmod(0o755)
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'])
    result = subprocess.run([sys.executable, str(CLI), 'commit', str(prepared), '--message','task'], cwd=root, env=env, capture_output=True, text=True)
    assert result_path.exists()
    assert int(result_path.read_text()) != 0, 'concurrent git add acquired index without exclusion'
    assert result.returncode == 0, result.stderr
    assert (root / 'other').read_text() == 'concurrent user file'
    assert not (root / '.git/index.lock').exists()


def test_snapshot_rejects_intent_to_add_without_turning_it_into_staged_content(tmp_path):
    root = repository(tmp_path)
    (root / 'draft').write_text('user unstaged draft')
    git(root, 'add', '-N', 'draft')
    before = git(root, 'diff', '--cached', '--name-only')
    result = run(root, 'snapshot', tmp_path / 'snapshot')
    assert result.returncode == 1 and 'intent-to-add' in result.stderr
    assert git(root, 'diff', '--cached', '--name-only') == before
    assert 'draft' in git(root, 'diff', '--name-only')
