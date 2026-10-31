"""Transaction-owned publication graph and primary membership operations."""
from oko.storage.database import Database
from oko.storage.transaction import begin_write
from oko.storage.reader_status import changed as status_changed


_GRAPH_LOCK = 0x4F4B4F4752415048


def lock_graph(db: Database) -> None:
    begin_write(db)
    db.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),%s))', (_GRAPH_LOCK,))


def add(db: Database, feed: str, item: str) -> bool:
    """Add a membership while holding the graph lock."""
    lock_graph(db)
    owner = db.execute('SELECT connection FROM feeds WHERE id=%s FOR UPDATE', (feed,)).fetchone()
    if owner is None:
        raise ValueError('Feed not found')
    if owner['connection']=='deleting':
        raise ValueError('The feed is deleting')
    if db.execute('SELECT id FROM publications WHERE id=%s FOR UPDATE', (item,)).fetchone() is None:
        raise ValueError('Publication not found')
    row = db.execute('''INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed)
        SELECT %s,i.id,NOT EXISTS (SELECT 1 FROM feed_publications m WHERE m.publication_id=i.id AND m.is_primary),i.viewed=0
        FROM publications i WHERE i.id=%s ON CONFLICT(feed_id,publication_id) DO NOTHING RETURNING publication_id''', (feed,item)).fetchone()
    if row is not None:
        mark=getattr(db,'incomplete_group_mark',None)
        if mark is not None:mark([item])
    return row is not None


def remove(db: Database, *, items: list[str] | None = None, feed: str | None = None) -> list[str]:
    """Delete memberships and deterministically replace a removed primary."""
    if (items is None) == (feed is None):
        raise ValueError('Choose items or feed')
    lock_graph(db)
    ids = sorted(set(items)) if items is not None else [r[0] for r in db.execute(
        'SELECT publication_id FROM feed_publications WHERE feed_id=%s ORDER BY publication_id', (feed,)).fetchall()]
    if not ids:
        return []
    db.execute('SELECT id FROM publications WHERE id=ANY(%s) ORDER BY id FOR UPDATE', (ids,)).fetchall()
    if feed is None:
        removed = db.execute('DELETE FROM feed_publications WHERE publication_id=ANY(%s) RETURNING publication_id,is_primary', (ids,)).fetchall()
    else:
        removed = db.execute('DELETE FROM feed_publications WHERE feed_id=%s AND publication_id=ANY(%s) RETURNING publication_id,is_primary', (feed,ids)).fetchall()
    primary = sorted({r['publication_id'] for r in removed if r['is_primary']})
    if primary:
        db.execute('''UPDATE feed_publications m SET is_primary=true WHERE (m.feed_id,m.publication_id) IN (
            SELECT min(feed_id),publication_id FROM feed_publications WHERE publication_id=ANY(%s)
            GROUP BY publication_id HAVING NOT bool_or(is_primary))''', (primary,))
    ids_removed=[r['publication_id'] for r in removed]
    mark=getattr(db,'incomplete_group_mark',None)
    if mark is not None and ids_removed:mark(ids_removed)
    return ids_removed


def set_viewed(db: Database, items: list[str], viewed: bool) -> None:
    """Update publication and transitional membership state in one transaction."""
    if not items:
        return
    lock_graph(db)
    changed = db.execute('UPDATE publications SET viewed=%s,complete=0 WHERE id=ANY(%s) AND (viewed IS DISTINCT FROM %s OR complete IS DISTINCT FROM 0)', (int(viewed),items,int(viewed))).rowcount
    if changed:
        status_changed(db)
        mark=getattr(db,'incomplete_group_mark',None)
        if mark is not None:mark(items)
    db.execute('UPDATE feed_publications SET is_unviewed=%s::integer::boolean WHERE publication_id=ANY(%s) AND is_unviewed IS DISTINCT FROM %s::integer::boolean',
               (not viewed,items,not viewed))
