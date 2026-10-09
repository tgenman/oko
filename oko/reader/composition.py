"""Current reading composition; no persisted snapshot or lifecycle ownership."""
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from oko.storage.store import Store


@dataclass(frozen=True)
class FolderContents:
    members: tuple[tuple[str, str], ...]
    cards: tuple[str, ...]


def represented_originals(views: list[dict[str, Any]]) -> set[str]:
    """Shared ownership rule for composition and selection of event inputs."""
    return {member for view in views for member in
            ([source['id'] for source in view['sources']] if view.get('sources') else [view['id']])}


class ReadingComposition:
    def __init__(self, store: 'Store') -> None:
        self._store = store

    def _views(self) -> list[dict[str, Any]]:
        return self._store.events.views() + self._store.digests.views()

    def cards(self, show_hidden: bool = False) -> list[dict[str, Any]]:
        feeds = {feed['id']: feed for feed in self._store.subscriptions.feeds()}
        views = self._views()
        covered = represented_originals(views)
        cards = []
        for row in self._store.db.execute('SELECT id FROM items WHERE viewed=0 ORDER BY published DESC').fetchall():
            if row['id'] in covered:
                continue
            material = self._store.material(row['id'])
            if material and (show_hidden or not material.get('filter', {}).get('hidden')):
                card = dict(material)
                card.pop('display_text', None)
                del card['text']
                cards.append(card)
        for view in views:
            card = dict(view)
            card.pop('text')
            card['sources'] = [dict(id=source['id'], url=source['url']) for source in view['sources']]
            cards.append(card)
        return [dict(card, folder_id=card.get('folder_id', feeds[card['feed_id']]['folder_id']),
                     importance=card.get('importance', feeds[card['feed_id']]['importance']))
                for card in cards if card['feed_id'] in feeds]

    def folder_contents(self, folder: str) -> FolderContents:
        self._store.subscriptions.check_owner('folder', folder)
        feeds = {feed['id']: feed['folder_id'] for feed in self._store.subscriptions.feeds()}
        cards = self.cards(True)
        owned = [card for card in cards if card['folder_id'] == folder]
        covered = represented_originals([card for card in cards if card.get('sources')])
        selected = represented_originals(owned)
        rows = self._store.db.execute('SELECT id,feed_id,members FROM items WHERE viewed=0 ORDER BY id').fetchall()
        # Frozen folder reviews keep their originals when a feed moves. Include
        # hidden/unreadable/loading originals unless another folder represents them.
        members = tuple((row['id'], row['members']) for row in rows if row['id'] in selected
                        or (feeds.get(row['feed_id']) == folder and row['id'] not in covered))
        return FolderContents(members, tuple(sorted(card['id'] for card in owned)))

    def material(self, identity: str) -> dict[str, Any] | None:
        if identity.startswith('digest-'):
            return self._store.digests.material(identity)
        if identity.startswith('event-'):
            return self._store.events.material(identity)
        return self._store.material(identity)

    def members(self, identity: str) -> list[str]:
        material = self.material(identity)
        return sorted(represented_originals([material])) if material else [identity]
