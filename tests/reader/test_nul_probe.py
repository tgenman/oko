import psycopg
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS

def test_nul_surrogate_roundtrip(tmp_path,postgres_database_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('','action:damaged','action','{}',1)")
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        details=client.get('/api/v3/status/details'); summary=client.get('/api/v3/status')
        assert details.status_code==summary.status_code==200
        problem=details.json()['problems']['current'][0]
        assert '\0' in problem['id'] and problem['recovery']==[] and problem['affected']==[]
        assert problem['id'] in {p['id'] for p in summary.json()['problems']}
        assert client.post('/api/v3/problems/'+problem['id'].replace('\0','%00')+'/abandon').status_code in (400,404,409,422)
