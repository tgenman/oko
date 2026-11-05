"""Assess saved inputs for the initial-history outcome without reader projections."""
from pathlib import Path
from oko.storage.database import Connection
from oko.storage.media_paths import existing_file


def loaded(db: Connection, root: Path, identity: str) -> bool:
    rows=db.execute('''SELECT p.id,p.content,j.status AS fetch_status,j.reason_message
        FROM publications p LEFT JOIN jobs j ON j.kind='fetch' AND j.item=p.id
        WHERE p.id=%s OR p.id IN (SELECT target_publication_id FROM publication_links WHERE source_publication_id=%s)''',(identity,identity)).fetchall()
    if not rows:return False
    for row in rows:
        content=row['content']
        if 'blocks' not in content or row['fetch_status'] in ('pending','running','blocked','failed','unavailable') or row['reason_message']:
            return False
        if any(block.get('type')=='external_media' and (block.get('kind')=='web' or block.get('reason')=='public_preview_unavailable') for block in content['blocks']):
            return False
        files=db.execute('SELECT status,storage_key FROM media_files WHERE publication_id=%s',(row['id'],))
        if any(file['status']!='ready' or existing_file(root,file['storage_key']) is None for file in files):
            return False
    return True
