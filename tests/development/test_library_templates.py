"""Templates must retain real Store output without sharing mutable scenario data."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.library_templates import LibraryTemplates
from tests.support.performance_seed import seed
from tests.support.reading_cost import library

HEADERS = {'Authorization': 'Bearer test'}


@pytest.mark.parametrize('kind', ['reading', 'saved'])
def test_library_copies_remain_independent_through_http(tmp_path, kind):
    def prepare(root):
        if kind == 'saved':
            return seed(root, 26)
        return library(root, [26] * 4)

    first, second, third = (tmp_path / name for name in ('first', 'second', 'third'))
    result = prepare(first)
    if isinstance(result, dict):
        result['feeds'].clear()  # Metadata returned to a caller is private too.
    else:
        result[0].clear()
    metadata = prepare(second)
    assert len(metadata['feeds'] if isinstance(metadata, dict) else metadata[0]) == 4
    with TestClient(create_app(first, api_only=True, token='test'), headers=HEADERS) as a, TestClient(create_app(second, api_only=True, token='test'), headers=HEADERS) as b:
        cards = a.get('/api/v3/cards').json()
        assert len(cards) == 104
        assert cards == b.get('/api/v3/cards').json()
        item = cards[0]['id']
        content = b.get('/api/v3/materials/' + item).json()['content']
        assert a.post('/api/v3/materials/' + item + '/viewed').status_code == 200
        assert a.patch('/api/v3/settings/oko/global', json={'prompt_theses': 'first only'}).status_code == 200
        assert len(a.get('/api/v3/cards').json()) == 103
        assert len(b.get('/api/v3/cards').json()) == 104
        assert b.get('/api/v3/materials/' + item).json()['content'] == content
        assert b.get('/api/v3/settings/oko/global').json()['effective']['prompt_theses']['value'] != 'first only'
    prepare(third)
    with TestClient(create_app(third, api_only=True, token='test'), headers=HEADERS) as c:
        assert len(c.get('/api/v3/cards').json()) == 104
        assert c.get('/api/v3/materials/' + item).json()['content'] == content


def test_failed_template_build_is_not_reused_and_cleanup_removes_sources(tmp_path):
    templates = LibraryTemplates()
    directory = Path(templates.directory.name)
    attempts = []
    def build(root):
        attempts.append(root)
        (root / 'fixture').write_text(str(len(attempts)))
        if len(attempts) == 1:
            raise RuntimeError('incomplete fixture')
        return {'items': ['original']}
    try:
        with pytest.raises(RuntimeError, match='incomplete fixture'):
            templates.copy(tmp_path / 'failed', 'same', build)
        assert not attempts[0].exists()
        first = templates.copy(tmp_path / 'first', 'same', build)
        first['items'].clear()
        second = templates.copy(tmp_path / 'second', 'same', build)
        assert len(attempts) == 2
        assert second == {'items': ['original']}
        assert (tmp_path / 'second/fixture').read_text() == '2'
        with pytest.raises(ValueError, match='destination must be empty'):
            templates.copy(tmp_path / 'second', 'same', build)
    finally:
        templates.close()
    assert not directory.exists()
    assert (tmp_path / 'second/fixture').read_text() == '2'


def test_run_templates_publish_once_across_processes_and_copy_private_metadata(tmp_path):
    import subprocess
    import sys
    import json
    from tests.support.library_templates import RunLibraryTemplates
    shared=tmp_path/'run'
    shared.mkdir()
    script="""
from pathlib import Path
import json,sys,time
from tests.support.library_templates import RunLibraryTemplates
shared,destination=map(Path,sys.argv[1:])
def build(root):
    with (shared/'builds').open('a') as count:count.write('built\\n')
    time.sleep(.05)  # Overlap competing workers at publication.
    (root/'content').write_text('immutable')
    return {'items':['original']}
print(json.dumps(RunLibraryTemplates(shared).copy(destination,('fixture',1),build)))
"""
    processes=[subprocess.Popen([sys.executable,'-c',script,str(shared),str(tmp_path/str(i))],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(3)]
    try:
        for process in processes:
            stdout,stderr=process.communicate(timeout=15)
            assert process.returncode==0,stderr
            assert json.loads(stdout)=={'items':['original']}
        assert (shared/'builds').read_text().splitlines()==['built']
        (tmp_path/'0/content').write_text('private')
        assert (tmp_path/'1/content').read_text()=='immutable'
        templates=RunLibraryTemplates(shared)
        def forbidden(_):raise AssertionError('Published seed rebuilt')
        metadata=templates.copy(tmp_path/'next',('fixture',1),forbidden)
        metadata['items'].clear()
        assert templates.copy(tmp_path/'last',('fixture',1),forbidden)=={'items':['original']}
        assert (tmp_path/'last/content').read_text()=='immutable'
        # A new run must build independently, even for the same logical key.
        fresh=tmp_path/'new-run';fresh.mkdir()
        def build(root):
            (root/'content').write_text('fresh')
            return {'items':['fresh']}
        assert RunLibraryTemplates(fresh).copy(tmp_path/'fresh',('fixture',1),build)=={'items':['fresh']}
    finally:
        for process in processes:
            if process.poll() is None:process.kill()
            process.wait(timeout=5)


def test_run_templates_never_publish_a_failed_builder(tmp_path):
    from tests.support.library_templates import RunLibraryTemplates
    shared=tmp_path/'run';shared.mkdir()
    templates=RunLibraryTemplates(shared)
    def broken(root):
        (root/'partial').write_text('unfinished')
        raise RuntimeError('interrupted seed')
    with pytest.raises(RuntimeError,match='interrupted seed'):
        templates.copy(tmp_path/'failed','fixture',broken)
    assert not list(shared.glob('*.result'))
    def complete(root):
        assert not (root/'partial').exists()
        (root/'content').write_text('complete')
        return ['complete']
    assert templates.copy(tmp_path/'ok','fixture',complete)==['complete']
    assert (tmp_path/'ok/content').read_text()=='complete'
