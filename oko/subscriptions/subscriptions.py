from oko.storage.reading import reading_value
"""Persistent subscription identity and field-by-field effective settings."""
import hashlib
import json
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit, parse_qs

from oko.subscriptions.history import History, InitialHistory
from oko.subscriptions.telegram import TelegramAccess
from oko.collection.telegram.contracts import HistoryEnvelope
from oko.collection.contracts import SourceError

from pydantic import BaseModel, ConfigDict, Field, field_validator
from oko.subscriptions.details import FeedDetails


class FeedURL(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    url: str = Field(min_length=1, max_length=4000)


class AddFeed(FeedURL):
    initial_history: InitialHistory = Field(default_factory=InitialHistory)
    verification: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    folder: str = Field(default='Без папки', min_length=1, max_length=200)


class Overrides(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    feed_refresh_minutes: int | None = Field(default=None, ge=1, le=1440)
    originals: bool | None = None
    theses: bool | None = None
    prompt_theses: str | None = Field(default=None, max_length=20000)
    prompt_filters: str | None = Field(default=None, max_length=20000)
    processor: str | None = Field(default=None, min_length=1, max_length=200)


class FeedPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator('title', mode='before')
    @classmethod
    def trim_title(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    model_config = ConfigDict(extra='forbid', strict=True)
    folder_id: str | None = None
    new_folder: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"\S")
    importance: Literal['normal', 'elevated', 'high'] | None = None
    initial_days: int | None = Field(default=None, ge=1, le=3650)
    public_ai: bool | None = None
    read_history: bool | None = None
    auto_read: bool | None = None
    transport: Literal['telegram', 'rss', 'youtube'] | None = None


DEFAULT_FEED_REFRESH_MINUTES = 15


DEFAULTS: dict[str, Any] = dict(
    originals=True, theses=False, prompt_theses='Выдели основные тезисы публикации.',
    prompt_filters='', processor='codex')


def key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def identify(url: str) -> tuple[str, str, str]:
    """Only exact recognisable routes merge; ambiguous routes remain unconnected."""
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Нужен HTTP(S)-адрес без учётных данных')
    host = parsed.hostname.lower()
    route = re.fullmatch(r'/telegram/channel/([A-Za-z][A-Za-z0-9_]{3,31})(?:/([^/]+))?/?', parsed.path)
    if route and route[2]:
        display_option = r'show(?:LinkPreview|ViaBot|ReplyTo|FwdFrom(?:Author)?|InlineButtons|MediaTag(?:InTitle|AsEmoji)|HashtagAsHyperlink)=(?:0|1|true|false)'
        if not re.fullmatch(display_option + r'(?:&' + display_option + r')*', route[2]):
            route = None
    direct = re.fullmatch(r'/([A-Za-z][A-Za-z0-9_]{3,31})/?', parsed.path) if host in ('t.me', 'telegram.me') else None
    if (route or direct) and not parsed.query:
        match = route or direct
        assert match is not None
        return 'telegram:' + match[1].lower(), 'telegram', 'needs_connection'
    if parsed.path.startswith('/telegram/') or host in ('t.me', 'telegram.me'):
        return 'unresolved:' + url, 'unresolved', 'ambiguous'
    channel = parse_qs(parsed.query).get('channel_id', [])
    if host in ('www.youtube.com', 'youtube.com') and parsed.path == '/feeds/videos.xml' and len(channel) == 1:
        return 'youtube:' + channel[0], 'youtube', 'needs_connection'
    canonical = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ''))
    return 'rss:' + canonical, 'rss', 'needs_connection'


class FolderConflict(ValueError):
    def __init__(self, code: Literal['folder_not_empty', 'folder_name_conflict'], message: str) -> None:
        self.code = code
        super().__init__(message)


