import psycopg
from psycopg import sql
import pytest
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS

def test_payload_guard_not_overridden_by_schema_function(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:89]);schema=db.execute('SELECT current_schema()').fetchone()[0]
  db.execute("INSERT INTO jobs(kind,item,payload) VALUES ('delete','p',%s)",('{"n":1e3}',))
  db.execute(sql.SQL("CREATE FUNCTION {}.json_typeof(json) RETURNS text LANGUAGE sql IMMUTABLE AS 'SELECT ''string''::text'").format(sql.Identifier(schema)))
  try:
   with pytest.raises(psycopg.errors.RaiseException):migrate(db,MIGRATIONS)
   assert db.execute("SELECT payload FROM jobs WHERE item='p'").fetchone()[0]=='{"n":1e3}'
  finally:db.execute(sql.SQL('DROP FUNCTION {}.json_typeof(json)').format(sql.Identifier(schema)))
