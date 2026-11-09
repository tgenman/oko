import json
import pytest

from oko.storage.transaction import transaction
from tests.support.storage import Store


@pytest.mark.parametrize(('module','prefix','version'), [
    ('subtitle_problems','subtitle:reservation:','oko-subtitle-v1'),
    ('youtube_fetch_problems','youtube-fetch:reservation:','oko-youtube-fetch-v1'),
])
def test_cleanup_removes_only_well_formed_orphan_pointers(tmp_path, postgres_url, module, prefix, version):
    from importlib import import_module
    cleanup=import_module('oko.storage.'+module).cleanup_pointers
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        live='a'*32
        orphan='b'*32
        malformed='c'*32
        with transaction(store.db):
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background','{}',1)",(live,prefix+live))
            for identity,body in [(live,json.dumps([version,'1','x'])),(orphan,json.dumps([version,'1','x'])),(malformed,'invalid')]:
                store.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(prefix+identity,body))
            cursor,done=cleanup(store,None)
            assert cursor==prefix+malformed and not done
            assert cleanup(store,cursor)==(None,True)
            keys={row['key'] for row in store.db.execute('SELECT key FROM state WHERE key=ANY(%s)',([prefix+live,prefix+orphan,prefix+malformed],))}
            assert keys=={prefix+live,prefix+malformed}
    finally:
        store.db.close()