class Subscriptions:
    def __init__(self, db: sqlite3.Connection, fault: Callable[[str], None] = lambda _: None, *, wall_clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.history = History(db)
        db.executescript('''
        CREATE TABLE IF NOT EXISTS folders (id TEXT PRIMARY KEY, title TEXT UNIQUE, position INTEGER);
        CREATE TABLE IF NOT EXISTS feeds (
          id TEXT PRIMARY KEY, identity TEXT UNIQUE, title TEXT, kind TEXT, folder_id TEXT,
          original_urls TEXT, connection TEXT, transport TEXT,
          importance TEXT DEFAULT 'normal', initial_days INTEGER DEFAULT 1,
          public_ai INTEGER DEFAULT 0, read_history INTEGER DEFAULT 0, auto_read INTEGER);
        CREATE TABLE IF NOT EXISTS settings (scope TEXT, owner TEXT, field TEXT, value TEXT,
          PRIMARY KEY(scope,owner,field));
        ''')

        with db:
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(feeds)')}
            for column, sql_type in [('telegram_peer', 'INTEGER'), ('telegram_account', 'TEXT'), ('telegram_username', 'TEXT'), ('telegram_topic', 'INTEGER')]:
                if column not in columns:
                    db.execute(f'ALTER TABLE feeds ADD COLUMN {column} {sql_type}')
            if 'telegram_username' not in columns:
                # Infer only while upgrading legacy storage. A new connection's
                # explicit absence of a public username must remain authoritative.
                for row in db.execute("SELECT * FROM feeds WHERE kind='telegram' AND telegram_peer IS NOT NULL").fetchall():
                    candidates = ([row['identity'].removeprefix('telegram:')]
                                  if row['identity'].startswith('telegram:') else []) + json.loads(row['original_urls'])
                    username = next((name.lstrip('@') for name in candidates
                                     if re.fullmatch(r'@?[a-zA-Z][a-zA-Z0-9_]{3,31}', name)), None)
                    db.execute('UPDATE feeds SET telegram_username=? WHERE id=?', (username, row['id']))
                fault('before_telegram_username_commit')

        self.details = FeedDetails(db, wall_clock)
        self.telegram = TelegramAccess(db, lambda feed: self.details.record(feed, 'modified'))

    def ensure_demo(self) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO folders VALUES ('demo','Тестовый источник',-1)")
            inserted = self.db.execute("INSERT OR IGNORE INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport) VALUES ('demo','demo','Тестовый источник','demo','demo','[]','connected','demo')")
            if inserted.rowcount:
                self.details.created('demo')

    @reading_value
    def folders(self) -> list[dict[str, Any]]:
        folders = [dict(row) for row in self.db.execute('SELECT * FROM folders ORDER BY position,id')]
        def order(folder: dict[str, Any]) -> tuple[int, int]:
            prefix = re.match(r'^(\d+)\s', folder['title'])
            return (0, int(prefix[1])) if prefix else (1, folder['position'])
        return sorted(folders, key=order)

    def ensure_folder(self, title: str) -> str:
        """Resolve a creation label; all stored relationships use the opaque ID.

        The caller owns the transaction enclosing folder and feed creation.
        Never derive identity from a mutable title or reuse a deleted ID.
        """
        self.db.execute('INSERT OR IGNORE INTO folders VALUES (?,?,?)', (secrets.token_hex(16), title, len(self.folders())))
        return str(self.db.execute('SELECT id FROM folders WHERE title=?', (title,)).fetchone()[0])

    def rename_folder(self, identity: str, title: str) -> dict[str, Any]:
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.check_owner('folder', identity)
            try:
                self.db.execute('UPDATE folders SET title=? WHERE id=?', (title.strip(), identity))
            except sqlite3.IntegrityError:
                raise FolderConflict('folder_name_conflict', 'A folder with this name already exists.') from None
        return next(folder for folder in self.folders() if folder['id'] == identity)

    def remove_folder(self, identity: str) -> None:
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.check_owner('folder', identity)
            if self.db.execute('SELECT 1 FROM feeds WHERE folder_id=? LIMIT 1', (identity,)).fetchone():
                raise FolderConflict('folder_not_empty', 'Move or delete all feeds before deleting this folder.')
            self.db.execute("DELETE FROM settings WHERE scope='folder' AND owner=?", (identity,))
            self.db.execute('DELETE FROM folders WHERE id=?', (identity,))

    @reading_value
    def feeds(self, identity: str | None = None, *, folder: str | None = None) -> list[dict[str, Any]]:
        conditions, parameters = [], []
        if identity is not None:
            conditions.append('id=?')
            parameters.append(identity)
        if folder is not None:
            conditions.append('folder_id=?')
            parameters.append(folder)
        query = 'SELECT id FROM feeds' + (' WHERE ' + ' AND '.join(conditions) if conditions else '') + ' ORDER BY rowid'
        return [self._feed(row['id']) for row in self.db.execute(query, parameters)]

    @reading_value
    def _feed(self, identity: str) -> dict[str, Any]:
        row = self.db.execute('SELECT * FROM feeds WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise LookupError('Фид не найден')
        feed = dict(row)
        feed.pop('telegram_topic', None)
        avatar = self.db.execute('SELECT digest FROM telegram_avatars WHERE feed=?', (feed['id'],)).fetchone() if feed['kind'] == 'telegram' else None
        if avatar:
            feed['avatar_url'] = '/api/v3/feeds/'+feed['id']+'/avatar/'+avatar[0]
        feed['original_urls'] = json.loads(feed['original_urls'])
        binding = self.telegram.binding(feed['id'])
        if binding and binding['kind'] == 'client' and feed['connection'] != 'deleting':
            profile = self.telegram.profile(binding['role'])
            if not profile or profile['state'] != 'ready':
                feed['connection'] = 'unavailable'
        feed['initial_history'] = self.history.status(feed['id'])
        if feed['initial_history'] is None:
            namespace = 'telegram:' if feed['transport'] == 'telegram' else ''
            saved = self.db.execute('SELECT value FROM state WHERE key=?', ('cursor:' + namespace + feed['id'],)).fetchone()
            try:
                cursor = json.loads(saved['value']) if saved else {}
            except json.JSONDecodeError:
                cursor = {}  # Adapters may own opaque, non-JSON cursors.
            if feed['transport']=='telegram' and saved:
                try:
                    feed['legacy_since'] = HistoryEnvelope.decode(saved['value'],adapter='client').selection.since
                except SourceError:
                    feed['legacy_since'] = None
            else:
                feed['legacy_since'] = cursor.get('since') if isinstance(cursor,dict) else None
        for field in ('public_ai', 'read_history', 'auto_read'):
            feed[field] = None if feed[field] is None else bool(feed[field])
        return feed

    def add_feed(self, body: AddFeed) -> dict[str, Any]:
        identity, kind, connection = identify(body.url)
        if kind not in ('rss', 'youtube'):
            raise ValueError('Для Telegram используйте подключение Telegram-источника')
        old = self.existing_feed(body.url)
        if old:
            return old
        title = body.title or urlsplit(body.url).hostname or 'RSS'
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            folder = self.ensure_folder(body.folder)
            self.db.execute('''INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport,public_ai)
                            VALUES (?,?,?,?,?,?,?,?,?)''',
                            (key(identity), identity, title, kind, folder, json.dumps([body.url]), connection, kind, kind == 'youtube'))
            self.details.created(key(identity))
            self.history.configure(key(identity), body.initial_history)
        return next(feed for feed in self.feeds() if feed['identity'] == identity)

    def existing_feed(self, url: str) -> dict[str, Any] | None:
        identity, _, _ = identify(url)
        return next((feed for feed in self.feeds() if feed['identity'] == identity), None)

    def check_owner(self, scope: str, owner: str) -> None:
        if scope == 'oko' and owner == 'global':
            return
        table = {'folder': 'folders', 'feed': 'feeds'}.get(scope)
        if not table or not self.db.execute(f'SELECT id FROM {table} WHERE id=?', (owner,)).fetchone():
            raise LookupError('Настройка не найдена')

    def feed_refresh_minutes(self) -> int:
        row = self.db.execute("SELECT value FROM settings WHERE scope='oko' AND owner='global' AND field='feed_refresh_minutes'").fetchone()
        return int(json.loads(row['value'])) if row else DEFAULT_FEED_REFRESH_MINUTES

    @reading_value
    def _settings(self, scope: str, owner: str) -> list[sqlite3.Row]:
        return self.db.execute('SELECT field,value FROM settings WHERE scope=? AND owner=?', (scope, owner)).fetchall()

    @reading_value
    def effective(self, scope: str, owner: str) -> dict[str, Any]:
        self.check_owner(scope, owner)
        feed = self._feed(owner) if scope == 'feed' else None
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
            for row in self._settings(level, identity):
                if row['field'] not in defaults:
                    continue
                value = json.loads(row['value'])
                values[row['field']] = dict(value=value, scope=level, owner=identity)
                if (level, identity) == (scope, owner):
                    overrides[row['field']] = value
        result = dict(effective=values, overrides=overrides)
        if scope == 'feed':
            assert feed is not None
            result['feed'] = feed
        return result

    def update_settings(self, scope: str, owner: str, patch: Overrides) -> dict[str, Any]:
        self.check_owner(scope, owner)
        with self.db:
            before = list(self.db.execute('SELECT field,value FROM settings WHERE scope=? AND owner=? ORDER BY field', (scope, owner)))
            for field, value in patch.model_dump(exclude_unset=True).items():
                if value is None:
                    self.db.execute('DELETE FROM settings WHERE scope=? AND owner=? AND field=?', (scope, owner, field))
                else:
                    self.db.execute('INSERT INTO settings VALUES (?,?,?,?) ON CONFLICT(scope,owner,field) DO UPDATE SET value=excluded.value',
                                    (scope, owner, field, json.dumps(value)))
            after = list(self.db.execute('SELECT field,value FROM settings WHERE scope=? AND owner=? ORDER BY field', (scope, owner)))
            if scope == 'feed' and before != after:
                self.details.record(owner, 'modified')
        return self.effective(scope, owner)

    def youtube_theses(self, feed_id: str) -> bool:
        settings = self.effective('feed', feed_id)
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
                self.db.execute('BEGIN IMMEDIATE')
                if self.db.execute('SELECT 1 FROM folders WHERE title=?', (folder_title,)).fetchone():
                    raise ValueError('A folder with this name already exists.')
                values['folder_id'] = self.ensure_folder(folder_title)
            changed = any(feed.get(field) != value for field, value in values.items())
            for field, value in values.items():
                self.db.execute(f'UPDATE feeds SET {field}=? WHERE id=?', (value, feed_id))
            if 'transport' in values and values['transport'] != feed['transport']:
                connection = 'ambiguous' if feed['kind'] == 'unresolved' and transport is None else 'needs_connection'
                self.db.execute('UPDATE feeds SET connection=? WHERE id=?', (connection, feed_id))
            if changed:
                self.details.record(feed_id, 'modified')
        return next(f for f in self.feeds() if f['id'] == feed_id)
