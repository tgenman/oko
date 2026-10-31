import psycopg
from psycopg import sql
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS

def test_schema_function_cannot_shadow_json_guard(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:88])
  db.execute("INSERT INTO feeds(id,config) VALUES ('f',%s)",('{"v":1e3}',))
  name=db.execute('SELECT current_schema()').fetchone()[0]
  db.execute(sql.SQL("CREATE FUNCTION {}.json_typeof(json) RETURNS text LANGUAGE sql IMMUTABLE AS 'SELECT ''string''::text'").format(sql.Identifier(name)))
  try:
   import pytest
   with pytest.raises(psycopg.errors.RaiseException):migrate(db,MIGRATIONS)
   assert db.execute("SELECT config FROM feeds WHERE id='f'").fetchone()[0]=='{"v":1e3}'
  finally:
   db.execute(sql.SQL('DROP FUNCTION {}.json_typeof(json)').format(sql.Identifier(name)))
