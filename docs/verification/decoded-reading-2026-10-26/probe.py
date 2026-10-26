"""Synthetic PostgreSQL full-list timing; never reads production configuration."""
import argparse
import contextvars
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

parser=argparse.ArgumentParser()
parser.add_argument('--repo',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--count',type=int,default=1600)
args=parser.parse_args()
sys.path.insert(0,str(args.repo.resolve()))
import psycopg
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.postgres import isolated_database
from tests.support.reader import HEADERS,Clock
from tests.support.worker_library import worker_library
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS

active=contextvars.ContextVar('decode_measure',default=None)
loads=json.loads
execute=psycopg.Connection.execute

def measured_loads(value,*a,**kw):
 target=active.get()
 if target is not None and isinstance(value,str) and 'decode-probe-' in value:
  target['content_decodes']+=1
 return loads(value,*a,**kw)

def measured_execute(connection,*a,**kw):
 target=active.get()
 if target is not None:target['sql']+=1
 return execute(connection,*a,**kw)

json.loads=measured_loads
psycopg.Connection.execute=measured_execute
with isolated_database(os.environ['OKO_TEST_POSTGRES_URL']) as dsn,tempfile.TemporaryDirectory(prefix='oko-decoded-probe-') as folder:
 root=Path(folder)
 with psycopg.connect(dsn,autocommit=True) as db:migrate(db,MIGRATIONS)
 store,first,_,_=worker_library(root,text='decode-probe-initial',database_url=dsn)
 store.set_work_enabled(False)
 store.processing.background=True
 def query(sql):
  return sql if hasattr(store.db, '_queries') else sql.replace('?', '%s')
 def execute_seed(sql, parameters=()):
  return store.db.execute(query(sql), parameters)
 def execute_many_seed(sql, parameters):
  return store.db.executemany(query(sql), parameters)
 with store.db:
  execute_seed("INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport) VALUES ('small','small','Small','rss','demo','[]','connected','rss')")
  template=dict(execute_seed('SELECT * FROM items WHERE id=?',(first,)).fetchone())
  fields=list(template)
  rows=[]
  identities=[first]+[f'{n:032x}' for n in range(1,args.count)]
  for n,identity in enumerate(identities):
   content=loads(template['content'])
   content['blocks'][0]['text']=f'decode-probe-{n}\n'+('Long synthetic article with useful text. '*1200)
   row=dict(template,id=identity,source_id=identity,external_id=identity,feed_id='small' if n<2 else 'demo',content=json.dumps(content))
   if n==0:
    execute_seed('UPDATE items SET content=?,feed_id=? WHERE id=?',(row['content'],row['feed_id'],first))
    execute_seed('DELETE FROM feed_publications WHERE publication_id=?',(first,))
   else:rows.append([row[f] for f in fields])
  execute_many_seed('INSERT INTO items('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',rows)
  execute_many_seed('INSERT INTO feed_publications VALUES (?,?)',[('small' if n<2 else 'demo',i) for n,i in enumerate(identities)])
  for n in range(max(1,args.count//16)):
   identity=f'{n+100000:032x}'
   content=loads(template['content']);content['blocks'][0]['text']=f'decode-probe-linked-{n}\n'+('Shared linked article. '*1600)
   row=dict(template,id=identity,source_id=identity,external_id=identity,feed_id=None,platform='other_web',content=json.dumps(content))
   execute_seed('INSERT INTO items('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',[row[f] for f in fields])
   execute_many_seed('INSERT INTO publication_links VALUES (?,?)',[(i,identity) for i in identities[n*16:(n+1)*16]])
 store.db.close()
 clock=Clock();app=create_app(root,token='test',api_only=True,database_url=dsn,**clock.options())
 measurements=[]
 @app.middleware('http')
 async def measure(request,call_next):
  counter=dict(content_decodes=0,sql=0);token=active.set(counter)
  try:
   response=await call_next(request);measurements.append(counter);return response
  finally:active.reset(token)
 def body(target):return dict(storage_id=None,resources=[dict(kind='status',known_version=None),dict(kind='navigation',known_version=None),dict(kind='list',known_version=None,known_cards={},options=dict(target=target,sort='newest',show_hidden=False))])
 result=dict(materials=args.count,body_characters=48000,linked_shared_by=16,samples=[])
 with TestClient(app,headers=HEADERS) as client:
  deadline=time.monotonic()+180
  while app.state.host.execution._store is None or app.state.host.execution._store.processing.recovering:
   if time.monotonic()>deadline:raise RuntimeError('Recovery timeout')
   time.sleep(.02)
  def request(name,method,url,**kwargs):
   measurements.clear();start=time.perf_counter();r=client.request(method,url,**kwargs);ms=(time.perf_counter()-start)*1000
   assert r.status_code==200,(r.status_code,r.text[:100])
   data=r.json();cards=[v['data'] for v in data.get('resources',[]) if v['kind']=='card' and v['state']=='changed']
   normalized=loads(json.dumps(cards).replace(first,'FIRST'))
   canonical=json.dumps(sorted(normalized,key=lambda c:c['id']),sort_keys=True)
   result['samples'].append(dict(name=name,ms=round(ms,3),bytes=len(r.content),**measurements[-1],cards_digest=hashlib.sha256(canonical.encode()).hexdigest(),card_count=len(cards)))
   print(name,round(ms),measurements[-1],flush=True)
   return data
  for n in range(5):
   data=request('initial','POST','/api/v3/reading/check',json=body({'scope':'all'}))
   request('small_feed','POST','/api/v3/reading/check',json=body({'scope':'feed','feed_id':'small'}))
   request('opened','GET','/api/v3/reading/materials/'+first)
  listing=next(v for v in data['resources'] if v['kind']=='list')
  request('unchanged','POST','/api/v3/reading/check',json=dict(storage_id=data['storage_id'],resources=[dict(kind='list',options=listing['data']['options'],known_version=listing['version'],known_cards={v['id']:v['version'] for v in data['resources'] if v['kind']=='card'})]))
 result['medians']={name:statistics.median(s['ms'] for s in result['samples'] if s['name']==name) for name in ('initial','small_feed','opened')}
 args.output.write_text(json.dumps(result,indent=2)+'\n')
