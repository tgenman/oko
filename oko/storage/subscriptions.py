from oko.storage.database import INTEGRITY_ERRORS
from oko.storage.database import expect_row, Connection, Row
from oko.storage.reading import reading_value
from oko.storage.settings_cache import SettingsCache
from oko.storage.feed_facts import feed_facts
import json
import re
import secrets
from uuid import uuid4
from oko.storage.transaction import begin_write
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from oko.subscriptions.history import History
from oko.subscriptions.telegram import TelegramAccess
from oko.subscriptions.details import FeedDetails
from oko.subscriptions.subscriptions import AddFeed, Overrides, FeedPatch, key, identify, FolderConflict, DEFAULT_FEED_REFRESH_MINUTES, DEFAULTS

class SubscriptionRepository:
    def __init__(self, db: Connection, fault: Callable[[str], None] = lambda _: None, *, wall_clock: Callable[[], float] = time.time) -> None:
        self._feed_batch: dict[str,dict[str,Any]] | None=None
        self.db = db
        self.history = History(db)

        self.settings = SettingsCache(db)
        self.details = FeedDetails(db, wall_clock)
        self.telegram = TelegramAccess(db, lambda feed: self.details.record(feed, 'modified'))


    def source_revision(self) -> int:
        row = self.db.execute("SELECT value FROM state WHERE key = 'sources-version'").fetchone()
        return int(row[0]) if row else 0

    def ensure_demo(self) -> None:
        with self.db:
            self.db.execute("INSERT INTO folders VALUES ( 'demo' , 'Тестовый источник' , - 1 ) ON CONFLICT DO NOTHING")
            inserted = self.db.execute("INSERT INTO feeds ( id , identity , title , kind , folder_id , original_urls , connection , transport ) VALUES ( 'demo' , 'demo' , 'Тестовый источник' , 'demo' , 'demo' , '[]' , 'connected' , 'demo' ) ON CONFLICT DO NOTHING")
            if inserted.rowcount:
                self.settings.refresh_feed('demo')
                self.details.created('demo')

    @reading_value
    def folders(self) -> list[dict[str, Any]]:
        folders = [dict(row) for row in self.db.execute('SELECT * FROM folders ORDER BY position , id')]
        def order(folder: dict[str, Any]) -> tuple[int, int]:
            prefix = re.match(r'^(\d+)\s', folder['title'])
            return (0, int(prefix[1])) if prefix else (1, folder['position'])
        return sorted(folders, key=order)

    def ensure_folder(self, title: str) -> str:
        """Resolve a creation label; all stored relationships use the opaque ID.

        The caller owns the transaction enclosing folder and feed creation.
        Never derive identity from a mutable title or reuse a deleted ID.
        """
        self.db.execute('INSERT INTO folders VALUES ( %s , %s , %s ) ON CONFLICT DO NOTHING', (secrets.token_hex(16), title, len(self.folders())))
        return str(expect_row(self.db.execute('SELECT id FROM folders WHERE title = %s', (title,)).fetchone())[0])

    def rename_folder(self, identity: str, title: str) -> dict[str, Any]:
        with self.db:
            begin_write(self.db)
            self.check_owner('folder', identity)
            try:
                self.db.execute('UPDATE folders SET title = %s WHERE id = %s', (title.strip(), identity))
            except INTEGRITY_ERRORS:
                raise FolderConflict('folder_name_conflict', 'A folder with this name already exists.') from None
        return next(folder for folder in self.folders() if folder['id'] == identity)

    def remove_folder(self, identity: str) -> None:
        with self.db:
            begin_write(self.db)
            self.check_owner('folder', identity)
            if self.db.execute('SELECT 1 FROM feeds WHERE folder_id = %s LIMIT 1', (identity,)).fetchone():
                raise FolderConflict('folder_not_empty', 'Move or delete all feeds before deleting this folder.')
            self.clear_settings('folder', identity)
            self.db.execute('DELETE FROM folders WHERE id = %s', (identity,))

    @reading_value
    def feeds(self, identity: str | None = None, *, folder: str | None = None, transport: str | None = None) -> list[dict[str, Any]]:
        conditions, parameters = [], []
        if identity is not None:
            conditions.append('id = %s')
            parameters.append(identity)
        if folder is not None:
            conditions.append('folder_id = %s')
            parameters.append(folder)
        if transport is not None:
            conditions.append('transport = %s')
            parameters.append(transport)
        query = 'SELECT * FROM feeds' + (' WHERE ' + ' AND '.join(conditions) if conditions else '') + ' ORDER BY rowid'
        return self._feeds(list(self.db.execute(query,parameters)))

    def feeds_many(self, identities: set[str]) -> list[dict[str, Any]]:
        rows: list[Row] = []
        ids = sorted(identities)
        for start in range(0, len(ids), 400):
            batch = ids[start:start+400]
            rows.extend(self.db.execute(f"SELECT * FROM feeds WHERE id IN ( {','.join(('%s' for _ in batch))} ) ORDER BY rowid", batch))
        return self._feeds(rows)

    def _feeds(self, rows: list[Row]) -> list[dict[str, Any]]:
        return [feed for start in range(0, len(rows), 400)
                for feed in self._feed_values(rows[start:start+400])]

    def _feed_values(self, rows: list[Row]) -> list[dict[str, Any]]:
        identities=[row['id'] for row in rows]
        if not identities:
            return []
        bindings=self.telegram.bindings(identities)
        profiles=self.telegram.profiles() if any(b['kind']=='client' for b in bindings.values()) else {}
        histories=self.history.statuses(identities)
        avatars={row['feed']:row['digest'] for row in self.db.execute('SELECT feed , digest FROM telegram_avatars WHERE feed IN ( SELECT value FROM oko_json_each ( %s ) )',(json.dumps(identities),))}
        batch={}
        for row in rows:
            binding=bindings.get(row['id'])
            profile=profiles.get(binding['role']) if binding and binding['kind']=='client' else None
            batch[row['id']]=self._feed_value(row,binding,histories.get(row['id']),avatars.get(row['id']),profile)
        previous=self._feed_batch
        self._feed_batch=batch
        try:
            # Prime the existing request-local @reading_value cache. No values
            # remain on Subscriptions after this synchronous assembly.
            return [self._feed(identity) for identity in identities]
        finally:
            self._feed_batch=previous

    @reading_value
    def _feed(self, identity: str) -> dict[str, Any]:
        if self._feed_batch is not None and identity in self._feed_batch:
            return self._feed_batch[identity]
        row=self.db.execute('SELECT * FROM feeds WHERE id = %s',(identity,)).fetchone()
        if row is None:
            raise LookupError('Фид не найден')
        binding=self.telegram.binding(identity)
        avatar=self.db.execute('SELECT digest FROM telegram_avatars WHERE feed = %s',(identity,)).fetchone() if row['kind']=='telegram' else None
        profile=self.telegram.profile(binding['role']) if binding and binding['kind']=='client' else None
        return self._feed_value(row,binding,self.history.status(identity),avatar[0] if avatar else None,profile)

    @staticmethod
    def _feed_value(row: Row, binding: dict[str,Any] | None, history: dict[str,Any] | None,
                    avatar: str | None, profile: dict[str,Any] | None) -> dict[str,Any]:
        feed = feed_facts(row)
        if avatar:
            feed['avatar_url'] = '/api/v3/feeds/'+feed['id']+'/avatar/'+avatar
        if binding and binding['kind'] == 'client' and feed['connection'] != 'deleting':
            if not profile or profile['state'] != 'ready':
                feed['connection'] = 'unavailable'
        feed['initial_history'] = history
        return feed

    def add_feed(self, body: AddFeed) -> dict[str, Any]:
        identity, kind, connection = identify(body.url)
        if kind not in ('rss', 'youtube'):
            raise ValueError('Для Telegram используйте подключение Telegram-источника')
        old = self.existing_feed(body.url)
        if old:
            return old
        title = body.title or urlsplit(body.url).hostname or 'RSS'
        identity_id = self.new_feed_id()
        with self.db:
            begin_write(self.db)
            folder = self.ensure_folder(body.folder)
            self.db.execute('INSERT INTO feeds ( id , identity , title , kind , folder_id , original_urls , connection , transport , public_ai ) VALUES ( %s , %s , %s , %s , %s , %s , %s , %s , %s )',
                            (identity_id, identity, title, kind, folder, json.dumps([body.url]), connection, kind, kind == 'youtube'))
            self.settings.refresh_feed(identity_id)
            self.details.created(identity_id)
            self.history.configure(identity_id, body.initial_history)
        return next(feed for feed in self.feeds() if feed['identity'] == identity)

    def new_feed_id(self) -> str:
        return str(uuid4())

    def existing_feed(self, url: str) -> dict[str, Any] | None:
        identity, _, _ = identify(url)
        return next((feed for feed in self.feeds() if feed['identity'] == identity), None)

    def check_owner(self, scope: str, owner: str) -> None:
        if scope == 'oko' and owner == 'global':
            return
        table = {'folder': 'folders', 'feed': 'feeds'}.get(scope)
        if not table or not self.db.execute(f'SELECT id FROM {table} WHERE id = %s', (owner,)).fetchone():
            raise LookupError('Настройка не найдена')

    def feed_refresh_minutes(self) -> int:
        return int(self._settings('oko', 'global').get('feed_refresh_minutes', DEFAULT_FEED_REFRESH_MINUTES))

    def telegram_http_settings(self) -> dict[str, Any]:
        values = self._settings('oko', 'global')
        return dict(value=float(values.get('telegram_http_rps', 2.)),
                    origin=dict(scope='oko', owner='global') if 'telegram_http_rps' in values else dict(scope='default'))

    def retry_settings(self) -> dict[str, int]:
        values = self._settings('oko', 'global')
        return dict(initial_seconds=int(values.get('retry_initial_seconds', 60)),
                    max_seconds=int(values.get('retry_max_seconds', 3600)))

    def validate_retry_settings(self, patch: dict[str, Any]) -> None:
        effective = self.retry_settings()
        defaults = dict(initial_seconds=60, max_seconds=3600)
        for name in ('initial_seconds','max_seconds'):
            if 'retry_'+name in patch:
                value = patch['retry_'+name]
                effective[name] = defaults[name] if value is None else value
        if effective['initial_seconds'] > effective['max_seconds']:
            raise ValueError('Максимальная задержка должна быть не меньше начальной')

    @reading_value
    def _settings(self, scope: str, owner: str) -> dict[str, Any]:
        return self.settings.values(scope, owner)

    @reading_value
    def effective(self, scope: str, owner: str, *, details: bool = True) -> dict[str, Any]:
        if details or scope != 'feed':
            self.check_owner(scope, owner)
        feed = (self._feed(owner) if details else self.settings.feed_values(owner)) if scope == 'feed' else None
        return self._effective_value(scope,owner,feed)

    def effective_feeds(self) -> dict[str,dict[str,Any]]:
        return {feed['id']:self._effective_value('feed',feed['id'],feed) for feed in self.feeds()}

    def _effective_value(self, scope: str, owner: str, feed: dict[str,Any] | Row | None) -> dict[str,Any]:
        chain = [('oko', 'global')]
        if scope == 'feed':
            assert feed is not None
            folder = feed['folder_id']
            chain.append(('folder', folder))
        if scope != 'oko':
            chain.append((scope, owner))
        defaults = {**DEFAULTS, 'feed_refresh_minutes': DEFAULT_FEED_REFRESH_MINUTES} if scope == 'oko' else DEFAULTS
        values = {field: dict(value=value, scope='default', owner=None) for field, value in defaults.items()}
        if feed is not None and feed['kind'] == 'youtube':
            values['theses']['value'] = True
        overrides = {}
        for level, identity in chain:
            for field, value in self._settings(level,identity).items():
                if field not in defaults:
                    continue
                values[field] = dict(value=value, scope=level, owner=identity)
                if (level, identity) == (scope, owner):
                    overrides[field] = value
        result: dict[str,Any] = dict(effective=values, overrides=overrides)
        if scope == 'feed':
            assert feed is not None
            result['feed'] = feed
        return result

    def update_settings(self, scope: str, owner: str, patch: Overrides) -> dict[str, Any]:
        self.check_owner(scope, owner)
        self.validate_retry_settings(patch.model_dump(exclude_unset=True))
        with self.db:
            before = self.settings.values(scope, owner)
            self.settings.patch(scope, owner, patch.model_dump(exclude_unset=True))
            after = self.settings.values(scope, owner)
            if scope == 'feed' and before != after:
                self.details.record(owner, 'modified')
        return self.effective(scope, owner)

    def clear_settings(self, scope: str, owner: str) -> None:
        self.settings.clear(scope, owner)

    def youtube_theses(self, feed_id: str) -> bool:
        settings = self.effective('feed', feed_id, details=False)
        return bool(settings['feed']['kind'] == 'youtube' and (settings['feed']['importance'] == 'high' or settings['effective']['theses']['value']))

    def update_feed(self, feed_id: str, patch: FeedPatch) -> dict[str, Any]:
        self.check_owner('feed', feed_id)
        feed = next(f for f in self.feeds() if f['id'] == feed_id)
        values = patch.model_dump(exclude_unset=True)
        if any(value is None for field, value in values.items() if field not in ('auto_read', 'transport')):
            raise ValueError('Поле не может быть пустым')
        folder_title = values.pop('new_folder', None)
        if folder_title is not None:
            folder_title = folder_title.strip()
            if 'folder_id' in values:
                raise ValueError('Choose an existing folder or create a new one, not both.')
            if feed['connection'] == 'deleting':
                raise ValueError('The feed is being deleted.')
        if 'folder_id' in values:
            self.check_owner('folder', values['folder_id'])
        if any(field in values for field in ('read_history', 'auto_read')) and feed['kind'] != 'telegram':
            raise ValueError('Отметки чтения применимы только к Telegram')
        binding = self.telegram.binding(feed_id)
        if feed['kind'] == 'telegram' and 'transport' in values:
            raise ValueError('Способ доступа неизменяем; удалите фид и добавьте заново')
        if binding and binding['kind'] == 'web' and set(values).intersection(('auto_read', 'read_history')):
            raise ValueError('Веб-доступ не поддерживает отметки Telegram')
        transport = values.get('transport')
        if transport == 'telegram' and feed['kind'] != 'telegram':
            raise ValueError('Telegram-источник не распознан')
        if transport == 'youtube' and feed['kind'] != 'youtube':
            raise ValueError('YouTube-источник не распознан')
        if transport == 'rss' and feed['kind'] in ('youtube', 'demo'):
            raise ValueError('Для этого источника нет RSS-подключения')
        with self.db:
            if folder_title is not None:
                begin_write(self.db)
                if self.db.execute('SELECT 1 FROM folders WHERE title = %s', (folder_title,)).fetchone():
                    raise ValueError('A folder with this name already exists.')
                values['folder_id'] = self.ensure_folder(folder_title)
            changed = any(feed.get(field) != value for field, value in values.items())
            for field, value in values.items():
                self.db.execute(f'UPDATE feeds SET {field} = %s WHERE id = %s', (value, feed_id))
            if {'folder_id', 'importance', 'public_ai', 'transport'}.intersection(values):
                self.settings.refresh_feed(feed_id)
            if 'transport' in values and values['transport'] != feed['transport']:
                connection = 'ambiguous' if feed['kind'] == 'unresolved' and transport is None else 'needs_connection'
                self.db.execute('UPDATE feeds SET connection = %s WHERE id = %s', (connection, feed_id))
            if changed:
                self.details.record(feed_id, 'modified')
        return next(f for f in self.feeds() if f['id'] == feed_id)
