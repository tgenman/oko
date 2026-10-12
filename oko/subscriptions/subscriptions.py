"""Persistent subscription identity and field-by-field effective settings."""
import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit, parse_qs
import xml.etree.ElementTree as ET

from oko.collection.telegram.source import ConnectTelegram
from oko.subscriptions.history import History, InitialHistory

from pydantic import BaseModel, ConfigDict, Field


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    opml: str = Field(max_length=2_000_000)
    choices: dict[str, str] = Field(default_factory=dict)


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
    events: bool | None = None
    event_hours: int | None = Field(default=None, ge=1, le=87600)
    originals: bool | None = None
    theses: bool | None = None
    digests: bool | None = None
    prompt_theses: str | None = Field(default=None, max_length=20000)
    prompt_digest: str | None = Field(default=None, max_length=20000)
    prompt_filters: str | None = Field(default=None, max_length=20000)
    schedule: str | None = Field(default=None, pattern=r'^(off|([01][0-9]|2[0-3]):[0-5][0-9])$')
    digest_days: int | None = Field(default=None, ge=1, le=3650)
    processor: str | None = Field(default=None, min_length=1, max_length=200)


class FeedPatch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    folder_id: str | None = None
    importance: Literal['normal', 'elevated', 'high'] | None = None
    initial_days: int | None = Field(default=None, ge=1, le=3650)
    public_ai: bool | None = None
    read_history: bool | None = None
    auto_read: bool | None = None
    transport: Literal['telegram', 'rss', 'youtube'] | None = None


DEFAULTS: dict[str, Any] = dict(
    events=True, event_hours=24, originals=True, theses=False, digests=False, prompt_theses='Выдели основные тезисы публикации.',
    prompt_digest='Собери темы и основные тезисы за период.', prompt_filters='',
    schedule='off', digest_days=1, processor='codex')


def key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def folder_path(parts: list[str]) -> str:
    return ' / '.join(part.replace('\\', '\\\\').replace('/', '\\/') for part in parts)


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


def preview(opml: str) -> dict[str, Any]:
    # ElementTree does not fetch external entities. Reject declarations also to
    # avoid entity expansion and make the accepted format unambiguous.
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', opml, re.I):
        raise ValueError('OPML с объявлениями сущностей не поддерживается')
    try:
        root = ET.fromstring(opml)
    except ET.ParseError:
        raise ValueError('Некорректный OPML') from None
    body = root.find('body')
    if root.tag != 'opml' or body is None:
        raise ValueError('Нужен документ OPML с body')
    feeds: dict[str, dict[str, Any]] = {}
    folders: list[str] = []

    def walk(parent: ET.Element, path: list[str], depth: int) -> None:
        if depth > 30:
            raise ValueError('Слишком много вложенных папок')
        for outline in parent.findall('outline'):
            title = outline.get('text') or outline.get('title') or 'Без названия'
            url = outline.get('xmlUrl')
            if url:
                identity, kind, connection = identify(url)
                folder = folder_path(path) or 'Без папки'
                if folder not in folders:
                    folders.append(folder)
                feed = feeds.setdefault(identity, dict(identity=identity, title=title, kind=kind,
                                                       connection=connection, original_urls=[], folders=[]))
                if url not in feed['original_urls']:
                    feed['original_urls'].append(url)
                if folder not in feed['folders']:
                    feed['folders'].append(folder)
            else:
                folder = folder_path([*path, title])
                if folder not in folders:
                    folders.append(folder)
                walk(outline, [*path, title], depth + 1)

    walk(body, [], 0)
    return dict(folders=folders, feeds=list(feeds.values()))


