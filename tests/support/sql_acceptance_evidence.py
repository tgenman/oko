"""Reproduce synthetic background/scaling and archived SQLite plan evidence."""
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import json
from fastapi.testclient import TestClient
from tests.support.reading_cost import library,traced_app,tracing
from tests.support.reader import HEADERS
from oko.storage.store import Store
from oko.collection.contracts import Material,Publication


def archive_evidence():
    output={}
    def run(db,sql,args=()):
     step=100 if 'reader_action_results r ' in sql else 1
     ticks=[];db.set_progress_handler(lambda:ticks.append(1) or 0,step)
     rows=db.execute(sql,args).fetchall();db.set_progress_handler(None,0)
     return dict(rows=len(rows),vm=len(ticks)*step,vm_precision=step,plan=[r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql+' -- evidence '+str(len(output)),args)])
    with TemporaryDirectory() as d:
     s=Store(Path(d),lambda _:None);s.subscriptions.ensure_demo()
     with s.db:
      s.db.executemany('INSERT INTO items(id,feed_id,published,viewed) VALUES (?,?,?,?)',[(f'archive-{n}','demo',n,int(n>=100)) for n in range(20000)])
      s.db.executemany("INSERT INTO jobs(kind,item,status) VALUES ('fetch',?,'done')",[(f'archive-{n}',) for n in range(20000)])
      s.db.executemany("INSERT INTO history_members(feed,item) VALUES ('demo',?)",[(f'archive-{n}',) for n in range(20000)])
      s.db.execute("INSERT INTO jobs(kind,item) VALUES ('filter','pending')")
      s.db.execute('ANALYZE')
     operations={
      'unviewed':('SELECT id,feed_id FROM items WHERE viewed=0 ORDER BY published DESC',()),
      'history_point':('SELECT * FROM history_members WHERE item=?',('archive-100',)),
      'job_point':('SELECT * FROM jobs WHERE item=?',('archive-100',)),
      'pending':("SELECT id FROM jobs WHERE status='pending' AND next<=? ORDER BY next,id LIMIT 1",(0,))}
     for name,(sql,args) in operations.items():output[name]=run(s.db,sql,args)
     s.db.close()
    with TemporaryDirectory() as d:
     s=Store(Path(d),lambda _:None)
     rows=[]
     for n in range(1500):
      body=json.dumps(dict(identity=f'fixture-{n}',title='Synthetic',objects=[],action=None))
      rows.extend([(f'old-{n}',f'fixture-{n}',body,0,'failed'),(f'new-{n}',f'fixture-{n}',body,1,'failed')])
     with s.db:s.db.executemany('INSERT INTO reader_action_results(id,identity,body,started,result) VALUES (?,?,?,?,?)',rows)
     sql="SELECT * FROM reader_action_results r WHERE sequence=(SELECT max(sequence) FROM reader_action_results WHERE identity=r.identity) AND result IN ('failed','unknown','pending')"
     output['receipts_indexed']=run(s.db,sql)
     with s.db:s.db.execute('DROP INDEX reader_action_identity_sequence')
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
      
      with s.db:s.db.execute("UPDATE jobs SET status='pending',next=0 WHERE kind='discover' AND item=?",(feeds[0],))
      job=s.next_job(s.clock(),feeds=[feeds[0]],include_deletions=False)
      assert job and job.kind=='discover'
      changed=cost(lambda:s.discovered([Publication('new','https://example.org/new',999,teaser=Material('New','',captions='not_requested'))],'{}',job,feeds[0]))
      output['background'].append(dict(total=count*4,idle_processing_sql=idle,viewed_store_sql=viewed,new_publication_transaction_sql=changed))
      s.db.close()
    for count in [4,20]:
     with TemporaryDirectory() as d:
      root=Path(d);_,items=library(root,[5]*count);app=traced_app(root)
      with TestClient(app,headers=HEADERS) as c:
       tracing(c,app)
       old=c.get('/api/v3/materials/'+items[0]);new=c.get('/api/v3/reading/materials/'+items[0])
       output['feeds'].append(dict(feeds=count,old_get_sql=int(old.headers['X-Test-SQL']),versioned_get_sql=int(new.headers['X-Test-SQL'])))
    for count in [5,20,50]:
     with TemporaryDirectory() as d:
      s=Store(Path(d),lambda _:None)
      with s.db:
       s.db.execute("INSERT INTO folders VALUES ('folder','Synthetic',0)")
       for i in range(count):
        feed=f'{i:032x}'
        s.db.execute("INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport,telegram_peer,auto_read) VALUES (?,?,?,'telegram','folder','[]','connected','telegram',?,0)",(feed,feed,feed,-1000000-i))
        s.db.execute('INSERT INTO telegram_bindings VALUES (?,?)',(feed,json.dumps(dict(kind='client',role='personal',peer_id=str(-1000000-i)))))
        s.db.execute('INSERT INTO telegram_read_state(feed,since) VALUES (?,0)',(feed,))
      q=[];s.db.set_trace_callback(q.append);s.read_sync.sync();s.db.set_trace_callback(None)
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
