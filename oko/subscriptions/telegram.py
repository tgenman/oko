"""Durable Telegram bindings, verified profiles and source claim ownership.

All mutations run on the existing Subscriptions SQLite connection under Runtime
admission. No network, credentials, SDK or file deletion belongs here.
"""
from oko.storage.reading import reading_value
import json
import base64
import hashlib
import binascii
import re
import secrets
import sqlite3
from typing import Any
from collections.abc import Callable
from urllib.parse import urlsplit


class TelegramConflict(Exception):
    def __init__(self, code: str, details: dict[str, Any], message: str, status: int = 409) -> None:
        self.code, self.details, self.status = code, details, status
        super().__init__(message)


def locator(value: str) -> tuple[str, str | int]:
    value = value.strip()
    if re.fullmatch(r'-[1-9][0-9]*', value):
        return 'peer', int(value)
    if value.startswith('@'):
        value = value[1:]
    elif '://' in value or value.startswith(('t.me/', 'telegram.me/')):
        parsed = urlsplit(value if '://' in value else 'https://' + value)
        if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('t.me', 'telegram.me') or parsed.username or parsed.password or parsed.port:
            raise ValueError('Нужен адрес Telegram без учётных данных')
        parts = parsed.path.strip('/').split('/')
        if parts[0] == 's':
            parts = parts[1:]
        if len(parts) >= 2 and parts[0] == 'c' and parts[1].isdigit():
            if len(parts) not in (3, 4) or not all(v.isdigit() for v in parts[2:]):
                raise ValueError('Неверная ссылка на сообщение группы')
            return 'peer', -int('100' + parts[1])
        if not parts or len(parts) > 3 or any(not v.isdigit() for v in parts[1:]):
            raise ValueError('Нужна ссылка на источник или сообщение Telegram')
        value = parts[0]
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,31}', value):
        raise ValueError('Нужен публичный username или marked ID доступной группы')
    return 'username', value.lower()