class Subscriptions:
    def __init__(self, db: sqlite3.Connection, fault: Callable[[str], None] = lambda _: None) -> None:
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

    def connect_telegram(self, body: ConnectTelegram, info: dict[str, Any]) -> dict[str, Any]:
        identity = f"telegram-peer:{body.account}:{info['peer']}" + (f":topic:{body.topic_id}" if body.topic_id else "")
        username_identity = 'telegram:' + str(info.get('username') or '').lower()
        old = next((f for f in self.feeds() if f['identity'] == identity or (f.get('telegram_peer') == info['peer'] and f.get('telegram_account') == body.account and f.get('telegram_topic') == body.topic_id) or (body.topic_id is None and f['identity'] == username_identity and not f.get('telegram_peer'))), None)
        if body.feed_id:
            self.check_owner('feed', body.feed_id)
            selected = next(f for f in self.feeds() if f['id'] == body.feed_id)
            if selected['kind'] != 'telegram':
                raise ValueError('Выберите Telegram-фид')
            if old and old['id'] != selected['id']:
                raise ValueError('Источник уже подключён в другом фиде')
            if selected.get('telegram_peer') and (selected['telegram_peer'] != info['peer'] or selected['telegram_account'] != body.account or selected.get('telegram_topic') != body.topic_id):
                raise ValueError('Для другого источника создайте новый фид')
            old = selected
        elif old and next(f for f in self.folders() if f['id'] == old['folder_id'])['title'] != body.folder:
            raise ValueError('Источник уже в другой папке; переместите существующий фид')
        id = old['id'] if old else key(identity)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO folders VALUES (?,?,?)', (key('folder:' + body.folder), body.folder, len(self.folders())))
            folder = self.db.execute('SELECT id FROM folders WHERE title=?', (body.folder,)).fetchone()[0]
            if not old:
                self.db.execute('''INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls)
                                VALUES (?,?,?,'telegram',?,?)''', (id, identity, info['title'], folder, json.dumps([str(body.peer)])))
            self.db.execute("UPDATE feeds SET transport='telegram',connection='connected',telegram_peer=?,telegram_account=?,telegram_username=?,telegram_topic=?,auto_read=? WHERE id=?", (info['peer'], body.account, info.get('username'), body.topic_id, body.auto_read, id))
        return next(f for f in self.feeds() if f['id'] == id)

    def ensure_demo(self) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO folders VALUES ('demo','Тестовый источник',-1)")
            self.db.execute("INSERT OR IGNORE INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport) VALUES ('demo','demo','Тестовый источник','demo','demo','[]','connected','demo')")

    def folders(self) -> list[dict[str, Any]]:
        folders = [dict(row) for row in self.db.execute('SELECT * FROM folders ORDER BY position,id')]
        def order(folder: dict[str, Any]) -> tuple[int, int]:
            prefix = re.match(r'^(\d+)\s', folder['title'])
            return (0, int(prefix[1])) if prefix else (1, folder['position'])
        return sorted(folders, key=order)

    def feeds(self) -> list[dict[str, Any]]:
        result = []
        for row in self.db.execute('SELECT * FROM feeds ORDER BY rowid'):
            feed = dict(row)
            feed['original_urls'] = json.loads(feed['original_urls'])
            feed['initial_history'] = self.history.status(feed['id'])
            for field in ('public_ai', 'read_history', 'auto_read'):
                feed[field] = None if feed[field] is None else bool(feed[field])
            result.append(feed)
        return result

    def import_opml(self, request: ImportRequest) -> dict[str, Any]:
        plan = preview(request.opml)
        existing = {f['identity']: f for f in self.feeds()}
        folder_titles = {f['id']: f['title'] for f in self.folders()}
        selected: dict[str, str] = {}
        conflicts = []
        for feed in plan['feeds']:
            identity = feed['identity']
            old = existing.get(identity)
            options = list(feed['folders'])
            if old and folder_titles[old['folder_id']] not in options:
                options.append(folder_titles[old['folder_id']])
            choice = request.choices.get(identity)
            if len(options) > 1 and choice not in options:
                conflicts.append(dict(identity=identity, folders=options))
            elif choice is not None and choice not in options:
                raise ValueError('Выбранная папка отсутствует в импорте')
            else:
                selected[identity] = choice or options[0]
        if conflicts:
            return dict(conflicts=conflicts)
        with self.db:
            for title in plan['folders']:
                self.db.execute('INSERT OR IGNORE INTO folders VALUES (?,?,?)',
                                (key('folder:' + title), title, len(self.folders())))
            for feed in plan['feeds']:
                identity = feed['identity']
                old = existing.get(identity)
                urls = list(dict.fromkeys((old['original_urls'] if old else []) + feed['original_urls']))
                folder = self.db.execute('SELECT id FROM folders WHERE title=?', (selected[identity],)).fetchone()[0]
                self.db.execute('''INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(identity) DO UPDATE SET
                folder_id=excluded.folder_id,original_urls=excluded.original_urls''',
                                (key(identity), identity, feed['title'], feed['kind'], folder,
                                 json.dumps(urls), feed['connection'], feed['kind'] if feed['kind'] in ('rss', 'youtube') else None))
        return dict(folders=self.folders(), feeds=self.feeds())

    def add_feed(self, body: AddFeed) -> dict[str, Any]:
        identity, kind, connection = identify(body.url)
        if kind not in ('rss', 'youtube'):
            raise ValueError('Для Telegram используйте подключение Telegram-источника')
        old = self.existing_feed(body.url)
        if old:
            return old
        title = body.title or urlsplit(body.url).hostname or 'RSS'
        with self.db:
            folder = key('folder:' + body.folder)
            self.db.execute('INSERT OR IGNORE INTO folders VALUES (?,?,?)', (folder, body.folder, len(self.folders())))
            self.db.execute('''INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport)
                            VALUES (?,?,?,?,?,?,?,?)''',
                            (key(identity), identity, title, kind, folder, json.dumps([body.url]), connection, kind))
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

    def effective(self, scope: str, owner: str) -> dict[str, Any]:
        self.check_owner(scope, owner)
        chain = [('oko', 'global')]
        if scope == 'feed':
            folder = self.db.execute('SELECT folder_id FROM feeds WHERE id=?', (owner,)).fetchone()[0]
            chain.append(('folder', folder))
        if scope != 'oko':
            chain.append((scope, owner))
        values = {field: dict(value=value, scope='default', owner=None) for field, value in DEFAULTS.items()}
        overrides = {}
        for level, identity in chain:
            for row in self.db.execute('SELECT field,value FROM settings WHERE scope=? AND owner=?', (level, identity)):
                value = json.loads(row['value'])
                values[row['field']] = dict(value=value, scope=level, owner=identity)
                if (level, identity) == (scope, owner):
                    overrides[row['field']] = value
        result = dict(effective=values, overrides=overrides)
        if scope == 'feed':
            result['feed'] = next(f for f in self.feeds() if f['id'] == owner)
        return result

    def update_settings(self, scope: str, owner: str, patch: Overrides) -> dict[str, Any]:
        self.check_owner(scope, owner)
        if scope == 'feed' and {'events', 'event_hours'} & patch.model_fields_set:
            raise ValueError('Объединение событий настраивается для Око или папки')
        with self.db:
            for field, value in patch.model_dump(exclude_unset=True).items():
                if value is None:
                    self.db.execute('DELETE FROM settings WHERE scope=? AND owner=? AND field=?', (scope, owner, field))
                else:
                    self.db.execute('INSERT INTO settings VALUES (?,?,?,?) ON CONFLICT(scope,owner,field) DO UPDATE SET value=excluded.value',
                                    (scope, owner, field, json.dumps(value)))
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
        if 'folder_id' in values:
            self.check_owner('folder', values['folder_id'])
        if any(field in values for field in ('read_history', 'auto_read')) and feed['kind'] != 'telegram':
            raise ValueError('Отметки чтения применимы только к Telegram')
        transport = values.get('transport')
        if transport == 'telegram' and feed['kind'] != 'telegram':
            raise ValueError('Telegram-источник не распознан')
        if transport == 'youtube' and feed['kind'] != 'youtube':
            raise ValueError('YouTube-источник не распознан')
        if transport == 'rss' and feed['kind'] in ('youtube', 'demo'):
            raise ValueError('Для этого источника нет RSS-подключения')
        with self.db:
            for field, value in values.items():
                self.db.execute(f'UPDATE feeds SET {field}=? WHERE id=?', (value, feed_id))
            if 'transport' in values:
                connection = 'ambiguous' if feed['kind'] == 'unresolved' and transport is None else 'needs_connection'
                self.db.execute('UPDATE feeds SET connection=? WHERE id=?', (connection, feed_id))
        return next(f for f in self.feeds() if f['id'] == feed_id)
