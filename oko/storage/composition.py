"""Current reading composition; no persisted snapshot or lifecycle ownership."""
from oko.storage.database import expect_row
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
        where, parameters = ['i.viewed=0'], []
        if feed is not None:
            where.append('EXISTS ( SELECT 1 FROM reading_memberships m WHERE m . publication_id = i . id AND m . feed_id = %s )')
            parameters.append(feed)
        if folder is not None:
            where.append('EXISTS ( SELECT 1 FROM reading_memberships m JOIN feeds owner ON owner . id = m . feed_id WHERE m . publication_id = i . id AND owner . folder_id = %s )')
            parameters.append(folder)
        rows = self._store.db.execute(f"SELECT i . * FROM items i JOIN feeds f ON f . id = i . feed_id WHERE {' AND '.join(where)} ORDER BY i . published DESC", parameters).fetchall()
        feeds = {owner['id']: owner for owner in self._store.subscriptions.feeds_many({row['feed_id'] for row in rows})}
        cards = []
        from oko.storage.material_read import material_inputs
        with material_inputs(self._store.db, {row['id'] for row in rows}):
            for row in rows:
                owner_id=row['feed_id']
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
                    if not description and filtered.get('status') == 'done':
                        # Preserve the existing preview of display_text followed by
                        # its final fragment pass, including joined boundaries.
                        for fragment in filtered['removed']:
                            preview = preview.replace(fragment, '')
                    if filtered.get('status') == 'done':
                        for fragment in filtered['removed']:
                            preview = preview.replace(fragment, '')
                    cards.append(dict(material, feed_id=owner_id, snippet=snippet(preview), feed_title=owner['title'], folder_id=owner['folder_id'], importance=owner['importance']))
        return cards

    @reading_snapshot()
    def navigation(self) -> Navigation:
        """Count visible original publications in their current feeds and folders."""
        folders = self._store.subscriptions.folders()
        feeds = self._store.subscriptions.feeds()
        folder_counts={row['folder']:row['visible'] for row in self._store.db.execute('SELECT folder , visible FROM reader_folder_counts')}
        feed_counts={row['feed']:row for row in self._store.db.execute('SELECT feed , visible , unviewed FROM reader_feed_counts')}
        visible=expect_row(self._store.db.execute('SELECT count ( * ) FROM items i LEFT JOIN reader_visibility v ON v . item = i . id WHERE i . viewed = 0 AND coalesce ( v . hidden , 0 ) = 0 AND EXISTS ( SELECT 1 FROM reading_memberships m WHERE m . publication_id = i . id )').fetchone())[0]
        return dict(folders=[dict(folder,count=folder_counts.get(folder['id'],0)) for folder in folders],
                    feeds=[dict(feed,count=feed_counts[feed['id']]['visible'],has_unviewed=bool(feed_counts[feed['id']]['unviewed'])) for feed in feeds],
                    count=visible)

    def _contents(self, owner: str, scope: str) -> FolderContents:
        from oko.storage.material_read import material_inputs, item

        self._store.subscriptions.check_owner(scope, owner)
        condition = 'm . feed_id = %s' if scope == 'feed' else 'f . folder_id = %s'
        rows = list(self._store.db.execute(f'SELECT DISTINCT i . id , i . members FROM items i JOIN reading_memberships m ON m . publication_id = i . id JOIN feeds f ON f . id = m . feed_id WHERE i . viewed = 0 AND {condition} ORDER BY i . id', (owner,)))
        cards = []
        # Keep the exact publication identities (including Telegram members)
        # even when a completed publication cannot currently be read.
        ids = [row['id'] for row in rows]
        for start in range(0, len(ids), 400):
            with material_inputs(self._store.db, set(ids[start:start+400])):
                for identity in ids[start:start+400]:
                    row = item(self._store.db, identity)
                    assert row is not None
                    if row['feed_id'] is not None and (not row['complete'] or self._store.reading_content(row) is not None):
                        cards.append(identity)
        return FolderContents(tuple((row['id'], row['members']) for row in rows), tuple(cards))

    @reading_snapshot()
    def folder_contents(self, folder: str) -> FolderContents:
        return self._contents(folder, 'folder')

    @reading_snapshot()
    def feed_contents(self, feed: str) -> FolderContents:
        return self._contents(feed, 'feed')

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
