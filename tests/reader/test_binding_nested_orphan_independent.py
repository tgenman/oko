import psycopg
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
from oko.storage.postgres_connection import PostgresConnection
from oko.storage.material_read import feed

def test_deep_valid_binding_on_nontelegram_feed_does_not_break_read(postgres_database_url):
 raw='['*1100+'0'+']'*1100
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:91])
  db.execute("INSERT INTO feeds(id,identity,title,kind,original_urls) VALUES ('f','f','F','rss',ARRAY[]::text[])")
  db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('f',%s)",(raw,))
  old=db.execute("SELECT kind,binding FROM feeds LEFT JOIN telegram_bindings ON feed=id WHERE id='f'").fetchone()
  assert old[0]=='rss' and old[1]==raw
  migrate(db,MIGRATIONS)
 connection=PostgresConnection(postgres_database_url)
 try:
  item,binding=feed(connection,'f')
  assert item['id']=='f' and binding is None
 finally:connection.close()


def test_writer_keeps_deep_binding_raw_even_with_high_recursion_limit(tmp_path,postgres_url):
 import sys
 from tests.support.storage import Store
 from oko.storage.transaction import transaction
 s=Store(tmp_path,lambda _:None,database_url=postgres_url)
 previous=sys.getrecursionlimit()
 try:
  value=0
  for _ in range(1100):value=[value]
  sys.setrecursionlimit(4000)
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,kind,original_urls) VALUES ('f','f','F','rss',ARRAY[]::text[])")
   s.subscriptions.telegram.put_binding('f',{'value':value})
  row=s.db.execute("SELECT binding IS NULL AS raw,binding_raw IS NOT NULL AS retained FROM telegram_bindings WHERE feed='f'").fetchone()
  assert row['raw'] and row['retained']
  sys.setrecursionlimit(previous)
  assert feed(s.db,'f')[1] is None
 finally:
  sys.setrecursionlimit(previous)
  s.db.close()
