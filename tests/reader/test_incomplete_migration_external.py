import json
import psycopg
import pytest
from oko.storage.postgres_migrations import Migration,migrate,verify
from oko.storage.postgres_schema import MIGRATIONS

def test_migration_075_preserves_python_valid_jsonb_invalid_and_rolls_back(postgres_database_url):
    raw=json.dumps({'extra':{'_reader':{'full':False}},'payload':'\x00'})
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:74])
        db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,source_deleted,content) VALUES ('p','rss','p',0,1,false,%s)",(raw,))
        migration=MIGRATIONS[74]
        failing=Migration(75,migration.name,migration.sql+'; SELECT missing_column FROM publications')
        with pytest.raises(psycopg.errors.UndefinedColumn):migrate(db,(*MIGRATIONS[:74],failing))
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==raw
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='publications' AND column_name='incomplete_class'").fetchone()[0]==0
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==74
        migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
        assert db.execute("SELECT content,incomplete_class FROM publications WHERE id='p'").fetchone()==(raw,None)
