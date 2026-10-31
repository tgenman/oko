import random
from tests.support.storage import Store
from oko.storage.incomplete_index import content_class


def test_numeric_lexemes_match_postgres_jsonb_and_integer(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        rng=random.Random(8321)
        lexemes=['0e0','-0e0','0e-3','-0.0','1e1','1.000e3','1e-1','2147483648','-2147483648']
        for _ in range(600):
            integral=str(rng.randrange(0,10**8))
            fraction='.'+str(rng.randrange(0,10**6)).zfill(rng.randint(1,6)) if rng.randrange(2) else ''
            exponent=rng.choice(['','e'+str(rng.randrange(-8,9)),'E+'+str(rng.randrange(0,9))])
            lexemes.append(('-' if rng.randrange(3)==0 else '')+integral+fraction+exponent)
        for lexeme in lexemes:
            raw='{"extra":{"_reader":{"full":'+lexeme+'}}}'
            valid=store.db.execute("SELECT pg_input_is_valid(%s,'jsonb')",(raw,)).fetchone()[0]
            if not valid:
                expected=2
            else:
                val=store.db.execute("SELECT %s::jsonb#>>'{extra,_reader,full}'",(raw,)).fetchone()[0]
                integer=store.db.execute("SELECT pg_input_is_valid(%s,'integer')",(val,)).fetchone()[0]
                expected=(0 if int(val)==0 else 1) if integer else 2
            assert content_class(raw)==expected,(lexeme,expected,content_class(raw))
    finally:store.db.close()

def test_unknown_full_not_hidden_by_other_problem(tmp_path,postgres_url):
    import json,pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':{'unexpected':'value'}}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','rss','p',0,1,%s,2)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','failure','source-network')")
            from oko.storage.incomplete_group_draft import save_keys
            save_keys(store.db,['p'])
        with pytest.raises(ValueError,match='Unrecognized publication completeness'):store.problems.snapshot(1)
    finally:store.db.close()
