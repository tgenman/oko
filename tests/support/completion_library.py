from tests.support.worker_library import worker_library

def library(root, database_url, count):
    store, original, _, _ = worker_library(root, database_url=database_url)
    store.set_work_enabled(False)
    store.processing.background = True
    with store.db:
        template = dict(store.db.execute('SELECT * FROM publications WHERE id = %s', (original,)).fetchone())
        fields = list(template)
        for number in range(1, count):
            identity = f'{number:032x}'
            row = dict(template, id=identity, source_id='completion:'+identity, external_id='completion:'+identity)
            marks = ['%s::integer::boolean' if field == 'source_deleted' else '%s' for field in fields]
            store.db.execute('INSERT INTO publications('+','.join(fields)+') VALUES ('+','.join(marks)+')', tuple(row[field] for field in fields))
            store.db.execute('INSERT INTO feed_publications (feed_id,publication_id,is_primary) VALUES ( %s , %s , true )', ('demo', identity))
    store.db.close()

