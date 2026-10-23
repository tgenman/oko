"""Offline activation of a temporary empty publication store for reader scenarios."""
from oko.storage.migration import migrate_publications
from tests.support.app import create_app
from tests.support.v3 import TestClient

HEADERS={'Authorization':'Bearer test'}


def migrated_root(tmp_path):
    source = tmp_path/'old'
    with TestClient(create_app(source, token='test', api_only=True), headers=HEADERS):
        pass
    target = tmp_path/'migration'
    assert migrate_publications(source, target)['success']
    return target/'storage'

