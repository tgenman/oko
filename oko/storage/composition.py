"""Current reading composition; no persisted snapshot or lifecycle ownership."""
from oko.storage.database import expect_row
from oko.storage.reading import reading_snapshot
from dataclasses import dataclass
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

    def page(self, options: dict[str, Any], cursor: str | None, limit: int) -> tuple[list[str], int] | None:
        """Return the inclusive cursor page and total without reading card bodies."""
        target=options['target']
        scope=target['scope']
        membership={
            'all': 'EXISTS (SELECT 1 FROM feed_publications m JOIN feeds owner_feed ON owner_feed.id=m.feed_id WHERE m.publication_id=i.id)',
            'feed': 'EXISTS (SELECT 1 FROM feed_publications m JOIN feeds owner_feed ON owner_feed.id=m.feed_id WHERE m.publication_id=i.id AND m.feed_id=%(owner)s)',
            'folder': 'EXISTS (SELECT 1 FROM feed_publications m JOIN feeds owner_feed ON owner_feed.id=m.feed_id WHERE m.publication_id=i.id AND owner_feed.folder_id=%(owner)s)',
        }[scope]
        selected=f"SELECT i.id, i.published, i.published_known FROM items_read i JOIN feeds f ON f.id=i.feed_id WHERE i.viewed=0 AND (%(hidden)s=1 OR i.status IS DISTINCT FROM 'filter_out') AND {membership}"
        cte=f"WITH selected AS ({selected}), ordered AS (SELECT id, CASE WHEN published_known=0 THEN 1 ELSE 0 END AS missing, CASE WHEN published_known=0 THEN 0 WHEN %(sort)s='oldest' THEN published ELSE -published END AS stamp FROM selected)"
        args=dict(owner=target.get('feed_id',target.get('folder_id','')),sort=options['sort'],hidden=int(options['show_hidden']),cursor=cursor,limit=limit+1)
        db=self._store.db
        total=expect_row(db.execute(cte+' SELECT count(*) FROM ordered',args).fetchone())[0]
        if cursor is not None and not db.execute(cte+' SELECT 1 FROM ordered WHERE id = %(cursor)s',args).fetchone():return None
        predicate=" WHERE (missing, stamp, id) >= (SELECT missing, stamp, id FROM ordered WHERE id = %(cursor)s)" if cursor is not None else ''
        rows=db.execute(cte+' SELECT id FROM ordered'+predicate+' ORDER BY missing, stamp, id LIMIT %(limit)s',args)
        return [row[0] for row in rows],total

    @reading_snapshot()
    def navigation(self) -> Navigation:
        """Count visible original publications in their current feeds and folders."""
        folders = self._store.subscriptions.folders()
        feeds = self._store.subscriptions.feeds()
        feed_counts={row['feed']:row for row in self._store.db.execute('''SELECT m.feed_id AS feed, count(*) AS unviewed,
            count(*) FILTER (WHERE i.status IS DISTINCT FROM 'filter_out') AS visible
            FROM feed_publications m JOIN publications i ON i.id=m.publication_id
            WHERE i.viewed=0 GROUP BY m.feed_id''')}
        folder_counts={row['folder']:row['visible'] for row in self._store.db.execute('''SELECT f.folder_id AS folder,
            count(DISTINCT m.publication_id) AS visible FROM feed_publications m JOIN feeds f ON f.id=m.feed_id
            JOIN publications i ON i.id=m.publication_id
            WHERE i.viewed=0 AND i.status IS DISTINCT FROM 'filter_out' GROUP BY f.folder_id''')}
        visible=expect_row(self._store.db.execute("SELECT count(*) FROM items_read i WHERE i.viewed=0 AND i.status IS DISTINCT FROM 'filter_out' AND EXISTS (SELECT 1 FROM feed_publications m WHERE m.publication_id=i.id)").fetchone())[0]
        return dict(folders=[dict(folder,count=folder_counts.get(folder['id'],0)) for folder in folders],
                    feeds=[dict(feed,count=feed_counts[feed['id']]['visible'] if feed['id'] in feed_counts else 0,
                                has_unviewed=bool(feed_counts[feed['id']]['unviewed']) if feed['id'] in feed_counts else False) for feed in feeds],
                    count=visible)

    def _contents(self, owner: str, scope: str) -> FolderContents:
        self._store.subscriptions.check_owner(scope, owner)
        condition = 'm . feed_id = %s' if scope == 'feed' else 'f . folder_id = %s'
        rows = list(self._store.db.execute(f'SELECT DISTINCT i . id , i . members , i . feed_id FROM items_read i JOIN feed_publications m ON m . publication_id = i . id JOIN feeds f ON f . id = m . feed_id WHERE i . viewed = 0 AND {condition} ORDER BY i . id', (owner,)))
        # Pending and damaged publications both have a visible card. Completion
        # still captures every member, including those with no current feed.
        return FolderContents(tuple((row['id'], row['members']) for row in rows),
                              tuple(row['id'] for row in rows if row['feed_id'] is not None))

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
            from oko.storage import material_read
            try:
                owner,_ = material_read.feed(self._store.db,material['feed_id'])
            except ValueError:
                return material
            return _reading_heading(material, owner, self._store.original(identity)[0] if material['complete'] else {})
        return material

    def members(self, identity: str) -> list[str]:
        return [identity]

    def unviewed(self, identities: list[str]) -> set[str]:
        selected = sorted(set(identities))
        members: set[str] = set()
        for start in range(0, len(selected), 400):
            batch = selected[start:start+400]
            marks = ','.join('%s' for _ in batch)
            members.update(row[0] for row in self._store.db.execute(
                f'SELECT id FROM items_read WHERE viewed = 0 AND id IN ( {marks} )', batch))
        return members
