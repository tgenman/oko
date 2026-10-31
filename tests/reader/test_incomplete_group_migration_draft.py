import json
import psycopg
import pytest
from oko.storage.postgres_migrations import Migration,migrate,verify
from oko.storage.postgres_schema import MIGRATIONS


def test_group_migration_rolls_back_and_preserves_legacy(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:78])
        raw=json.dumps({'extra':{'_reader':{'full':False}},'payload':'\x00'})
        db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','rss','p',0,1,%s,0)",(raw,))
        body=json.dumps({'code':'material-incomplete','objects':[None],'operation':'producer:status'})
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','incomplete:f:p','background',%s,1)",(body,))
        migration=MIGRATIONS[78]
        failing=Migration(79,migration.name,migration.sql+'; SELECT missing FROM publications')
        with pytest.raises(psycopg.errors.UndefinedColumn):migrate(db,(*MIGRATIONS[:78],failing))
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==78
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==raw
        assert db.execute("SELECT body FROM reader_problems WHERE id='legacy'").fetchone()[0]==body
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='publications' AND column_name='incomplete_group_key'").fetchone()[0]==0
        migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==raw
        assert db.execute("SELECT body FROM reader_problems WHERE id='legacy'").fetchone()[0]==body
