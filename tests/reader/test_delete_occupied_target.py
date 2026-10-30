from tests.support.storage import Store


def test_owner_change_cannot_overwrite_unrelated_occupied_target(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        original='job:delete:feed:fetch:item:delete:part'
        target='job:delete:newfeed:discover:x:fetch:item:delete:part'
        old=dict(id=original,code='delete',section='object',title='old',consequence='c',next_step='n',objects=[],action=None,operation='producer:status',state='current',next_at=None)
        foreign=dict(old,title='fetch problem',operation='producer:fetch',objects=[dict(kind='material',id='item:delete:part')])
        with store.db:store.problems.sync_candidate(original,old,1)
        store.problems.reconcile([foreign],2,feeds={})
        fetch=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'status_job_identity'=%s AND finished IS NULL",(original,)).fetchone()
        with store.db:
            store.problems.sync_candidate(target,dict(old,id=target,title='unrelated',operation='producer:discover'),2)
        other=store.db.execute('SELECT id,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(target,)).fetchone()
        discover=dict(old,id=target,title='unrelated',operation='producer:discover')
        store.problems.reconcile([discover,dict(foreign,id=target,_legacy_ids={original})],3,feeds={})
        assert store.db.execute('SELECT id,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(target,)).fetchone()==other
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE id=%s AND finished IS NULL',(fetch['id'],)).fetchone()
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        store.problems.reconcile([discover,dict(foreign,id=target,_legacy_ids={original})],4,feeds={})
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(target,)).fetchone()[0]==other['id']
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE id=%s AND finished IS NULL',(fetch['id'],)).fetchone()
    finally:store.db.close()


def test_real_jobs_with_same_key_preserve_both_producers(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('discover','x:fetch:item:delete:part','newfeed','failed','failure','delete'),('fetch','item:delete:part','newfeed:discover:x','failed','failure','delete')")
        problems=store.problems.candidates(2)
        assert {p.get('operation') for p in problems if p['code']=='delete'}=={'producer:discover','producer:fetch'}
        first=store.problems.snapshot(2)['current']
        ids={p['id'] for p in first if p['code']=='delete'}
        assert len(ids)==2
        assert {p['id'] for p in store.problems.snapshot(3)['current'] if p['code']=='delete'}==ids
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert {p['id'] for p in store.problems.snapshot(4)['current'] if p['code']=='delete'}==ids
        with store.db:
            store.db.execute("UPDATE jobs SET status='done',error=NULL,problem_code=NULL WHERE kind='fetch' AND item='item:delete:part'")
        remaining={p['id'] for p in store.problems.snapshot(5)['current'] if p['code']=='delete'}
        assert len(remaining)==1 and remaining.issubset(ids)
    finally:store.db.close()


def test_occupied_new_owner_preserves_existing_foreign_legacy_id(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        original='job:delete:oldfeed:fetch:item'
        target='job:delete:newfeed:fetch:item'
        base=dict(code='delete',section='object',title='x',consequence='c',next_step='n',objects=[dict(kind='material',id='item')],action=None,state='current',next_at=None)
        with store.db:
            store.problems.sync_candidate(original,dict(base,id=original,operation='producer:fetch'),1)
            store.problems.sync_candidate(target,dict(base,id=target,operation='producer:discover'),1)
        before=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(original,)).fetchone()['id']
        store.problems.reconcile([dict(base,id=target,operation='producer:discover'),dict(base,id=target,operation='producer:fetch',_legacy_ids={original})],2,feeds={})
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE id=%s AND finished IS NULL',(before,)).fetchone()
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        store.problems.reconcile([dict(base,id=target,operation='producer:discover'),dict(base,id=target,operation='producer:fetch',_legacy_ids={original})],3,feeds={})
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE id=%s AND finished IS NULL',(before,)).fetchone()
    finally:store.db.close()


def test_one_isolated_alias_does_not_merge_two_split_problems(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        original='job:delete:old:fetch:item:delete:part'
        target1='job:delete:new1:fetch:item:delete:part'
        target2='job:delete:new2:fetch:item:delete:part'
        base=dict(code='delete',section='object',title='x',consequence='c',next_step='n',objects=[],action=None,state='current',next_at=None)
        legacy=dict(base,id=original,operation='producer:status')
        foreign=dict(base,id=original,operation='producer:fetch',objects=[dict(kind='material',id='item:delete:part')])
        with store.db:store.problems.sync_candidate(original,legacy,1)
        store.problems.reconcile([foreign],2,feeds={})
        with store.db:
            for target in (target1,target2):store.problems.sync_candidate(target,dict(base,id=target,operation='producer:discover'),2)
        candidates=[dict(base,id=target,operation='producer:discover') for target in (target1,target2)]+[dict(foreign,id=target,_legacy_ids={original}) for target in (target1,target2)]
        store.problems.reconcile(candidates,3,feeds={})
        rows=store.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'operation'='producer:fetch' AND finished IS NULL").fetchall()
        assert len(rows)==2
    finally:store.db.close()


def test_same_kind_jobs_with_same_key_survive_restart(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','b:fetch:c','a','failed','failure','delete'),('fetch','c','a:fetch:b','failed','failure','delete')")
        first=store.problems.snapshot(2)['current']
        ids={p['id'] for p in first if p['code']=='delete'}
        assert len(ids)==2
        assert {p['id'] for p in store.problems.snapshot(3)['current'] if p['code']=='delete'}==ids
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert {p['id'] for p in store.problems.snapshot(4)['current'] if p['code']=='delete'}==ids
        with store.db:
            store.db.execute("UPDATE jobs SET status='done',error=NULL,problem_code=NULL WHERE kind='fetch' AND item='b:fetch:c'")
        remaining={p['id'] for p in store.problems.snapshot(5)['current'] if p['code']=='delete'}
        assert len(remaining)==1 and remaining.issubset(ids)
    finally:store.db.close()


def test_same_kind_old_isolated_record_without_target_keeps_existing_ids(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','b:fetch:c','a','failed','failure','delete'),('fetch','c','a:fetch:b','failed','failure','delete')")
        ids={p['id'] for p in store.problems.snapshot(2)['current'] if p['code']=='delete'}
        with store.db:
            row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity LIKE 'job:status-isolated:%' AND finished IS NULL").fetchone()
            body=json.loads(row['body']);body.pop('status_job_target')
            store.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(json.dumps(body),row['id']))
        assert {p['id'] for p in store.problems.snapshot(3)['current'] if p['code']=='delete'}==ids
    finally:store.db.close()


def test_existing_unrelated_job_problem_is_not_rewritten_on_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','item','feed','failed','failure','custom')")
            candidate=next(p for p in store.problems.candidates(1) if p['code']=='custom')
            candidate.pop('_status_job_target',None)
            store.problems.sync_candidate(candidate['id'],candidate,1)
        before=store.db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(candidate['id'],)).fetchone()
        store.problems.snapshot(2)
        assert store.db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(candidate['id'],)).fetchone()==before
    finally:store.db.close()
