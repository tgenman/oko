"""Batched reader facts over the existing SQLite/file storage adapter."""
from oko.storage.database import expect_row
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from oko.storage.store import Store
from oko.storage import material_read, publication_links

class ReaderFacts:
    def __init__(self, storage: Callable[[], Store]) -> None:
        self.storage = storage

    def item(self, identity: str) -> dict[str, Any] | None:
        row = material_read.item(self.storage().db, identity)
        return dict(row) if row else None

    def jobs(self, identity: str) -> dict[str, dict[str, Any]]:
        return {kind: dict(row) for kind, row in material_read.jobs(self.storage().db, identity).items()}

    def feed(self, identity: str) -> dict[str, Any]:
        return material_read.feed(self.storage().db, identity)

    @contextmanager
    def inputs(self, identities: set[str]) -> Iterator[None]:
        with self.storage().db.access_lock, material_read.material_inputs(self.storage().db, identities):
            yield

    def scope_exists(self, scope: str, identity: str) -> bool:
        return scope == 'all' or bool(self.storage().db.execute('SELECT 1 FROM '+('feeds' if scope=='feed' else 'folders')+' WHERE id=?', (identity,)).fetchone())

    def avatar(self, feed: str, revision: str) -> tuple[bytes, str] | None:
        row=self.storage().db.execute("SELECT a.image,a.mime FROM telegram_avatars a JOIN feeds f ON f.id=a.feed WHERE a.feed=? AND a.digest=? AND f.connection!='deleting'", (feed, revision)).fetchone()
        return (bytes(row['image']), row['mime']) if row else None

    def material_feeds(self, identities: set[str]) -> dict[str, str]:
        return {row['id']: row['feed_id'] for row in self.storage().db.execute('SELECT id,feed_id FROM items WHERE viewed=0 AND id IN (' + ','.join('?' for _ in identities) + ')', tuple(identities))} if identities else {}

    def navigation_versions(self) -> dict[str, Any]:
        store=self.storage()
        folders=store.subscriptions.folders()
        versions=store.read_versions.batch({('folder',f['id']) for f in folders})
        counts={row['folder']:row['visible'] for row in store.db.execute('SELECT * FROM reader_folder_counts')}
        feeds=[dict(id=r['id'],count=r['visible'] or 0,has_unviewed=bool(r['unviewed'])) for r in store.db.execute('SELECT f.id,c.visible,c.unviewed FROM feeds f LEFT JOIN reader_feed_counts c ON c.feed=f.id ORDER BY f.rowid')]
        return dict(folders=[dict(id=f['id'],title=f['title'],position=f['position'] or 0,count=counts.get(f['id'],0),version=str(versions['folder',f['id']]['version'])) for f in folders],feeds=feeds,count=expect_row(store.db.execute("SELECT count(*) FROM items i LEFT JOIN reader_visibility v ON v.item=i.id WHERE i.viewed=0 AND coalesce(v.hidden,0)=0 AND EXISTS (SELECT 1 FROM reading_memberships m WHERE m.publication_id=i.id)").fetchone())[0])

    def linked_preview(self, identity: str) -> dict[str, Any] | None:
        store=self.storage()
        return publication_links.reading(store,identity,store.publications.original(identity)[1])

    def was_viewed(self, identity: str) -> bool:
        row=self.storage().db.execute('SELECT viewed FROM items WHERE id=?', (identity,)).fetchone()
        return row is None or bool(row['viewed'])

    def viewed_ids(self) -> list[str]:
        return [row['id'] for row in self.storage().db.execute('SELECT id FROM items WHERE viewed=1')]

    def feed_members(self, identity: str) -> set[str]:
        return {row[0] for row in self.storage().db.execute('SELECT id FROM items WHERE feed_id=?', (identity,))}

    def deletion_feeds(self) -> set[str]:
        return {row[0] for row in self.storage().db.execute("SELECT DISTINCT feed_id FROM items WHERE id IN (SELECT item FROM jobs WHERE kind='delete' AND status!='done')")}
