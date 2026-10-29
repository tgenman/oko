"""Batched reader facts over the existing PostgreSQL/file storage."""
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

    def feed(self, identity: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return material_read.feed(self.storage().db, identity)

    @contextmanager
    def inputs(self, identities: set[str], *, include_subtitles: bool = True) -> Iterator[None]:
        with self.storage().db.access_lock, material_read.material_inputs(self.storage().db, identities, include_subtitles=include_subtitles):
            yield

    def card_validators(self, identities: list[str]) -> dict[str, tuple[str, int | None]]:
        if not identities:
            return {}
        rows=self.storage().db.execute("SELECT i.id, i.xmin::text AS row_generation, e.version FROM items i LEFT JOIN reader_entities e ON e.kind='card' AND e.identity=i.id WHERE i.id = ANY(%s)", (identities,))
        return {row['id']:(row['row_generation'],row['version']) for row in rows}

    def feed_resources(self, identities: set[str] | None = None) -> dict[str, tuple[dict[str, Any], dict[str, Any] | None]]:
        subscriptions = self.storage().subscriptions
        rows = subscriptions.feeds() if identities is None else subscriptions.feeds_many(identities)
        bindings = {}
        for start in range(0, len(rows), 400):
            bindings.update(subscriptions.telegram.bindings([row['id'] for row in rows[start:start+400]]))
        return {row['id']: (row, bindings.get(row['id'])) for row in rows}

    def avatar(self, feed: str, revision: str) -> tuple[bytes, str] | None:
        row=self.storage().db.execute("SELECT a . image , a . mime FROM telegram_avatars a JOIN feeds f ON f . id = a . feed WHERE a . feed = %s AND a . digest = %s AND f . connection != 'deleting'", (feed, revision)).fetchone()
        return (bytes(row['image']), row['mime']) if row else None

    def material_feeds(self, identities: set[str]) -> dict[str, str]:
        return {row['id']: row['feed_id'] for row in self.storage().db.execute(f"SELECT id , feed_id FROM items_read WHERE viewed = 0 AND id IN ( {','.join(('%s' for _ in identities))} )", tuple(identities))} if identities else {}

    def linked_preview(self, identity: str) -> dict[str, Any] | None:
        store=self.storage()
        return publication_links.reading(store,identity,store.publications.original(identity)[1])

    def was_viewed(self, identity: str) -> bool:
        row=self.storage().db.execute('SELECT viewed FROM items_read WHERE id = %s', (identity,)).fetchone()
        return row is None or bool(row['viewed'])

    def viewed_ids(self) -> list[str]:
        return [row['id'] for row in self.storage().db.execute('SELECT id FROM items_read WHERE viewed = 1')]

    def feed_members(self, identity: str) -> set[str]:
        return {row[0] for row in self.storage().db.execute('SELECT id FROM items_by_feed WHERE feed_id = %s', (identity,))}

    def deletion_feeds(self) -> set[str]:
        return {row[0] for row in self.storage().db.execute("SELECT DISTINCT feed_id FROM items_read WHERE id IN ( SELECT item FROM jobs WHERE kind = 'delete' AND status != 'done' )")}
