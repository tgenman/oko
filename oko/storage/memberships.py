"""Transaction-owned publication graph and primary membership operations."""
from oko.domain.publication import PublicationOwnershipConflict
from oko.storage.database import Database
from oko.storage.transaction import begin_write


_GRAPH_LOCK = 0x4F4B4F4752415048


def lock_graph(db: Database) -> None:
    begin_write(db)
    db.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),%s))', (_GRAPH_LOCK,))


def add(db: Database, feed: str, item: str, *, newly_created: bool = False) -> bool:
    """Assign a new publication; reject conflicts instead of changing ownership."""
    lock_graph(db)
    owner = db.execute('SELECT connection FROM feeds WHERE id=%s FOR UPDATE', (feed,)).fetchone()
    if owner is None:
        raise ValueError('Feed not found')
    if owner['connection'] == 'deleting':
        raise ValueError('The feed is deleting')
    publication = db.execute('SELECT id,feed_id,platform,external_id FROM publications WHERE id=%s FOR UPDATE', (item,)).fetchone()
    if publication is None:
        raise ValueError('Publication not found')
    previous = publication['feed_id']
    if previous == feed:
        return False
    if previous is not None or not newly_created:
        raise PublicationOwnershipConflict(platform=publication['platform'], external_id=publication['external_id'],
            publication_id=item, current_feed=previous, discovered_feed=feed)
    db.execute('UPDATE publications SET feed_id=%s WHERE id=%s', (feed,item))
    mark=getattr(db,'incomplete_group_mark',None)
    if mark is not None:mark([item])
    return True


def remove(db: Database, *, items: list[str] | None = None, feed: str | None = None) -> list[str]:
    """Remove the single feed assignment while holding the graph lock."""
    if (items is None) == (feed is None):
        raise ValueError('Choose items or feed')
    lock_graph(db)
    if items is not None:
        ids = sorted(set(items))
        if not ids:return []
        rows = db.execute('UPDATE publications SET feed_id=NULL WHERE id=ANY(%s) AND feed_id IS NOT NULL RETURNING id', (ids,)).fetchall()
    else:
        rows = db.execute('UPDATE publications SET feed_id=NULL WHERE feed_id=%s RETURNING id', (feed,)).fetchall()
    ids_removed = [row['id'] for row in rows]
    mark=getattr(db,'incomplete_group_mark',None)
    if mark is not None and ids_removed:mark(ids_removed)
    return ids_removed
