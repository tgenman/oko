"""Current reading composition; no persisted snapshot or lifecycle ownership."""
from oko.storage.reading import reading_snapshot
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


def telegram_title(header: dict[str, Any], text: str) -> str:
    # Structured web copies already derive the title from own text. Keep the
    # full-first-line reading compatibility for older and account copies.
    structured = 'reply_preview' in header.get('source_manifest', {}).get('post', {})
    return str(header['title']) if structured or not text else text.split('\n')[0] or str(header['title'])


def _reading_heading(material: dict[str, Any] | None, feed: dict[str, Any], header: dict[str, Any]) -> dict[str, Any] | None:
    if material and material['complete'] and feed['kind'] == 'telegram' and feed.get('transport') != 'rss':
        material['title'] = telegram_title(header, material['text'])
    return material


class ReadingComposition:
    def __init__(self, store: 'Store') -> None:
        self._store = store

    @reading_snapshot()
    def cards(self, show_hidden: bool = False, folder: str | None = None, feed: str | None = None) -> list[dict[str, Any]]:
        feeds = {owner['id']: owner for owner in self._store.subscriptions.feeds(feed, folder=folder)}
        where, parameters = ['i.viewed=0'], []
        if feed is not None:
            where.append('EXISTS (SELECT 1 FROM reading_memberships m WHERE m.publication_id=i.id AND m.feed_id=?)')
            parameters.append(feed)
        if folder is not None:
            where.append('EXISTS (SELECT 1 FROM reading_memberships m JOIN feeds owner ON owner.id=m.feed_id WHERE m.publication_id=i.id AND owner.folder_id=?)')
            parameters.append(folder)
        rows = self._store.db.execute('SELECT i.* FROM items i JOIN feeds f ON f.id=i.feed_id WHERE '
                                      + ' AND '.join(where) + ' ORDER BY i.published DESC', parameters)
        cards = []
        for row in rows:
            owners=[r[0] for r in self._store.db.execute('SELECT feed_id FROM reading_memberships WHERE publication_id=? ORDER BY feed_id',(row['id'],)) if r[0] in feeds]
            if not owners:
                continue
            owner_id=row['feed_id'] if row['feed_id'] in owners else owners[0]
            material = self._store.saved_publication(row)
            if material:
                material = _reading_heading(material, feeds[owner_id], self._store.original(row['id'])[0] if material['complete'] else {})
            if material and (show_hidden or not material.get('filter', {}).get('hidden')):
                owner = feeds[owner_id]
                text = material.pop('text')
                description = material.pop('description', '')
                material.pop('reply_preview', None)
                material.pop('linked_content', None)
                preview = description or text
                filtered = material.get('filter', {})
                if not description and filtered.get('status') == 'done' and not filtered['restored']:
                    # Preserve the existing preview of display_text followed by
                    # its final fragment pass, including joined boundaries.
                    for fragment in filtered['removed']:
                        preview = preview.replace(fragment, '')
                if filtered.get('status') == 'done' and not filtered['restored']:
                    for fragment in filtered['removed']:
                        preview = preview.replace(fragment, '')
                cards.append(dict(material, feed_id=owner_id, snippet=snippet(preview), feed_title=owner['title'], folder_id=owner['folder_id'], importance=owner['importance']))
        return cards

    @reading_snapshot()
    def navigation(self) -> Navigation:
        """Count visible original publications in their current feeds and folders."""
        folders = self._store.subscriptions.folders()
        feeds = self._store.subscriptions.feeds()
        by_feed = {feed['id']: feed for feed in feeds}
        folder_members: dict[str, set[str]] = {folder['id']: set() for folder in folders}
        feed_members: dict[str, set[str]] = {feed['id']: set() for feed in feeds}
        visible = set()
        unviewed_feeds: set[str] = set()
        for row in self._store.db.execute('SELECT i.id,m.feed_id FROM items i JOIN reading_memberships m ON m.publication_id=i.id WHERE i.viewed=0'):
            if row['feed_id'] not in by_feed:
                continue
            unviewed_feeds.add(row['feed_id'])
            if self._store.filters.read(row['id'])['hidden']:
                continue
            identity = row['id']
            visible.add(identity)
            feed_members[row['feed_id']].add(identity)
            folder_members[by_feed[row['feed_id']]['folder_id']].add(identity)
        return dict(folders=[dict(folder, count=len(folder_members[folder['id']])) for folder in folders],
                    feeds=[dict(feed, count=len(feed_members[feed['id']]), has_unviewed=feed['id'] in unviewed_feeds) for feed in feeds],
                    count=len(visible))

    @reading_snapshot()
    def folder_contents(self, folder: str) -> FolderContents:
        self._store.subscriptions.check_owner('folder', folder)
        cards = self.cards(True, folder=folder)
        rows = self._store.db.execute("""SELECT DISTINCT i.id,i.members FROM items i JOIN reading_memberships m ON m.publication_id=i.id JOIN feeds f ON f.id=m.feed_id
            WHERE i.viewed=0 AND f.folder_id=? ORDER BY i.id""", (folder,))
        # Completion includes hidden, unreadable and loading publications.
        return FolderContents(tuple((row['id'], row['members']) for row in rows),
                              tuple(sorted(card['id'] for card in cards)))

    @reading_snapshot()
    def feed_contents(self, feed: str) -> FolderContents:
        self._store.subscriptions.check_owner('feed', feed)
        rows = self._store.db.execute('SELECT i.id,i.members FROM items i JOIN reading_memberships m ON m.publication_id=i.id WHERE i.viewed=0 AND m.feed_id=? ORDER BY i.id', (feed,)).fetchall()
        return FolderContents(tuple((row['id'], row['members']) for row in rows),
                              tuple(sorted(card['id'] for card in self.cards(True, feed=feed))))

    @reading_snapshot()
    def material(self, identity: str) -> dict[str, Any] | None:
        material = self._store.material(identity)
        if material:
            owners = self._store.subscriptions.feeds(material['feed_id'])
            if owners:
                return _reading_heading(material, owners[0], self._store.original(identity)[0] if material['complete'] else {})
        return material

    def members(self, identity: str) -> list[str]:
        return [identity]
