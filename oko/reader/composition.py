"""Current reading composition; no persisted snapshot or lifecycle ownership."""
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from oko.storage.store import Store


class Navigation(TypedDict):
    folders: list[dict[str, Any]]
    feeds: list[dict[str, Any]]
    count: int


@dataclass(frozen=True)
class FolderContents:
    members: tuple[tuple[str, str], ...]
    cards: tuple[str, ...]


class _Snippet(HTMLParser):
    """Plain list preview, omitting executable markup and normalizing space."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ('script', 'style'):
            self.ignored += 1
        elif tag in ('p', 'div', 'br', 'li'):
            self.parts.append(' ')

    def handle_endtag(self, tag: str) -> None:
        if tag in ('script', 'style') and self.ignored:
            self.ignored -= 1
        elif tag in ('p', 'div', 'li'):
            self.parts.append(' ')

    def handle_data(self, data: str) -> None:
        if not self.ignored:
            self.parts.append(data)


def snippet(text: str) -> str:
    parser = _Snippet()
    parser.feed(text)
    return ' '.join(''.join(parser.parts).split())[:500]


class ReadingComposition:
    def __init__(self, store: 'Store') -> None:
        self._store = store

    def cards(self, show_hidden: bool = False, folder: str | None = None, feed: str | None = None) -> list[dict[str, Any]]:
        feeds = {feed['id']: feed for feed in self._store.subscriptions.feeds()}
        cards = []
        for row in self._store.db.execute('SELECT id,feed_id FROM items WHERE viewed=0 ORDER BY published DESC').fetchall():
            owner = feeds.get(row['feed_id'])
            if owner is None or (folder is not None and owner['folder_id'] != folder) or (feed is not None and row['feed_id'] != feed):
                continue
            material = self._store.material(row['id'])
            if material and (show_hidden or not material.get('filter', {}).get('hidden')):
                card = dict(material)
                preview_text = material.get('description') or material.get('display_text', material['text'])
                filtered = material.get('filter', {})
                if filtered.get('status') == 'done' and not filtered.get('restored'):
                    for fragment in filtered['removed']:
                        preview_text = preview_text.replace(fragment, '')
                card['snippet'] = snippet(preview_text)
                card.pop('description', None)
                card.pop('display_text', None)
                del card['text']
                cards.append(card)
        return [dict(card, feed_title=feeds[card['feed_id']]['title'],
                     folder_id=feeds[card['feed_id']]['folder_id'],
                     importance=feeds[card['feed_id']]['importance']) for card in cards]

    def navigation(self) -> Navigation:
        """Count visible original publications in their current feeds and folders."""
        folders = self._store.subscriptions.folders()
        feeds = self._store.subscriptions.feeds()
        by_feed = {feed['id']: feed for feed in feeds}
        folder_members: dict[str, set[str]] = {folder['id']: set() for folder in folders}
        feed_members: dict[str, set[str]] = {feed['id']: set() for feed in feeds}
        visible = set()
        for row in self._store.db.execute('SELECT id,feed_id FROM items WHERE viewed=0'):
            if row['feed_id'] not in by_feed or self._store.filters.read(row['id'])['hidden']:
                continue
            identity = row['id']
            visible.add(identity)
            feed_members[row['feed_id']].add(identity)
            folder_members[by_feed[row['feed_id']]['folder_id']].add(identity)
        return dict(folders=[dict(folder, count=len(folder_members[folder['id']])) for folder in folders],
                    feeds=[dict(feed, count=len(feed_members[feed['id']])) for feed in feeds],
                    count=len(visible))

    def folder_contents(self, folder: str) -> FolderContents:
        self._store.subscriptions.check_owner('folder', folder)
        feeds = {feed['id']: feed['folder_id'] for feed in self._store.subscriptions.feeds()}
        cards = self.cards(True)
        owned = [card for card in cards if card['folder_id'] == folder]
        rows = self._store.db.execute('SELECT id,feed_id,members FROM items WHERE viewed=0 ORDER BY id').fetchall()
        # Folder completion also includes hidden, unreadable and loading items.
        members = tuple((row['id'], row['members']) for row in rows if feeds.get(row['feed_id']) == folder)
        return FolderContents(members, tuple(sorted(card['id'] for card in owned)))

    def material(self, identity: str) -> dict[str, Any] | None:
        return self._store.material(identity)

    def members(self, identity: str) -> list[str]:
        return [identity]
