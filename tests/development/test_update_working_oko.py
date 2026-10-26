"""Update command uses fetched main, never unfinished development files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/update-working-oko'


def git(root, *arguments):
    return subprocess.check_output(['git', '-C', str(root), *arguments], text=True).strip()


def test_retired_local_update_preserves_checkout_without_fetch_or_build(tmp_path):
    upstream = tmp_path / 'upstream'
    upstream.mkdir()
    git(upstream, 'init', '-q', '-b', 'main')
    git(upstream, 'config', 'user.name', 'Synthetic')
    git(upstream, 'config', 'user.email', 'synthetic@example.invalid')
    (upstream / 'scripts').mkdir()
    controller = upstream / 'scripts/oko-release'
    controller.write_text('''import json, os, pathlib, subprocess, sys
root = pathlib.Path(__file__).resolve().parents[1]
operation = 'prepare' if 'prepare' in sys.argv else 'update'
entry = dict(operation=operation, args=sys.argv[1:], source=(root/'source.txt').read_text(), root=str(root),
             dirty=subprocess.check_output(['git','-C',str(root),'status','--porcelain'],text=True))
with open(os.environ['UPDATE_LOG'], 'a') as output: output.write(json.dumps(entry)+'\\n')
if operation == 'prepare' and os.environ['FAIL_PREPARE'] == '1': sys.exit(17)
''')
    (upstream / 'source.txt').write_text('old release')
    git(upstream, 'add', '.')
    git(upstream, 'commit', '-qm', 'old')
    working = tmp_path / 'development'
    subprocess.run(['git', 'clone', '-q', str(upstream), str(working)], check=True)
    old_head = git(working, 'rev-parse', 'HEAD')
    shutil.copyfile(SCRIPT, working / 'scripts/update-working-oko')
    (working / 'source.txt').write_text('unfinished local changes')
    (working / 'notes.txt').write_text('untracked local file')
    (upstream / 'source.txt').write_text('published main')
    git(upstream, 'add', 'source.txt')
    git(upstream, 'commit', '-qm', 'new main')
    expected = git(upstream, 'rev-parse', 'HEAD')
    log = tmp_path / 'updates.jsonl'
    result = subprocess.run([sys.executable, str(working / 'scripts/update-working-oko')],
                            cwd=tmp_path, env=dict(os.environ, UPDATE_LOG=str(log), FAIL_PREPARE='0'),
                            capture_output=True, text=True)
    assert result.returncode == 1 and 'retired' in result.stderr
    assert not log.exists()
    assert git(working, 'rev-parse', 'origin/main') == old_head
    assert git(working, 'rev-parse', 'HEAD') == old_head
    assert (working / 'source.txt').read_text() == 'unfinished local changes'
    assert (working / 'notes.txt').read_text() == 'untracked local file'
