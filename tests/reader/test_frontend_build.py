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