class TelegramAccess:
    def __init__(self, db: sqlite3.Connection, modified: Callable[[str], None] = lambda _: None) -> None:
        self.db = db
        self.modified = modified
        # No executescript here: migrations and their ownership rows are atomic.
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS telegram_avatars (feed TEXT PRIMARY KEY, image BLOB NOT NULL, mime TEXT NOT NULL, digest TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS telegram_bindings (feed TEXT PRIMARY KEY, binding TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS telegram_profiles (
                role TEXT PRIMARY KEY, account TEXT, user_id INTEGER, generation INTEGER NOT NULL,
                state TEXT, checked REAL, message TEXT)''')
            db.execute('CREATE TABLE IF NOT EXISTS telegram_source_claims (kind TEXT, value TEXT, PRIMARY KEY(kind,value))')
            db.execute('''CREATE TABLE IF NOT EXISTS telegram_claim_owners (
                kind TEXT, value TEXT, feed TEXT, evidence TEXT, PRIMARY KEY(kind,value,feed))''')
            db.execute("UPDATE telegram_profiles SET state='unavailable',message='Требуется проверка сессии после запуска'")

    def bindings(self) -> dict[str, dict[str, Any]]:
        return {row['feed']: json.loads(row['binding']) for row in self.db.execute('SELECT feed,binding FROM telegram_bindings')}

    def profiles(self) -> dict[str, dict[str, Any]]:
        return {row['role']: dict(row) for row in self.db.execute('SELECT * FROM telegram_profiles')}

    @reading_value
    def binding(self, feed: str) -> dict[str, Any] | None:
        row = self.db.execute('SELECT binding FROM telegram_bindings WHERE feed=?', (feed,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_binding(self, feed: str, binding: dict[str, Any]) -> None:
        self.db.execute('INSERT INTO telegram_bindings VALUES (?,?) ON CONFLICT(feed) DO UPDATE SET binding=excluded.binding', (feed, json.dumps(binding)))

    @reading_value
    def profile(self, role: str) -> dict[str, Any] | None:
        row = self.db.execute('SELECT * FROM telegram_profiles WHERE role=?', (role,)).fetchone()
        return dict(row) if row else None

    def dependents(self, role: str) -> list[str]:
        return [r['feed'] for r in self.db.execute("SELECT feed FROM telegram_bindings WHERE json_extract(binding,'$.role')=?", (role,))]

    def save_profile(self, role: str, account: str, user_id: int, generation: int, checked: float) -> dict[str, Any]:
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old = self.profile(role)
            if (old['generation'] if old else 0) != generation:
                raise TelegramConflict('profile_unavailable', dict(retry_at=None), 'Профиль изменился; повторите проверку', 503)
            if old and old['user_id'] != user_id and self.dependents(role):
                raise TelegramConflict('profile_in_use', dict(role=role), 'Профиль используется фидами')
            if self.db.execute('SELECT 1 FROM telegram_profiles WHERE role!=? AND user_id=?', (role, user_id)).fetchone():
                raise TelegramConflict('profile_account_conflict', dict(role=role), 'Для двух ролей нужны разные аккаунты')
            self.db.execute('''INSERT INTO telegram_profiles VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(role) DO UPDATE SET account=excluded.account,user_id=excluded.user_id,
                generation=excluded.generation,state=excluded.state,checked=excluded.checked,message=NULL''',
                (role, account, user_id, generation + 1, 'ready', checked, None))
        return self.profile(role) or {}

    def checked(self, role: str, generation: int, user_id: int | None, checked: float) -> bool:
        row = self.profile(role)
        if not row or row['generation'] != generation:
            return False
        state = 'ready' if user_id == row['user_id'] else 'unavailable' if user_id is None else 'identity_mismatch'
        with self.db:
            self.db.execute('UPDATE telegram_profiles SET state=?,checked=?,message=? WHERE role=?',
                (state, checked, None if state == 'ready' else 'Личность Telegram-аккаунта не подтверждена', role))
        return state == 'ready'

    def claims(self, info: dict[str, Any]) -> list[tuple[str, str]]:
        result = [('peer', str(info['peer']))] if info.get('peer') is not None else []
        for name in [info.get('username'), *info.get('usernames', [])]:
            if name:
                result.append(('username', str(name).lstrip('@').lower()))
        return list(dict.fromkeys(result))

    def owners(self, claims: list[tuple[str, str]]) -> list[str]:
        return sorted({row[0] for kind, value in claims for row in self.db.execute('SELECT feed FROM telegram_claim_owners WHERE kind=? AND value=?', (kind, value))})

    def claim(self, feed: str, claim: tuple[str, str], evidence: str) -> None:
        self.db.execute('INSERT OR IGNORE INTO telegram_source_claims VALUES (?,?)', claim)
        self.db.execute('INSERT OR IGNORE INTO telegram_claim_owners VALUES (?,?,?,?)', (*claim, feed, evidence))

    def conflicts(self, claims: list[tuple[str, str]], peer: int | None = None) -> list[str]:
        owners = self.owners(claims)
        if peer is not None:
            for feed in owners:
                row = self.db.execute('SELECT telegram_peer FROM feeds WHERE id=?', (feed,)).fetchone()
                if row and row[0] and row[0] != peer:
                    raise TelegramConflict('source_identity_conflict', dict(feed_id=feed), 'Сохранённый username принадлежит другому peer')
        return owners

    def connect(self, binding: dict[str, Any], info: dict[str, Any], source: str, folder: str, history: Any, auto_read: bool, subscriptions: Any) -> dict[str, Any]:
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            claims = self.claims(info)
            duplicates = self.conflicts(claims, info.get('peer'))
            if duplicates:
                raise TelegramConflict('duplicate_source', self.duplicates(duplicates), 'Источник уже подключён')
            identity = subscriptions.new_feed_id()
            folder_id = subscriptions.ensure_folder(folder)
            profile = self.profile(binding['role']) if binding['kind'] == 'client' else None
            self.db.execute('''INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport,
                telegram_peer,telegram_account,telegram_username,auto_read)
                VALUES (?,?,?,'telegram',?,?,'connected',?,?,?,?,?)''',
                (identity, 'telegram-instance:' + identity, info['title'], folder_id, json.dumps([source]),
                 'telegram' if profile else None, info.get('peer'), profile['account'] if profile else None,
                 info.get('username'), auto_read if profile else None))
            subscriptions.details.created(identity)
            subscriptions.details.description(identity, info.get('description'))
            self.put_binding(identity, binding)
            self.save_metadata(identity, info)
            for claim in claims:
                self.claim(identity, claim, 'verified client' if profile else 'public preview')
            if history is not None:
                subscriptions.history.configure(identity, history)
        result: dict[str, Any] = subscriptions.feeds(identity)[0]
        return result

    def duplicates(self, feeds: list[str]) -> dict[str, Any]:
        return dict(existing_feeds=[dict(feed_id=feed, title=self.db.execute('SELECT title FROM feeds WHERE id=?', (feed,)).fetchone()[0], access=self.binding(feed)) for feed in feeds])


    def change_access(self, feed: str, binding: dict[str,Any], info: dict[str,Any],
                      auto_read: bool | None, subscriptions: Any) -> dict[str,Any]:
        """Apply verified channel access inside Runtime's acceptance transaction."""
        subscriptions.check_owner('feed',feed)
        row=self.db.execute('SELECT * FROM feeds WHERE id=?',(feed,)).fetchone()
        previous=self.binding(feed)
        if row['kind']!='telegram' or row['transport']=='rss' or row['connection']=='deleting' or not previous:
            raise TelegramConflict('access_immutable',dict(feed_id=feed),'Способ доступа этого фида изменить нельзя')
        known_peer=row['telegram_peer']
        if known_peer is not None and info.get('peer')!=known_peer:
            raise TelegramConflict('source_identity_conflict',dict(feed_id=feed),'Не подтверждён прежний канал Telegram')
        if info.get('kind') not in (None,'channel') or binding['kind']=='client' and info.get('kind')!='channel':
            raise TelegramConflict('access_immutable',dict(feed_id=feed),'Смена доступа поддерживается только для каналов')
        aliases={name.lower() for name in [info.get('username'),*info.get('usernames',[])] if isinstance(name,str)}
        if previous['kind']=='web' and previous['username'].lower() not in aliases:
            raise TelegramConflict('source_identity_conflict',dict(feed_id=feed),'Username прежнего фида не подтверждён клиентом')
        owners=[owner for owner in self.conflicts(self.claims(info),info.get('peer')) if owner!=feed]
        if owners:
            raise TelegramConflict('source_identity_conflict',dict(feed_id=feed),'Внешний ключ уже принадлежит другому фиду')
        profile=self.profile(binding['role']) if binding['kind']=='client' else None
        account=profile['account'] if profile else row['telegram_account']
        automatic=(bool(row['auto_read']) if auto_read is None else auto_read) if profile else None
        self.put_binding(feed,binding)
        self.db.execute('''UPDATE feeds SET telegram_peer=?,telegram_account=?,telegram_username=?,transport=?,
            connection='connected',auto_read=?,config=?,cursor='' WHERE id=?''',
            (info.get('peer',known_peer),account,info.get('username'), 'telegram' if profile else None,automatic,
             json.dumps(dict(source_kind=info.get('kind','channel'))),feed))
        self.save_metadata(feed,info)
        for claim in self.claims(info):
            self.claim(feed,claim,'explicit access change')
        self.modified(feed)
        return dict(subscriptions.feeds(feed)[0])

    def refresh(self, feed: str, info: dict[str, Any]) -> None:
        claims = self.claims(info)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.conflicts(claims, info.get('peer'))
            for claim in claims:
                owners = self.owners([claim])
                if owners and feed not in owners:
                    raise TelegramConflict('source_identity_conflict', dict(feed_id=feed), 'Новый alias уже занят другим фидом')
            for claim in claims:
                self.claim(feed, claim, 'client metadata refresh')
            self.save_metadata(feed, info)

    def save_metadata(self, feed: str, info: dict[str, Any]) -> None:
        if info.get('kind') in ('channel','group','supergroup'):
            row=self.db.execute('SELECT config FROM feeds WHERE id=?',(feed,)).fetchone()
            if row:
                config=json.loads(row[0])
                config['source_kind']=info['kind']
                self.db.execute('UPDATE feeds SET config=? WHERE id=?',(json.dumps(config),feed))
        if 'avatar' not in info:
            return
        if info['avatar'] is None:
            self.db.execute('DELETE FROM telegram_avatars WHERE feed=?', (feed,))
            return
        try:
            encoded = info['avatar']
            if not isinstance(encoded, str) or len(encoded) > 180000:
                return
            image = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return
        mime = ('image/jpeg' if image.startswith(b'\xff\xd8\xff') else 'image/png' if image.startswith(b'\x89PNG\r\n\x1a\n') else 'image/webp' if image[:4] == b'RIFF' and image[8:12] == b'WEBP' else None)
        if not mime or len(image) > 128*1024:
            return
        self.db.execute('INSERT INTO telegram_avatars VALUES (?,?,?,?) ON CONFLICT(feed) DO UPDATE SET image=excluded.image,mime=excluded.mime,digest=excluded.digest',
                        (feed, image, mime, hashlib.sha256(image).hexdigest()))

    def release(self, feed: str) -> None:
        self.db.execute('DELETE FROM telegram_avatars WHERE feed=?', (feed,))
        self.db.execute('DELETE FROM telegram_claim_owners WHERE feed=?', (feed,))
        self.db.execute('DELETE FROM telegram_source_claims WHERE NOT EXISTS (SELECT 1 FROM telegram_claim_owners o WHERE o.kind=telegram_source_claims.kind AND o.value=telegram_source_claims.value)')
        self.db.execute('DELETE FROM telegram_bindings WHERE feed=?', (feed,))
