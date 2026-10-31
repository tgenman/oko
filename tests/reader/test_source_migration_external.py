import json
import psycopg
import pytest
from oko.storage.postgres_migrations import Migration,migrate,verify
from oko.storage.postgres_schema import MIGRATIONS


def test_source_migration_preserves_long_legacy_data_and_rolls_back(postgres_database_url):
    body=json.dumps({'source_group_identity':'x'*10000,'code':'legacy','objects':[]})
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:77])
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:old','background',%s,1)",(body,))
        import hashlib
        large=''.join(hashlib.sha256(str(n).encode()).hexdigest() for n in range(60))
        db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','p',%s,'failed','failure',%s)",(large,'source-'+large))
        migration=MIGRATIONS[77]
        failing=Migration(78,migration.name,migration.sql+'; SELECT missing_column FROM jobs')
        with pytest.raises(psycopg.errors.UndefinedColumn):migrate(db,(*MIGRATIONS[:77],failing))
        assert db.execute("SELECT body FROM reader_problems WHERE id='old'").fetchone()[0]==body
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='jobs' AND column_name='source_event_owned'").fetchone()[0]==0
        migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
        assert db.execute("SELECT feed_id,problem_code FROM jobs WHERE item='p'").fetchone()==(large,'source-'+large)
        assert db.execute("SELECT body FROM reader_problems WHERE id='old'").fetchone()[0]==body
