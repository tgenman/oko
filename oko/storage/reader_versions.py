"""Persisted versions of reader entities and their derived membership.

Versions are updated by the same database transaction as their inputs. There is
no database cursor or event history. Lists retain only a digest of service IDs.
"""
from oko.storage.database import expect_row, Connection, Row
import hashlib
import json
from oko.storage.transaction import begin_write
import uuid
from typing import Any, TYPE_CHECKING
from collections.abc import Callable

if TYPE_CHECKING:
    from oko.storage.store import Store


class ReaderVersions:
    def __init__(self, store: 'Store') -> None:
        self.store, self.db = store, store.db
        self.offset = store.wall_clock() - store.clock()
        self._next_action_cleanup = float('-inf')
        self.db.before_commit=lambda: self.flush_reading_lists(self.db)
        with self.db:
            self.db.execute('INSERT INTO reader_identity VALUES ( 1 , %s ) ON CONFLICT DO NOTHING', (str(uuid.uuid4()),))
            for kind, identity in [('status', 'runtime'), ('navigation', 'navigation')]:
                self.db.execute('INSERT INTO reader_entities VALUES ( %s , %s , 1 , %s ) ON CONFLICT DO NOTHING', (kind, identity, store.wall_clock()))
            for kind, table in [('feed','feeds'),('folder','folders'),('card','items'),('material','items'),('filter','items'),('theses','items'),('media','items')]:
                self.db.execute(f'INSERT INTO reader_entities SELECT %s , id , 1 , %s FROM {table} ON CONFLICT DO NOTHING', (kind, store.wall_clock()))
            self.db.execute('INSERT INTO reader_visibility SELECT id , 0 FROM items_read ON CONFLICT DO NOTHING')
            self.db.execute('INSERT INTO reader_feed_counts SELECT f . id , count ( i . id ) , coalesce ( sum ( CASE WHEN i . id IS NOT NULL AND coalesce ( v . hidden , 0 ) = 0 THEN 1 ELSE 0 END ) , 0 ) FROM feeds f LEFT JOIN reading_memberships m ON m . feed_id = f . id LEFT JOIN unviewed_items_by_feed i ON i . id = m . publication_id AND i . viewed = 0 LEFT JOIN reader_visibility v ON v . item = i . id GROUP BY f . id ON CONFLICT ( feed ) DO UPDATE SET unviewed = excluded . unviewed , visible = excluded . visible WHERE reader_feed_counts . unviewed IS DISTINCT FROM excluded . unviewed OR reader_feed_counts . visible IS DISTINCT FROM excluded . visible')
            # SUM's synthetic LEFT JOIN row is not an actual publication.
            self.db.execute('UPDATE reader_feed_counts SET visible = 0 WHERE unviewed = 0 AND visible != 0')
            self.db.execute('INSERT INTO reader_folder_counts SELECT f . id , coalesce ( sum ( c . visible ) , 0 ) FROM folders f LEFT JOIN feeds s ON s . folder_id = f . id LEFT JOIN reader_feed_counts c ON c . feed = s . id GROUP BY f . id ON CONFLICT ( folder ) DO UPDATE SET visible = excluded . visible WHERE reader_folder_counts . visible IS DISTINCT FROM excluded . visible')
            self.db.execute("INSERT INTO reader_job_time SELECT id , next , CASE WHEN next <= %s OR status != 'pending' THEN 1 ELSE 0 END FROM jobs WHERE kind IN ( 'fetch' , 'filter' , 'theses' ) ON CONFLICT ( job ) DO NOTHING", (store.clock(),))
        self.identity = str(expect_row(self.db.execute('SELECT value FROM reader_identity WHERE id = 1').fetchone())[0])
        with self.db:
            self.db.membership_feeds={r[0] for r in self.db.execute('SELECT id FROM feeds')}
            self.db.membership_folders={r[0] for r in self.db.execute('SELECT id FROM folders')}
            self.refresh_memberships(self.db)

    @staticmethod
    def flush_reading_lists(db: Connection) -> None:
        for row in db.execute('SELECT kind , identity FROM reader_membership_dirty'):
            (db.membership_feeds if row['kind']=='feed' else db.membership_folders).add(row['identity'])
        db.execute('DELETE FROM reader_membership_dirty')
        if getattr(db,'membership_feeds',None) or getattr(db,'membership_folders',None):
            ReaderVersions.refresh_memberships(db)
        rows=db.execute('SELECT identity , scope , owner , sort , hidden , digest FROM reader_lists WHERE pending = 1').fetchall()
        for identity,scope,owner,sort,hidden,previous in rows:
            ids=[row[0] for row in db.execute(ReaderVersions._ids(scope,'%(owner)s','%(sort)s','%(hidden)s'),dict(scope=scope,owner=owner,sort=sort,hidden=hidden))]
            digest=hashlib.sha256(json.dumps(ids,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            db.execute('UPDATE reader_lists SET version = version + %s , changed = CASE WHEN %s = 1 THEN oko_reader_time ( ) ELSE changed END , digest = %s , pending = 0 WHERE identity = %s',
                         (int(digest!=previous),int(digest!=previous),digest,identity))

    @staticmethod
    def refresh_memberships(db: Connection) -> None:
        feeds: set[str]=getattr(db,'membership_feeds',set())
        folders: set[str]=getattr(db,'membership_folders',set())
        db.membership_feeds=set()
        db.membership_folders=set()
        for feed in feeds:
            db.execute('UPDATE reader_feed_counts SET unviewed = ( SELECT count ( * ) FROM reading_memberships m JOIN items_read i ON i . id = m . publication_id WHERE m . feed_id = feed AND i . viewed = 0 ) , visible = ( SELECT count ( * ) FROM reading_memberships m JOIN items_read i ON i . id = m . publication_id LEFT JOIN reader_visibility v ON v . item = i . id WHERE m . feed_id = feed AND i . viewed = 0 AND coalesce ( v . hidden , 0 ) = 0 ) WHERE feed = %s',(feed,))
            row=db.execute('SELECT folder_id FROM feeds WHERE id = %s',(feed,)).fetchone()
            if row:
                folders.add(row[0])
            db.execute("UPDATE reader_lists SET pending = 1 WHERE scope = 'all' OR ( scope = 'feed' AND owner = %s )",(feed,))
        for folder in folders:
            db.execute('UPDATE reader_folder_counts SET visible = ( SELECT count ( DISTINCT m . publication_id ) FROM reading_memberships m JOIN feeds f ON f . id = m . feed_id JOIN items_read i ON i . id = m . publication_id LEFT JOIN reader_visibility v ON v . item = i . id WHERE f . folder_id = folder AND i . viewed = 0 AND coalesce ( v . hidden , 0 ) = 0 ) WHERE folder = %s',(folder,))
            db.execute("UPDATE reader_lists SET pending = 1 WHERE scope = 'folder' AND owner = %s",(folder,))


    @staticmethod
    def _bump(kind: str, identity: str) -> str:
        return f"INSERT INTO reader_entities VALUES ( '{kind}' , {identity} , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed"

    def media_changed(self, item: str) -> None:
        for kind in ('material','media','card'):
            self.db.execute(self._bump(kind, '%s'), (item,))

    def linked_changed(self, item: str) -> None:
        self.media_changed(item)
        for kind in ('filter','theses'):
            self.db.execute(self._bump(kind,'%s'),(item,))




    @staticmethod
    def _ids(scope: str, owner: str, sort: str, hidden: str) -> str:
        membership = {
            'all': 'EXISTS (SELECT 1 FROM reading_memberships m JOIN feeds owner_feed ON owner_feed.id=m.feed_id WHERE m.publication_id=i.id)',
            'feed': f'i.id IN (SELECT m.publication_id FROM reading_memberships m JOIN feeds owner_feed ON owner_feed.id=m.feed_id WHERE m.feed_id={owner})',
            'folder': f'i.id IN (SELECT m.publication_id FROM reading_memberships m JOIN feeds owner_feed ON owner_feed.id=m.feed_id WHERE owner_feed.folder_id={owner})',
        }[scope]
        return f"SELECT i . id FROM items_read i JOIN feeds f ON f . id = i . feed_id LEFT JOIN reader_visibility v ON v . item = i . id WHERE i . viewed = 0 AND ( {hidden} = 1 OR coalesce ( v . hidden , 0 ) = 0 ) AND {membership} ORDER BY CASE WHEN {sort} = 'importance' THEN CASE f . importance WHEN 'high' THEN - 2 WHEN 'elevated' THEN - 1 ELSE 0 END ELSE 0 END , CASE WHEN {sort} != 'importance' THEN CAST ( i . published_known = 0 AS INTEGER ) ELSE 0 END , CASE WHEN {sort} = 'importance' THEN - i . published WHEN i . published_known = 0 THEN 0 WHEN {sort} = 'oldest' THEN i . published ELSE - i . published END , CASE WHEN {sort} != 'importance' THEN i . feed_id ELSE '' END , CASE WHEN {sort} = 'importance' THEN 0 WHEN {sort} = 'oldest' THEN - coalesce ( i . source_order , 0 ) ELSE coalesce ( i . source_order , 0 ) END , i . id"





    def sync_filter(self, item: str, hidden: bool) -> None:
        query='INSERT INTO reader_visibility VALUES ( %s , %s ) ON CONFLICT ( item ) DO UPDATE SET hidden = excluded . hidden WHERE reader_visibility . hidden IS DISTINCT FROM excluded . hidden'
        if self.db.in_transaction:
            self.db.execute(query,(item,int(hidden)))
        else:
            with self.db:self.db.execute(query,(item,int(hidden)))

    def version(self, kind: str, identity: str) -> Row | None:
        return self.db.execute('SELECT version , changed FROM reader_entities WHERE kind = %s AND identity = %s', (kind,identity)).fetchone()

    def batch(self, keys: set[tuple[str, str]]) -> dict[tuple[str, str], Row]:
        result = {}
        ordered=sorted(keys)
        for start in range(0,len(ordered),499):
            batch=ordered[start:start+499]
            marks=','.join('(%s,%s)' for _ in batch)
            rows=self.db.execute(f"WITH wanted ( kind , identity ) AS ( VALUES {marks} ) SELECT e . * , i . id AS item_exists , i . viewed , f . id AS feed_exists FROM wanted w JOIN reader_entities e ON e . kind = w . kind AND e . identity = w . identity LEFT JOIN items_read i ON e . kind IN ( 'card' , 'material' , 'filter' , 'theses' , 'media' ) AND i . id = e . identity LEFT JOIN feeds f ON e . kind = 'feed' AND f . id = e . identity", [v for pair in batch for v in pair])
            result.update({(r['kind'],r['identity']):r for r in rows})
        return result

    @staticmethod
    def list_identity(options: dict[str, Any]) -> str:
        target = options['target']
        return json.dumps([target['scope'],target.get('feed_id',target.get('folder_id','')),options['sort'],options['show_hidden']],ensure_ascii=False,separators=(',',':'))

    def ids(self, options: dict[str, Any]) -> list[str]:
        target=options['target']
        sql=self._ids(target['scope'],'%(owner)s','%(sort)s','%(hidden)s')
        return [r[0] for r in self.db.execute(sql,dict(scope=target['scope'],owner=target.get('feed_id',target.get('folder_id','')),sort=options['sort'],hidden=int(options['show_hidden'])))]

    def list_version(self, options: dict[str, Any]) -> Row:
        identity=self.list_identity(options)
        self.db.flush()
        row=self.db.execute('SELECT version , changed FROM reader_lists WHERE identity = %s',(identity,)).fetchone()
        if row:return row
        ids=self.ids(options)
        # Use the canonical compact JSON array used by write-side list maintenance.
        digest=hashlib.sha256(json.dumps(ids,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        target=options['target']
        with self.db:
            self.db.execute('INSERT INTO reader_lists ( identity , scope , owner , sort , hidden , version , changed , digest ) VALUES ( %s , %s , %s , %s , %s , 1 , %s , %s )',(identity,target['scope'],target.get('feed_id',target.get('folder_id','')),options['sort'],int(options['show_hidden']),self.store.wall_clock(),digest))
        row=self.db.execute('SELECT version , changed FROM reader_lists WHERE identity = %s',(identity,)).fetchone()
        assert row is not None
        return row

    def advance(self, metadata: dict[str, Any], now: float | None = None, wall: float | None = None) -> None:
        now=self.store.clock() if now is None else now
        wall=self.store.wall_clock() if wall is None else wall
        with self.db:
            self.db.execute('UPDATE reader_job_time SET ready = 1 WHERE ready = 0 AND deadline <= %s',(now,))
            self.db.execute('INSERT INTO reader_youtube_time SELECT 1 , CASE WHEN blocked_until > %s THEN 1 ELSE 0 END FROM youtube_access WHERE id = 1 ON CONFLICT ( id ) DO UPDATE SET paused = excluded . paused WHERE reader_youtube_time . paused IS DISTINCT FROM excluded . paused',(wall,))
            self.db.execute('DELETE FROM reader_problems WHERE rowid IN ( SELECT rowid FROM reader_problems WHERE finished <= %s ORDER BY finished LIMIT 200 )',(wall-86400,))
            if now >= self._next_action_cleanup:
                from oko.storage.problem_links import cleanup
                cleanup(self.db, wall - 86400)
                self._next_action_cleanup=now+60
            body=json.dumps(metadata,sort_keys=True,separators=(',',':'))
            self.db.execute('INSERT INTO reader_runtime VALUES ( 1 , %s ) ON CONFLICT ( id ) DO UPDATE SET body = excluded . body WHERE reader_runtime . body IS DISTINCT FROM excluded . body',(body,))
