import json,pytest
from tests.support.storage import Store


@pytest.mark.parametrize('canonical,aliases',[('Z',['a','é','中','😀']),('é',['Z','a','中','😀']),('Ω',['é','α','中','😀'])])
def test_tied_processor_duplicates_keep_python_byte_order(canonical,aliases,tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=dict(code='processor-config',section='action',title='legacy',consequence='c',next_step='n',objects=[],action=None,operation='producer:read',state='current',next_at=None)
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,'processor:configuration','background',%s,1)",(canonical,json.dumps(body)))
            for idx,name in enumerate(aliases):
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(name,'processor:configuration-isolated:'+str(idx),json.dumps(dict(body,processor_group_identity='processor:configuration'))))
            expected=min([canonical,*aliases])
            store.problems.recover_processor_duplicates(2)
        assert store.problems.processor_current()['id']==expected
    finally:store.db.close()
