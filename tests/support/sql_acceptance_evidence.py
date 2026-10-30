from oko.storage.publication_owners import owner_expr
from tests.support.storage import analyze
from tests.support.collection_acceptance import discovered
from tests.support.source import publication_packet
"""Reproduce synthetic background/scaling and PostgreSQL plan evidence."""
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import json
from fastapi.testclient import TestClient
from tests.support.reading_cost import library,traced_app,tracing
from tests.support.reader import HEADERS
from tests.support.storage import Store
from tests.support.postgres_work import scan_rows
from oko.collection.contracts import Material,Publication


def archive_evidence():
    output={}
    def run(db,sql,args=()):
     rows=db.execute(sql,args).fetchall()
     plan=db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,args or None).fetchone()[0][0]['Plan']
     return dict(rows=len(rows),rows_scanned=scan_rows(plan),buffers=plan.get('Shared Hit Blocks',0)+plan.get('Shared Read Blocks',0),plan=plan)
    with TemporaryDirectory() as d:
     s=Store(Path(d),lambda _:None);s.subscriptions.ensure_demo()
     with s.db:
      s.db.executemany('INSERT INTO publications ( id , published , viewed ) VALUES ( %s , %s , %s )',[(f'archive-{n}',n,int(n>=100)) for n in range(20000)])
      s.db.executemany("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) SELECT 'demo',id,true,viewed=0 FROM publications WHERE id=%s", [(f'archive-{n}',) for n in range(20000)])
      s.db.executemany("INSERT INTO jobs ( kind , item , status ) VALUES ( 'fetch' , %s , 'done' )",[(f'archive-{n}',) for n in range(20000)])
      s.db.executemany("INSERT INTO history_members ( feed , item ) VALUES ( 'demo' , %s )",[(f'archive-{n}',) for n in range(20000)])
      s.db.execute("INSERT INTO jobs(kind,item) VALUES ('filter','pending')")
      analyze(s.db)
     operations={
      'unviewed':(f"SELECT i.id,{owner_expr('i')} AS feed_id FROM publications i WHERE i.viewed=0 ORDER BY i.published DESC",()),
      'history_point':('SELECT * FROM history_members WHERE item=%s',('archive-100',)),
      'job_point':('SELECT * FROM jobs WHERE item=%s',('archive-100',)),
      'pending':("SELECT id FROM jobs WHERE status='pending' AND next<=%s ORDER BY next,id LIMIT 1",(0,))}
     for name,(sql,args) in operations.items():output[name]=run(s.db,sql,args)
     s.db.close()
    with TemporaryDirectory() as d:
     s=Store(Path(d),lambda _:None)
     rows=[]
     for n in range(1500):
      body=json.dumps(dict(identity=f'fixture-{n}',title='Synthetic',objects=[],action=None))
      rows.extend([(f'old-{n}',f'fixture-{n}',body,0,'failed'),(f'new-{n}',f'fixture-{n}',body,1,'failed')])
     with s.db:s.db.executemany('INSERT INTO reader_action_results ( id , identity , body , started , result ) VALUES ( %s , %s , %s , %s , %s )',rows)
     sql="SELECT * FROM reader_action_results r WHERE sequence=(SELECT max(sequence) FROM reader_action_results WHERE identity=r.identity) AND result IN ('failed','unknown','pending')"
     output['receipts_indexed']=run(s.db,sql)
     with s.db:s.db._db.execute('DROP INDEX reader_action_identity_sequence')
     output['receipts_without_index']=run(s.db,sql)
     s.db.close()
    return output


def background_evidence():
    output={'background':[],'feeds':[],'telegram':[]}
    for count in [26,100]:
     with TemporaryDirectory() as d:
      root=Path(d);feeds,items=library(root,[count]*4);s=Store(root,lambda _:None)
      def cost(call):
       queries=[];s.db.set_trace_callback(queries.append);call();s.db.set_trace_callback(None);return len(queries)
      idle=cost(s.processing.sync)
      viewed=cost(lambda:s.viewed(items[0]))

      with s.db:s.db.execute("UPDATE jobs SET status = 'pending' , next = 0 WHERE kind = 'discover' AND item = %s",(feeds[0],))
      job=s.jobs.next_job(s.clock(),feeds=[feeds[0]],include_deletions=False)
      assert job and job.kind=='discover'
      changed=cost(lambda:discovered(s,[publication_packet(Publication('new','https://example.org/new',999,teaser=Material('New','',captions='not_requested')))],'{}',job,feeds[0]))
      output['background'].append(dict(total=count*4,idle_processing_sql=idle,viewed_store_sql=viewed,new_publication_transaction_sql=changed))
      s.db.close()
    for count in [4,20]:
     with TemporaryDirectory() as d:
      root=Path(d);_,items=library(root,[5]*count);app=traced_app(root)
      with TestClient(app,headers=HEADERS) as c:
       tracing(c,app)
       old=c.get('/api/v3/publication/'+items[0]);new=c.get('/api/v3/publication/'+items[0])
       output['feeds'].append(dict(feeds=count,old_get_sql=int(old.headers['X-Test-SQL']),publication_get_sql=int(new.headers['X-Test-SQL'])))
    for count in [5,20,50]:
     with TemporaryDirectory() as d:
      s=Store(Path(d),lambda _:None)
      with s.db:
       s.db.execute("INSERT INTO folders VALUES ('folder','Synthetic',0)")
       for i in range(count):
        feed=f'{i:032x}'
        s.db.execute("INSERT INTO feeds ( id , identity , title , kind , folder_id , original_urls , connection , transport , telegram_peer , auto_read ) VALUES ( %s , %s , %s , 'telegram' , 'folder' , '[]' , 'connected' , 'telegram' , %s , 0 )",(feed,feed,feed,-1000000-i))
        s.db.execute('INSERT INTO telegram_bindings VALUES ( %s , %s )',(feed,json.dumps(dict(kind='client',role='personal',peer_id=str(-1000000-i)))))
        s.db.execute('INSERT INTO telegram_read_state ( feed , since ) VALUES ( %s , 0 )',(feed,))
      q=[];s.db.set_trace_callback(q.append);s.jobs.sync_reads();s.db.set_trace_callback(None)
      output['telegram'].append(dict(feeds=count,sql=len(q)));s.db.close()
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    for name,measure in [('archives',archive_evidence),('background',background_evidence)]:
        data=measure()
        target=args.output/(name+'.json')
        target.write_text(json.dumps(data,indent=2)+'\n')
        print(str(target))
