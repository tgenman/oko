"""Persisted versions of reader entities and their derived membership.

Versions are updated by the same database transaction as their inputs. There is
no database cursor or event history. Lists retain only a digest of service IDs.
"""
from oko.storage.sqlite_schema import is_sqlite
from oko.storage.database import expect_row, Connection, Row
import hashlib
import json
import sqlite3
from oko.storage.transaction import begin_write
from oko.storage.connection import ReadingConnection
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
        if is_sqlite(self.db):
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS reader_identity (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reader_entities (
              kind TEXT, identity TEXT, version INTEGER NOT NULL, changed REAL NOT NULL,
              PRIMARY KEY(kind,identity));
            CREATE TABLE IF NOT EXISTS reader_originals (feed TEXT PRIMARY KEY, value INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS reader_visibility (item TEXT PRIMARY KEY, hidden INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS reader_feed_counts (feed TEXT PRIMARY KEY, unviewed INTEGER NOT NULL, visible INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS reader_folder_counts (folder TEXT PRIMARY KEY, visible INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS reader_lists (
              identity TEXT PRIMARY KEY, scope TEXT, owner TEXT, sort TEXT, hidden INTEGER,
              version INTEGER NOT NULL, changed REAL NOT NULL, digest TEXT NOT NULL, pending INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS reader_job_time (job INTEGER PRIMARY KEY, deadline REAL, ready INTEGER);
            CREATE INDEX IF NOT EXISTS reader_job_due ON reader_job_time(deadline) WHERE ready=0;
            CREATE TABLE IF NOT EXISTS reader_runtime (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reader_youtube_time (id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS reader_problem_expiry ON reader_problems(finished) WHERE finished IS NOT NULL;
            CREATE INDEX IF NOT EXISTS reader_action_expiry ON reader_action_results(completed) WHERE completed IS NOT NULL;
            ''')
            if not any(row['name']=='pending' for row in self.db.execute('PRAGMA table_info(reader_lists)')):
                with self.db:
                    for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'reader_v_%'").fetchall():
                        self.db.execute('DROP TRIGGER "'+row['name'].replace('"','""')+'"')
                    self.db.execute('ALTER TABLE reader_lists ADD COLUMN pending INTEGER NOT NULL DEFAULT 0')
        self.db.before_commit=lambda: self.flush_reading_lists(self.db)
        if is_sqlite(self.db):
            self.db.execute('CREATE INDEX IF NOT EXISTS reader_lists_pending ON reader_lists(identity) WHERE pending=1')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO reader_identity VALUES (1,?)', (str(uuid.uuid4()),))
            for kind, identity in [('status', 'runtime'), ('navigation', 'navigation')]:
                self.db.execute('INSERT OR IGNORE INTO reader_entities VALUES (?,?,1,?)', (kind, identity, store.wall_clock()))
            for kind, table in [('feed','feeds'),('folder','folders'),('card','items'),('material','items'),('filter','items'),('theses','items'),('media','items')]:
                self.db.execute(f"INSERT OR IGNORE INTO reader_entities SELECT ?,id,1,? FROM {table}", (kind, store.wall_clock()))
            self.db.execute('INSERT OR IGNORE INTO reader_visibility SELECT id,0 FROM items')
            self.db.execute('''INSERT INTO reader_feed_counts
                SELECT f.id,count(i.id),coalesce(sum(CASE WHEN i.id IS NOT NULL AND coalesce(v.hidden,0)=0 THEN 1 ELSE 0 END),0)
                FROM feeds f LEFT JOIN reading_memberships m ON m.feed_id=f.id LEFT JOIN items i ON i.id=m.publication_id AND i.viewed=0
                LEFT JOIN reader_visibility v ON v.item=i.id GROUP BY f.id
                ON CONFLICT(feed) DO UPDATE SET unviewed=excluded.unviewed,visible=excluded.visible WHERE unviewed IS NOT excluded.unviewed OR visible IS NOT excluded.visible''')
            # SUM's synthetic LEFT JOIN row is not an actual publication.
            self.db.execute('UPDATE reader_feed_counts SET visible=0 WHERE unviewed=0 AND visible!=0')
            self.db.execute('''INSERT INTO reader_folder_counts SELECT f.id,coalesce(sum(c.visible),0)
                FROM folders f LEFT JOIN feeds s ON s.folder_id=f.id
                LEFT JOIN reader_feed_counts c ON c.feed=s.id GROUP BY f.id
                ON CONFLICT(folder) DO UPDATE SET visible=excluded.visible WHERE visible IS NOT excluded.visible''')
            self.db.execute('''INSERT INTO reader_job_time SELECT id,next,CASE WHEN next<=? OR status!='pending' THEN 1 ELSE 0 END FROM jobs WHERE kind IN ('fetch','filter','theses')
                ON CONFLICT(job) DO NOTHING''', (store.clock(),))
        self.identity = str(expect_row(self.db.execute('SELECT value FROM reader_identity WHERE id=1').fetchone())[0])
        if is_sqlite(self.db):
            self._install()
        with self.db:
            self.db.membership_feeds={r[0] for r in self.db.execute('SELECT id FROM feeds')}
            self.db.membership_folders={r[0] for r in self.db.execute('SELECT id FROM folders')}
            self.refresh_memberships(self.db)

    @staticmethod
    def flush_reading_lists(db: Connection) -> None:
        if not is_sqlite(db):
            for row in db.execute('SELECT kind,identity FROM reader_membership_dirty'):
                (db.membership_feeds if row['kind']=='feed' else db.membership_folders).add(row['identity'])
            db.execute('DELETE FROM reader_membership_dirty')
        if getattr(db,'membership_feeds',None) or getattr(db,'membership_folders',None):
            ReaderVersions.refresh_memberships(db)
        rows=db.execute('SELECT identity,scope,owner,sort,hidden,digest FROM reader_lists WHERE pending=1').fetchall()
        for identity,scope,owner,sort,hidden,previous in rows:
            ids=[row[0] for row in db.execute(ReaderVersions._ids(scope,':owner',':sort',':hidden'),dict(scope=scope,owner=owner,sort=sort,hidden=hidden))]
            digest=hashlib.sha256(json.dumps(ids,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            db.execute('UPDATE reader_lists SET version=version+?,changed=CASE WHEN ?=1 THEN oko_reader_time() ELSE changed END,digest=?,pending=0 WHERE identity=?',
                         (int(digest!=previous),int(digest!=previous),digest,identity))

    @staticmethod
    def refresh_memberships(db: Connection) -> None:
        feeds: set[str]=getattr(db,'membership_feeds',set())
        folders: set[str]=getattr(db,'membership_folders',set())
        db.membership_feeds=set()
        db.membership_folders=set()
        for feed in feeds:
            db.execute("""UPDATE reader_feed_counts SET
                unviewed=(SELECT count(*) FROM reading_memberships m JOIN items i ON i.id=m.publication_id WHERE m.feed_id=feed AND i.viewed=0),
                visible=(SELECT count(*) FROM reading_memberships m JOIN items i ON i.id=m.publication_id LEFT JOIN reader_visibility v ON v.item=i.id
                    WHERE m.feed_id=feed AND i.viewed=0 AND coalesce(v.hidden,0)=0) WHERE feed=?""",(feed,))
            row=db.execute('SELECT folder_id FROM feeds WHERE id=?',(feed,)).fetchone()
            if row:
                folders.add(row[0])
            db.execute("UPDATE reader_lists SET pending=1 WHERE scope='all' OR (scope='feed' AND owner=?)",(feed,))
        for folder in folders:
            db.execute("""UPDATE reader_folder_counts SET visible=(SELECT count(DISTINCT m.publication_id)
                FROM reading_memberships m JOIN feeds f ON f.id=m.feed_id JOIN items i ON i.id=m.publication_id
                LEFT JOIN reader_visibility v ON v.item=i.id WHERE f.folder_id=folder AND i.viewed=0 AND coalesce(v.hidden,0)=0) WHERE folder=?""",(folder,))
            db.execute("UPDATE reader_lists SET pending=1 WHERE scope='folder' AND owner=?",(folder,))

    @staticmethod
    def functions(db: sqlite3.Connection, clock: Callable[[], float], wall_clock: Callable[[], float]) -> None:
        def mark(field: str, identity: str | None) -> None:
            if identity is not None:
                values: set[str]=getattr(db,field,set())
                values.add(identity)
                setattr(db,field,values)
        db.create_function('oko_memberships_changed',1,lambda feed: mark('membership_feeds',feed))
        db.create_function('oko_folder_memberships_changed',1,lambda folder: mark('membership_folders',folder))
        db.create_function('oko_reader_time', 0, wall_clock)
        db.create_function('oko_reader_clock', 0, clock)
        db.create_function('oko_reader_hash', 1, lambda value: hashlib.sha256(str(value).encode()).hexdigest(), deterministic=True)
        if isinstance(db, ReadingConnection):
            ready = False

            def flush() -> None:
                nonlocal ready
                if not ready:
                    ready = any(row[1] == 'pending' for row in db.execute('PRAGMA table_info(reader_lists)'))
                if ready:
                    ReaderVersions.flush_reading_lists(db)

            db.before_commit = flush

    @staticmethod
    def _bump(kind: str, identity: str) -> str:
        return f"""INSERT INTO reader_entities VALUES ('{kind}',{identity},1,oko_reader_time())
          ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;"""

    def media_changed(self, item: str) -> None:
        for kind in ('material','media','card'):
            self.db.execute(self._bump(kind, '?'), (item,))

    def linked_changed(self, item: str) -> None:
        self.media_changed(item)
        for kind in ('filter','theses'):
            self.db.execute(self._bump(kind,'?'),(item,))

    @classmethod
    def _material(cls, identity: str, *, card: bool = True) -> str:
        return cls._bump('material', identity) + (cls._bump('card', identity) if card else '')

    @classmethod
    def _related(cls, identity: str) -> str:
        return ''.join(cls._bump(kind, identity) for kind in ('filter','theses','media'))

    @staticmethod
    def _originals(where: str) -> str:
        return f"""INSERT INTO reader_originals SELECT f.id,coalesce(
            (SELECT CAST(json_extract(value,'$') AS INTEGER) FROM settings WHERE scope='feed' AND owner=f.id AND field='originals'),
            (SELECT CAST(json_extract(value,'$') AS INTEGER) FROM settings WHERE scope='folder' AND owner=f.folder_id AND field='originals'),
            (SELECT CAST(json_extract(value,'$') AS INTEGER) FROM settings WHERE scope='oko' AND owner='global' AND field='originals'),1)
            FROM feeds f WHERE {where} ON CONFLICT(feed) DO UPDATE SET value=excluded.value WHERE value IS NOT excluded.value;"""

    @staticmethod
    def _ids(scope: str, owner: str, sort: str, hidden: str) -> str:
        membership = {
            'all': 'EXISTS (SELECT 1 FROM reading_memberships m JOIN feeds owner_feed ON owner_feed.id=m.feed_id WHERE m.publication_id=i.id)',
            'feed': f'i.id IN (SELECT m.publication_id FROM reading_memberships m JOIN feeds owner_feed ON owner_feed.id=m.feed_id WHERE m.feed_id={owner})',
            'folder': f'i.id IN (SELECT m.publication_id FROM reading_memberships m JOIN feeds owner_feed ON owner_feed.id=m.feed_id WHERE owner_feed.folder_id={owner})',
        }[scope]
        return f"""SELECT i.id FROM items i JOIN feeds f ON f.id=i.feed_id
            LEFT JOIN reader_visibility v ON v.item=i.id
            WHERE i.viewed=0 AND ({hidden}=1 OR coalesce(v.hidden,0)=0)
              AND {membership}
            ORDER BY CASE WHEN {sort}='importance' THEN CASE f.importance WHEN 'high' THEN -2 WHEN 'elevated' THEN -1 ELSE 0 END ELSE 0 END,
              CASE WHEN {sort}!='importance' THEN CAST(i.published_known=0 AS INTEGER) ELSE 0 END,
              CASE WHEN {sort}='importance' THEN -i.published WHEN i.published_known=0 THEN 0 WHEN {sort}='oldest' THEN i.published ELSE -i.published END,
              CASE WHEN {sort}!='importance' THEN i.feed_id ELSE '' END,
              CASE WHEN {sort}='importance' THEN 0 WHEN {sort}='oldest' THEN -coalesce(i.source_order,0) ELSE coalesce(i.source_order,0) END,i.id"""

    @classmethod
    def _lists(cls, where: str) -> str:
        return f"UPDATE reader_lists SET pending=1 WHERE pending=0 AND identity IN (SELECT l.identity FROM reader_lists l WHERE {where});"

    @classmethod
    def _feed_lists(cls, feed: str) -> str:
        return cls._lists(f"l.scope='all' OR (l.scope='feed' AND l.owner={feed}) OR (l.scope='folder' AND l.owner=(SELECT folder_id FROM feeds WHERE id={feed}))")

    def _trigger(self, name: str, event: str, table: str, body: str, when: str = '') -> None:
        self.db.execute(f'CREATE TRIGGER IF NOT EXISTS reader_v_{name} AFTER {event} ON {table} '
                        + (f'WHEN {when} ' if when else '') + f'BEGIN {body} END')

    def _install(self) -> None:
        status = self._bump('status', "'runtime'")
        navigation = self._bump('navigation', "'navigation'")
        with self.db:
            begin_write(self.db)
            for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'reader_v_%'").fetchall():
                self.db.execute('DROP TRIGGER "' + row[0].replace('"','""') + '"')
            for event,alias in [('INSERT','NEW'),('DELETE','OLD')]:
                self._trigger('shared_'+event,event,'feed_publications',
                    f'SELECT oko_memberships_changed({alias}.feed_id);'+self._bump('navigation',"'navigation'")+
                    'INSERT OR IGNORE INTO processing_dirty VALUES ('+alias+'.publication_id);')
                self._trigger('membership_items_'+event,event,'items',f'SELECT oko_memberships_changed({alias}.feed_id);',alias+'.platform IS NOT NULL')
            shared="SELECT oko_memberships_changed(feed_id) FROM feed_publications WHERE publication_id=NEW.id;"
            self._trigger('membership_items_UPDATE','UPDATE','items',shared+
                'SELECT oko_memberships_changed(OLD.feed_id);SELECT oko_memberships_changed(NEW.feed_id);',
                'NEW.platform IS NOT NULL AND (NEW.viewed IS NOT OLD.viewed OR NEW.feed_id IS NOT OLD.feed_id OR NEW.published IS NOT OLD.published OR NEW.published_known IS NOT OLD.published_known OR NEW.source_order IS NOT OLD.source_order)')
            self._trigger('membership_visibility','UPDATE','reader_visibility',
                'SELECT oko_memberships_changed(feed_id) FROM feed_publications WHERE publication_id=NEW.item;',
                'NEW.hidden IS NOT OLD.hidden')
            self._trigger('membership_folder','UPDATE','feeds',
                '''SELECT oko_memberships_changed(NEW.id);SELECT oko_folder_memberships_changed(OLD.folder_id);
                SELECT oko_memberships_changed(feed_id) FROM feed_publications WHERE publication_id IN (SELECT id FROM items WHERE feed_id=NEW.id);''',
                'NEW.folder_id IS NOT OLD.folder_id OR NEW.importance IS NOT OLD.importance')
            self._trigger('counts', 'UPDATE', 'reader_feed_counts',
                "UPDATE reader_folder_counts SET visible=visible+NEW.visible-OLD.visible WHERE folder=(SELECT folder_id FROM feeds WHERE id=NEW.feed);"
                + self._feed_lists('NEW.feed'),
                'NEW.visible IS NOT OLD.visible OR NEW.unviewed IS NOT OLD.unviewed')
            self._trigger('counts_navigation','UPDATE','reader_feed_counts',navigation,
                'NEW.visible IS NOT OLD.visible OR (NEW.unviewed>0) IS NOT (OLD.unviewed>0)')
            self._trigger('folder_count', 'UPDATE', 'reader_folder_counts', self._bump('folder','NEW.folder') + navigation,
                          'NEW.visible IS NOT OLD.visible')
            self._trigger('item_insert', 'INSERT', 'items', self._material('NEW.id') + self._related('NEW.id') + status + '''
                INSERT INTO reader_visibility VALUES (NEW.id,0) ON CONFLICT(item) DO NOTHING;
                INSERT INTO reader_feed_counts SELECT NEW.feed_id,CASE WHEN NEW.viewed=0 THEN 1 ELSE 0 END,CASE WHEN NEW.viewed=0 THEN 1 ELSE 0 END WHERE NEW.feed_id IS NOT NULL
                ON CONFLICT(feed) DO UPDATE SET unviewed=unviewed+excluded.unviewed,visible=visible+excluded.visible;''')
            fields = ('feed_id','url','published','published_known','source_order','complete','members','viewed')
            self._trigger('item_data','UPDATE','items', self._material('NEW.id') + status,
                          ' OR '.join(f'NEW.{f} IS NOT OLD.{f}' for f in fields))
            self._trigger('item_inputs','UPDATE','items',self._bump('filter','NEW.id')+self._bump('theses','NEW.id'),
                'NEW.complete IS NOT OLD.complete OR NEW.members IS NOT OLD.members OR NEW.feed_id IS NOT OLD.feed_id')
            self._trigger('item_media_available','UPDATE','items',self._bump('media','NEW.id'),'NEW.complete IS NOT OLD.complete')
            self._trigger('item_membership','UPDATE','items', '''
                UPDATE reader_feed_counts SET unviewed=unviewed-CASE WHEN OLD.viewed=0 THEN 1 ELSE 0 END,
                    visible=visible-CASE WHEN OLD.viewed=0 AND coalesce((SELECT hidden FROM reader_visibility WHERE item=OLD.id),0)=0 THEN 1 ELSE 0 END WHERE feed=OLD.feed_id;
                UPDATE reader_feed_counts SET unviewed=unviewed+CASE WHEN NEW.viewed=0 THEN 1 ELSE 0 END,
                    visible=visible+CASE WHEN NEW.viewed=0 AND coalesce((SELECT hidden FROM reader_visibility WHERE item=NEW.id),0)=0 THEN 1 ELSE 0 END WHERE feed=NEW.feed_id;
                ''', 'NEW.viewed IS NOT OLD.viewed OR NEW.feed_id IS NOT OLD.feed_id')
            self._trigger('item_order','UPDATE','items', self._feed_lists('NEW.feed_id'),
                          'NEW.published IS NOT OLD.published OR NEW.published_known IS NOT OLD.published_known OR NEW.source_order IS NOT OLD.source_order')
            self._trigger('item_delete','DELETE','items', self._material('OLD.id') + self._related('OLD.id') + status + '''
                UPDATE reader_feed_counts SET unviewed=unviewed-CASE WHEN OLD.viewed=0 THEN 1 ELSE 0 END,
                visible=visible-CASE WHEN OLD.viewed=0 AND coalesce((SELECT hidden FROM reader_visibility WHERE item=OLD.id),0)=0 THEN 1 ELSE 0 END WHERE feed=OLD.feed_id;
                DELETE FROM reader_visibility WHERE item=OLD.id;''')
            self._trigger('hidden','UPDATE','reader_visibility', self._bump('card','NEW.item')+self._bump('filter','NEW.item')+self._bump('theses','NEW.item') + '''
                UPDATE reader_feed_counts SET visible=visible+OLD.hidden-NEW.hidden
                WHERE feed=(SELECT feed_id FROM items WHERE id=NEW.item AND viewed=0);''', 'NEW.hidden IS NOT OLD.hidden')
            for event, alias in [('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')]:
                self._trigger('deleted_'+event,event,'source_deleted',self._bump('material',alias+'.item') + status,
                    'NEW.item IS NOT OLD.item' if event=='UPDATE' else '')
                self._trigger('filter_input_'+event,event,'filter_inputs',self._bump('filter',alias+'.item')+self._bump('theses',alias+'.item')+self._bump('card',alias+'.item'),
                    'NEW.signature IS NOT OLD.signature' if event=='UPDATE' else '')
            for event, alias in [('INSERT','NEW'),('DELETE','OLD')]:
                self._trigger('feed_'+event,event,'feeds',self._bump('feed',alias+'.id')+navigation+status)
                self._trigger('folder_'+event,event,'folders',self._bump('folder',alias+'.id')+navigation+status)
            self._trigger('folder_counter_added','INSERT','folders', 'INSERT INTO reader_folder_counts VALUES (NEW.id,0) ON CONFLICT(folder) DO NOTHING;')
            self._trigger('feed_counter_added','INSERT','feeds', 'INSERT INTO reader_feed_counts VALUES (NEW.id,0,0) ON CONFLICT(feed) DO NOTHING;'+self._originals('f.id=NEW.id'))
            self._trigger('folder_update','UPDATE','folders',self._bump('folder','NEW.id')+navigation+status,
                          'NEW.title IS NOT OLD.title OR NEW.position IS NOT OLD.position')
            feed_fields = ('title','folder_id','identity','kind','original_urls','connection','transport','importance','initial_days','public_ai','read_history','auto_read','telegram_peer','telegram_account','telegram_username')
            self._trigger('feed_update','UPDATE','feeds',self._bump('feed','NEW.id')+status,
                          ' OR '.join(f'NEW.{f} IS NOT OLD.{f}' for f in feed_fields))
            self._trigger('feed_material','UPDATE','feeds',"""INSERT INTO reader_entities
                SELECT 'material',id,1,oko_reader_time() FROM items WHERE feed_id=NEW.id AND viewed=0
                ON CONFLICT(kind,identity) DO UPDATE SET version=version+1,changed=excluded.changed;""",
                'NEW.kind IS NOT OLD.kind OR NEW.transport IS NOT OLD.transport')
            self._trigger('feed_folder','UPDATE','feeds', '''
                UPDATE reader_folder_counts SET visible=visible-coalesce((SELECT visible FROM reader_feed_counts WHERE feed=NEW.id),0) WHERE folder=OLD.folder_id;
                UPDATE reader_folder_counts SET visible=visible+coalesce((SELECT visible FROM reader_feed_counts WHERE feed=NEW.id),0) WHERE folder=NEW.folder_id;
                ''' + self._originals('f.id=NEW.id') + self._lists("l.scope='folder' AND l.owner IN (OLD.folder_id,NEW.folder_id)"), 'NEW.folder_id IS NOT OLD.folder_id')
            self._trigger('feed_importance','UPDATE','feeds',self._lists("l.sort='importance' AND (l.scope='all' OR (l.scope='folder' AND l.owner=NEW.folder_id))"), 'NEW.importance IS NOT OLD.importance')
            for table, key in [('initial_history','feed'),('history_members','feed'),('telegram_bindings','feed'),('telegram_avatars','feed')]:
                for event, alias in [('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')]:
                    guard = ''
                    if event=='UPDATE':
                        cols = {'initial_history':['selection','frozen','paused','scan_complete'],'history_members':['outcome'], 'telegram_bindings':['binding'],'telegram_avatars':['digest']}[table]
                        guard=' OR '.join(f'NEW.{c} IS NOT OLD.{c}' for c in cols)
                    body=self._bump('feed',f'{alias}.{key}')+status
                    if table=='telegram_bindings':
                        body+=f"INSERT INTO reader_entities SELECT 'material',id,1,oko_reader_time() FROM items WHERE feed_id={alias}.feed AND viewed=0 ON CONFLICT(kind,identity) DO UPDATE SET version=version+1,changed=excluded.changed;"
                    self._trigger(table+'_'+event,event,table,body,guard)
            for event, alias in [('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')]:
                self._trigger('job_'+event,event,'jobs', status, "NEW.status IS NOT OLD.status OR NEW.error IS NOT OLD.error OR NEW.next IS NOT OLD.next OR NEW.problem_code IS NOT OLD.problem_code" if event=='UPDATE' else '')
                for kind in ('filter','theses'):
                    self._trigger('job_'+kind+'_'+event,event,'jobs',self._bump(kind,alias+'.item'),
                        f"{alias}.kind='{kind}'"+(" AND (NEW.status IS NOT OLD.status OR NEW.error IS NOT OLD.error OR NEW.next IS NOT OLD.next)" if event=='UPDATE' else ''))
                    self._trigger('job_card_'+kind+'_'+event,event,'jobs',self._bump('card',alias+'.item'),
                        f"{alias}.kind='{kind}'"+(" AND (NEW.status IS NOT OLD.status OR (NEW.error IS NULL) IS NOT (OLD.error IS NULL))" if event=='UPDATE' else ''))
                if event=='UPDATE':
                    before="CASE WHEN coalesce((SELECT complete FROM items WHERE id=OLD.item),0)=0 THEN OLD.status IN ('pending','running') ELSE OLD.status='running' OR (OLD.status='pending' AND OLD.next<=oko_reader_clock()) END"
                    after="CASE WHEN coalesce((SELECT complete FROM items WHERE id=NEW.item),0)=0 THEN NEW.status IN ('pending','running') ELSE NEW.status='running' OR (NEW.status='pending' AND NEW.next<=oko_reader_clock()) END"
                    guard="NEW.kind='fetch' AND ("+before+") IS NOT ("+after+")"
                else:guard=f"{alias}.kind='fetch'"
                self._trigger('job_fetch_'+event,event,'jobs',self._material(alias+'.item'),guard)
                self._trigger('filter_eligibility_'+event,event,'jobs',self._bump('theses',alias+'.item'),
                    f"{alias}.kind='filter'"+(" AND (NEW.status='done') IS NOT (OLD.status='done')" if event=='UPDATE' else ''))
                if event!='DELETE':
                    self._trigger('job_time_'+event,event,'jobs', '''INSERT INTO reader_job_time VALUES (NEW.id,NEW.next,CASE WHEN NEW.status!='pending' OR NEW.next<=oko_reader_clock() THEN 1 ELSE 0 END)
                        ON CONFLICT(job) DO UPDATE SET deadline=excluded.deadline,ready=excluded.ready;''', "NEW.kind IN ('fetch','filter','theses')")
                else:
                    self._trigger('job_time_delete',event,'jobs','DELETE FROM reader_job_time WHERE job=OLD.id;')
            self._trigger('time_ready','UPDATE','reader_job_time',status+"""INSERT INTO reader_entities
                SELECT CASE kind WHEN 'fetch' THEN 'material' ELSE kind END,item,1,oko_reader_time() FROM jobs WHERE id=NEW.job AND (kind='fetch' OR error IS NOT NULL)
                ON CONFLICT(kind,identity) DO UPDATE SET version=version+1,changed=excluded.changed;
                INSERT INTO reader_entities SELECT 'card',item,1,oko_reader_time() FROM jobs WHERE id=NEW.job AND kind='fetch'
                ON CONFLICT(kind,identity) DO UPDATE SET version=version+1,changed=excluded.changed;""", 'NEW.ready IS NOT OLD.ready')
            self._trigger('originals_changed','UPDATE','reader_originals',"""INSERT INTO reader_entities SELECT 'material',id,1,oko_reader_time() FROM items WHERE feed_id=NEW.feed AND viewed=0
                ON CONFLICT(kind,identity) DO UPDATE SET version=version+1,changed=excluded.changed;""",'NEW.value IS NOT OLD.value')
            self.db.execute(self._originals('1'))
            # These tables contain public status facts, not content or execution-only generations.
            for table, columns in [('reader_problems',['body','finished','outcome']),('reader_action_results',['result']),('telegram_read_state',['since','initial','confirmed','waiting','error']),('telegram_read_members',['eligible','missing','reason']),('youtube_access',['next_at','blocked_until','strikes'])]:
                for event in ['INSERT','UPDATE','DELETE']:
                    self._trigger(table+'_status_'+event,event,table,status,
                        ' OR '.join(f'NEW.{c} IS NOT OLD.{c}' for c in columns) if event=='UPDATE' else '')
            self._trigger('runtime','UPDATE','reader_runtime',status,'NEW.body IS NOT OLD.body')
            self._trigger('runtime_first','INSERT','reader_runtime',status)
            self._trigger('youtube_time','UPDATE','reader_youtube_time',status,'NEW.paused IS NOT OLD.paused')
            self._trigger('profile','UPDATE','telegram_profiles',status+'''INSERT INTO reader_entities
                SELECT 'feed',feed,1,oko_reader_time() FROM telegram_bindings WHERE json_extract(binding,'$.role')=NEW.role
                ON CONFLICT(kind,identity) DO UPDATE SET version=version+1,changed=excluded.changed;''', 'NEW.state IS NOT OLD.state OR NEW.user_id IS NOT OLD.user_id')
            self._trigger('profile_message','UPDATE','telegram_profiles',status,'NEW.message IS NOT OLD.message')
            for event, alias in [('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')]:
                scope=f"{alias}.scope='oko' OR ({alias}.scope='feed' AND f.id={alias}.owner) OR ({alias}.scope='folder' AND f.folder_id={alias}.owner)"
                self._trigger('settings_'+event,event,'settings',self._originals(scope)+status,
                    f"{alias}.field='originals'"+(' AND NEW.value IS NOT OLD.value' if event=='UPDATE' else ''))
                self._trigger('settings_status_'+event,event,'settings',status,'NEW.value IS NOT OLD.value' if event=='UPDATE' else '')
            for event,alias in [('INSERT','NEW'),('DELETE','OLD')]:
                self._trigger('profile_'+event,event,'telegram_profiles',status+f"""INSERT INTO reader_entities
                    SELECT 'feed',feed,1,oko_reader_time() FROM telegram_bindings WHERE json_extract(binding,'$.role')={alias}.role
                    ON CONFLICT(kind,identity) DO UPDATE SET version=version+1,changed=excluded.changed;""")
                self._trigger('state_status_'+event,event,'state',status,
                    f"{alias}.key='work-enabled' OR {alias}.key LIKE 'feed-status:%' OR {alias}.key='processor:codex'")
            for event,alias in [('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')]:
                self._trigger('job_history_'+event,event,'jobs',f"""INSERT INTO reader_entities
                    SELECT DISTINCT 'feed',h.feed,1,oko_reader_time() FROM history_members h
                    WHERE h.item={alias}.item AND h.outcome IN ('retrying','unavailable')
                    ON CONFLICT(kind,identity) DO UPDATE SET version=version+1,changed=excluded.changed;""",
                    f"{alias}.kind='fetch'" + (' AND NEW.error IS NOT OLD.error' if event=='UPDATE' else f' AND {alias}.error IS NOT NULL'))
            self._trigger('state_update','UPDATE','state',status,"NEW.value IS NOT OLD.value AND (NEW.key='work-enabled' OR NEW.key LIKE 'feed-status:%' OR NEW.key='processor:codex')")

    def sync_filter(self, item: str, hidden: bool) -> None:
        query='INSERT INTO reader_visibility VALUES (?,?) ON CONFLICT(item) DO UPDATE SET hidden=excluded.hidden WHERE hidden IS NOT excluded.hidden'
        if self.db.in_transaction:
            self.db.execute(query,(item,int(hidden)))
        else:
            with self.db:self.db.execute(query,(item,int(hidden)))

    def version(self, kind: str, identity: str) -> Row | None:
        return self.db.execute('SELECT version,changed FROM reader_entities WHERE kind=? AND identity=?', (kind,identity)).fetchone()

    def batch(self, keys: set[tuple[str, str]]) -> dict[tuple[str, str], Row]:
        result = {}
        ordered=sorted(keys)
        for start in range(0,len(ordered),499):
            batch=ordered[start:start+499]
            marks=','.join('(?,?)' for _ in batch)
            rows=self.db.execute("""WITH wanted(kind,identity) AS (VALUES """+marks+""")
                SELECT e.*,i.id AS item_exists,i.viewed,f.id AS feed_exists
                FROM wanted w JOIN reader_entities e ON e.kind=w.kind AND e.identity=w.identity
                LEFT JOIN items i ON e.kind IN ('card','material','filter','theses','media') AND i.id=e.identity
                LEFT JOIN feeds f ON e.kind='feed' AND f.id=e.identity""", [v for pair in batch for v in pair])
            result.update({(r['kind'],r['identity']):r for r in rows})
        return result

    @staticmethod
    def list_identity(options: dict[str, Any]) -> str:
        target = options['target']
        return json.dumps([target['scope'],target.get('feed_id',target.get('folder_id','')),options['sort'],options['show_hidden']],ensure_ascii=False,separators=(',',':'))

    def ids(self, options: dict[str, Any]) -> list[str]:
        target=options['target']
        sql=self._ids(target['scope'],':owner',':sort',':hidden')
        return [r[0] for r in self.db.execute(sql,dict(scope=target['scope'],owner=target.get('feed_id',target.get('folder_id','')),sort=options['sort'],hidden=int(options['show_hidden'])))]

    def list_version(self, options: dict[str, Any]) -> Row:
        identity=self.list_identity(options)
        self.db.flush()
        row=self.db.execute('SELECT version,changed FROM reader_lists WHERE identity=?',(identity,)).fetchone()
        if row:return row
        ids=self.ids(options)
        # Match SQLite's canonical JSON array used by write-side list maintenance.
        digest=hashlib.sha256(json.dumps(ids,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        target=options['target']
        with self.db:
            self.db.execute('INSERT INTO reader_lists(identity,scope,owner,sort,hidden,version,changed,digest) VALUES (?,?,?,?,?,1,?,?)',(identity,target['scope'],target.get('feed_id',target.get('folder_id','')),options['sort'],int(options['show_hidden']),self.store.wall_clock(),digest))
        row=self.db.execute('SELECT version,changed FROM reader_lists WHERE identity=?',(identity,)).fetchone()
        assert row is not None
        return row

    def advance(self, metadata: dict[str, Any], now: float | None = None, wall: float | None = None) -> None:
        now=self.store.clock() if now is None else now
        wall=self.store.wall_clock() if wall is None else wall
        with self.db:
            self.db.execute('UPDATE reader_job_time SET ready=1 WHERE ready=0 AND deadline<=?',(now,))
            self.db.execute('INSERT INTO reader_youtube_time SELECT 1,CASE WHEN blocked_until>? THEN 1 ELSE 0 END FROM youtube_access WHERE id=1 ON CONFLICT(id) DO UPDATE SET paused=excluded.paused WHERE paused IS NOT excluded.paused',(wall,))
            self.db.execute('DELETE FROM reader_problems WHERE rowid IN (SELECT rowid FROM reader_problems WHERE finished<=? ORDER BY finished LIMIT 200)',(wall-86400,))
            if now >= self._next_action_cleanup:
                from oko.storage.problem_links import cleanup
                cleanup(self.db, wall - 86400)
                self._next_action_cleanup=now+60
            body=json.dumps(metadata,sort_keys=True,separators=(',',':'))
            self.db.execute('INSERT INTO reader_runtime VALUES (1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body WHERE body IS NOT excluded.body',(body,))
