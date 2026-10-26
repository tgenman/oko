from pathlib import Path
import json
import shutil

import pytest

from tests.support.app import create_app


def test_missing_build_fails_before_creating_reader_storage(tmp_path: Path, monkeypatch):
    monkeypatch.setattr('oko.reader.static.FRONTEND_ROOT', tmp_path / 'missing-build')
    data = tmp_path / 'reader'
    with pytest.raises(RuntimeError, match='npm run build'):
        create_app(data, token='test', database_url='postgresql://unused.invalid/test')
    assert not data.exists()


@pytest.mark.parametrize('failure', ['stale', 'damaged'])
def test_stale_or_damaged_build_fails_before_creating_reader_storage(tmp_path: Path, monkeypatch, failure):
    from oko.reader.static import FRONTEND_ROOT
    build = tmp_path / 'build'
    shutil.copytree(FRONTEND_ROOT, build)
    manifest = json.loads((build / 'current.json').read_text())
    if failure == 'stale':
        manifest['source'] = '0' * 64
        (build / 'current.json').write_text(json.dumps(manifest))
    else:
        (build / 'generations' / manifest['generation'] / 'index.html').write_text('damaged')
    monkeypatch.setattr('oko.reader.static.FRONTEND_ROOT', build)
    data = tmp_path / 'reader'
    with pytest.raises(RuntimeError, match='npm run build'):
        create_app(data, token='test', database_url='postgresql://unused.invalid/test')
    assert not data.exists()
