import json
import psycopg
import pytest
from oko.storage.postgres_migrations import migrate, verify
from oko.storage.postgres_schema import MIGRATIONS

TARGET=MIGRATIONS[:88]
BASE=MIGRATIONS[:87]

def seed(db,values):
 for n,value in enumerate(values):
  db.execute('INSERT INTO feeds(id,identity,title,original_urls) VALUES (%s,%s,%s,%s)',(f'f{n}',f'i{n}',f'F{n}',value))

def snapshot(db):return db.execute('SELECT id,original_urls FROM feeds ORDER BY id').fetchall()
def type_name(db):return db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='feeds' AND column_name='original_urls'").fetchone()[0]


def test_legacy_array_values_and_ledger_are_preserved(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,BASE)
  urls=['https://example.test/a?x=1,2','тест/雪','','same','same','quote"slash\\']
  seed(db,[json.dumps(urls,ensure_ascii=False),'[]'])
  migrate(db,TARGET);verify(db,TARGET)
  assert type_name(db)=='ARRAY' and [row[1] for row in snapshot(db)]==[urls,[]]
  assert db.execute('SELECT version FROM oko_schema_migrations ORDER BY version DESC LIMIT 1').fetchone()[0]==88
  with pytest.raises(psycopg.errors.CheckViolation):db.execute("UPDATE feeds SET original_urls=ARRAY[NULL]::text[] WHERE id='f0'")

@pytest.mark.parametrize('invalid',[None,'not-json','null','{}','[1]','[null]','[[]]','[["x"]]','[{}]','["\\u0000"]','["\\ud800"]'])
def test_unrepresentable_legacy_rolls_back_ddl_and_ledger(postgres_database_url,invalid):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,BASE);seed(db,[' [ "safe" ] ',invalid]);before=snapshot(db)
  with pytest.raises(psycopg.errors.RaiseException,match='Cannot migrate feeds.original_urls'):migrate(db,TARGET)
  assert type_name(db)=='text' and snapshot(db)==before
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==87
  verify(db,BASE)


def test_new_readers_and_writers_use_migrated_array_in_store_and_http(tmp_path,postgres_database_url):
 from tests.support.storage import Store
 from oko.subscriptions.subscriptions import AddFeed
 from fastapi.testclient import TestClient
 from tests.support.app import create_app
 from tests.support.reader import HEADERS
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,BASE)
  db.execute("INSERT INTO folders(id,title,position) VALUES ('folder','Folder',1)")
  db.execute("INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport) VALUES ('old','old','Old','rss','folder',%s,'connected','rss')",(json.dumps(['https://old.test/feed','https://old.test/feed']),))
  migrate(db,TARGET)
  assert db.execute("SELECT original_urls FROM feeds WHERE id='old'").fetchone()[0]==['https://old.test/feed','https://old.test/feed']
  migrate(db,MIGRATIONS)
 store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
 try:
  assert next(f for f in store.subscriptions.feeds() if f['id']=='old')['original_urls']==['https://old.test/feed','https://old.test/feed']
  created=store.subscriptions.add_feed(AddFeed(url='https://new.test/feed',title='New',folder='Folder',verification='synthetic'))
  store.publications.configure_feed(created['id'])
  telegram=store.subscriptions.telegram.connect({'kind':'web','username':'synthetic','preview_url':'https://t.me/s/synthetic'},{'title':'Synthetic','username':'synthetic'},'https://t.me/synthetic','Folder',None,False,store.subscriptions)
  assert created['original_urls']==['https://new.test/feed']
  assert telegram['original_urls']==['https://t.me/synthetic']
 finally:store.db.close()
 app=create_app(tmp_path,token='test',api_only=True,database_url=postgres_database_url)
 with TestClient(app,headers=HEADERS) as client:
  response=client.get('/api/v3/subscriptions')
  assert response.status_code==200,response.text
  rows={f['id']:f for f in response.json()['feeds']}
  assert rows['old']['original_urls']==['https://old.test/feed','https://old.test/feed']
  assert rows[created['id']]['original_urls']==['https://new.test/feed']
  assert rows[telegram['id']]['original_urls']==['https://t.me/synthetic']
