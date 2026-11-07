"""Transaction-owned publication identity and feed assignment."""
from oko.storage.database import Database
from oko.storage.transaction import begin_write
from oko.storage.reader_status import changed as status_changed
from oko.collection.contracts import SourceError

_GRAPH_LOCK = 0x4F4B4F4752415048


def lock_graph(db: Database) -> None:
    begin_write(db)
    db.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),%s))', (_GRAPH_LOCK,))


def add(db: Database, feed: str, item: str, *, created: bool = False) -> bool:
    lock_graph(db)
    owner = db.execute('SELECT id FROM feeds WHERE id=%s FOR UPDATE', (feed,)).fetchone()
    if owner is None:
        raise ValueError('Feed not found')
    row = db.execute('SELECT feed_id,platform,external_id FROM publications WHERE id=%s FOR UPDATE', (item,)).fetchone()
    if row is None:
        raise ValueError('Publication not found')
    if row['feed_id'] == feed:
        return False
    if row['feed_id'] is not None or not created:
        raise SourceError(f"Конфликт публикации {item} ({row['platform']}, {row['external_id']}): фид {row['feed_id']}, обнаружен фидом {feed}", code='publication-feed-conflict', retryable=False)
    db.execute('UPDATE publications SET feed_id=%s WHERE id=%s', (feed,item))
    mark=getattr(db,'incomplete_group_mark',None)
    if mark is not None:mark([item])
    return True


def set_viewed(db: Database, items: list[str], viewed: bool) -> None:
    if not items:
        return
    lock_graph(db)
    changed = db.execute('UPDATE publications SET viewed=%s WHERE id=ANY(%s) AND viewed IS DISTINCT FROM %s', (int(viewed),items,int(viewed))).rowcount
    if changed:
        status_changed(db)
        mark=getattr(db,'incomplete_group_mark',None)
        if mark is not None:mark(items)
