import json
from tests.support.storage import Store


def test_minimum_sequence_and_superseded_negative_receipts(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='action-problems-recovered-v2'")
            store.db.execute("INSERT INTO state(key,value) VALUES ('action-problems-recovered-v1','true') ON CONFLICT DO NOTHING")
            body=json.dumps(dict(title='Command',objects=[],action=None,discardable=True))
            store.db.execute("INSERT INTO reader_action_results(sequence,id,identity,body,started,result) VALUES (-9223372036854775808,'minimum','minimum',%s,1,'failed'),(-2,'older','same',%s,1,'failed'),(0,'later','same',%s,2,'succeeded')",(body,body,body))
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='action:minimum' AND finished IS NULL").fetchone()
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='action:same' AND finished IS NULL").fetchone()
        original=store.db.execute("SELECT id FROM reader_problems WHERE identity='action:minimum' AND finished IS NULL").fetchone()[0]
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='action:minimum' AND finished IS NULL").fetchone()[0]==original
        assert store.db.execute("SELECT 1 FROM state WHERE key='action-problems-recovered-v2'").fetchone()
        assert not store.db.execute("SELECT 1 FROM state WHERE key='action-problems-recovered-v1'").fetchone()
    finally:store.db.close()
