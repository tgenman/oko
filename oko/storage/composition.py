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


class ReadingComposition:
    def __init__(self, store: 'Store') -> None:
        self._store = store

    def page(self, options: dict[str, Any], cursor: str | None, limit: int) -> tuple[list[str], int] | None:
        """Return the inclusive cursor page and total without reading card bodies."""
        target=options['target']
        scope=target['scope']
        membership={
            'all': 'i.feed_id IS NOT NULL',
            'feed': 'i.feed_id=%(owner)s',
            'folder': 'f.folder_id=%(owner)s',
        }[scope]
        selected=f"SELECT i.id, coalesce(i.published_at, i.discovered_at) AS ordering_time FROM publications i JOIN feeds f ON f.id=i.feed_id WHERE (%(show_read)s=1 OR NOT i.viewed) AND NOT f.deleted AND (%(hidden)s=1 OR i.status IS DISTINCT FROM 'filter_out') AND {membership}"
        cte=f"WITH selected AS ({selected}), ordered AS (SELECT id, CASE WHEN %(sort)s='oldest' THEN ordering_time ELSE -ordering_time END AS stamp FROM selected)"
        args=dict(owner=target.get('feed_id',target.get('folder_id','')),sort=options['sort'],hidden=int(options['show_hidden']),show_read=int(options.get('show_read',False)),cursor=cursor,limit=limit+1)
        db=self._store.db
        total=expect_row(db.execute(cte+' SELECT count(*) FROM ordered',args).fetchone())[0]
        if cursor is not None and not db.execute(cte+' SELECT 1 FROM ordered WHERE id = %(cursor)s',args).fetchone():return None
        predicate=" WHERE (stamp, id) >= (SELECT stamp, id FROM ordered WHERE id = %(cursor)s)" if cursor is not None else ''
        rows=db.execute(cte+' SELECT id FROM ordered'+predicate+' ORDER BY stamp, id LIMIT %(limit)s',args)
        return [row[0] for row in rows],total

    @reading_snapshot()
    def navigation(self) -> Navigation:
        """Count visible original publications in their current feeds and folders."""
        folders = self._store.subscriptions.folders()
        feeds = self._store.subscriptions.feeds()
        feed_counts={row['feed']:row for row in self._store.db.execute("""SELECT i.feed_id AS feed, count(*) AS unviewed,
            count(*) FILTER (WHERE i.status IS DISTINCT FROM 'filter_out') AS visible
            FROM publications i JOIN feeds f ON f.id=i.feed_id WHERE NOT i.viewed AND NOT f.deleted GROUP BY i.feed_id""")}
        folder_counts={row['folder']:row['visible'] for row in self._store.db.execute("""SELECT f.folder_id AS folder,
            count(*) AS visible FROM publications i JOIN feeds f ON f.id=i.feed_id
            WHERE NOT i.viewed AND NOT f.deleted AND i.status IS DISTINCT FROM 'filter_out' GROUP BY f.folder_id""")}
        visible=expect_row(self._store.db.execute("SELECT count(*) FROM publications i JOIN feeds f ON f.id=i.feed_id WHERE NOT i.viewed AND NOT f.deleted AND i.status IS DISTINCT FROM 'filter_out'").fetchone())[0]
        return dict(folders=[dict(folder,count=folder_counts.get(folder['id'],0)) for folder in folders],
                    feeds=[dict(feed,count=feed_counts[feed['id']]['visible'] if feed['id'] in feed_counts else 0,
                                has_unviewed=bool(feed_counts[feed['id']]['unviewed']) if feed['id'] in feed_counts else False) for feed in feeds],
                    count=visible)

    def _contents(self, owner: str, scope: str) -> FolderContents:
        self._store.subscriptions.check_owner(scope, owner)
        condition = 'i.feed_id = %s' if scope == 'feed' else 'f.folder_id = %s'
        rows = list(self._store.db.execute(f"SELECT i.id, COALESCE(i.content #> '{{extra,telegram,members}}','[]'::jsonb)::text AS members, i.feed_id FROM publications i JOIN feeds f ON f.id=i.feed_id WHERE NOT i.viewed AND NOT f.deleted AND {condition} ORDER BY i.id", (owner,)))
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


    def members(self, identity: str) -> list[str]:
        return [identity]

    def unviewed(self, identities: list[str]) -> set[str]:
        selected = sorted(set(identities))
        members: set[str] = set()
        for start in range(0, len(selected), 400):
            batch = selected[start:start+400]
            marks = ','.join('%s' for _ in batch)
            members.update(row[0] for row in self._store.db.execute(
                f'SELECT i.id FROM publications i JOIN feeds f ON f.id=i.feed_id WHERE NOT i.viewed AND NOT f.deleted AND i.id IN ( {marks} )', batch))
        return members
