"""Durable, content-free eligibility and cumulative Telegram confirmation."""
import json
from dataclasses import dataclass
from oko.collection.contracts import SourceError
from oko.collection.telegram.contracts import HistoryEnvelope
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from oko.storage.store import Store, Job


@dataclass(frozen=True)
class ReadPlan:
    initial: int
    confirmed: int
    until: int
    candidate: int
    waiting: str = ''
    missing: tuple[int, ...] = ()


class ReadSync:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        self._configuration: int | None = None
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS telegram_read_topics (
          feed TEXT, topic INTEGER, confirmed INTEGER, PRIMARY KEY(feed,topic));
        CREATE TABLE IF NOT EXISTS telegram_read_members (
          feed TEXT, member INTEGER, eligible INTEGER DEFAULT 0, reason TEXT, missing INTEGER DEFAULT 0,
          PRIMARY KEY(feed,member));
        CREATE TABLE IF NOT EXISTS telegram_read_state (
          feed TEXT PRIMARY KEY, since REAL, initial INTEGER,
          confirmed INTEGER DEFAULT 0, signature TEXT, waiting TEXT, error TEXT);
        ''')
        columns = {r['name'] for r in store.db.execute('PRAGMA table_info(telegram_read_members)')}
        if 'missing' not in columns:
            with store.db:
                store.db.execute('ALTER TABLE telegram_read_members ADD COLUMN missing INTEGER DEFAULT 0')

    def recover(self) -> None:
        """Import legacy tombstones once on startup, never in ordinary polling."""
        db = self.store.db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS telegram_read_dirty (feed TEXT PRIMARY KEY)')
            for event in ('INSERT', 'UPDATE'):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS telegram_read_member_{event}
                    AFTER {event} ON telegram_read_members BEGIN
                    INSERT INTO telegram_read_dirty VALUES (NEW.feed) ON CONFLICT(feed) DO NOTHING; END""")
            db.executescript("""
            CREATE TRIGGER IF NOT EXISTS telegram_read_item_added AFTER INSERT ON items
              BEGIN INSERT INTO telegram_read_dirty VALUES (NEW.feed_id) ON CONFLICT(feed) DO NOTHING; END;
            CREATE TRIGGER IF NOT EXISTS telegram_read_item_changed AFTER UPDATE OF complete,members,viewed ON items
              WHEN NEW.complete IS NOT OLD.complete OR NEW.members IS NOT OLD.members OR NEW.viewed IS NOT OLD.viewed
              BEGIN INSERT INTO telegram_read_dirty VALUES (NEW.feed_id) ON CONFLICT(feed) DO NOTHING; END;
            CREATE TRIGGER IF NOT EXISTS telegram_read_version_added AFTER INSERT ON content_current
              BEGIN INSERT INTO telegram_read_dirty SELECT feed_id FROM items WHERE id=NEW.item
                ON CONFLICT(feed) DO NOTHING; END;
            CREATE TRIGGER IF NOT EXISTS telegram_read_version_changed AFTER UPDATE ON content_current
              BEGIN INSERT INTO telegram_read_dirty SELECT feed_id FROM items WHERE id=NEW.item
                ON CONFLICT(feed) DO NOTHING; END;
            CREATE TRIGGER IF NOT EXISTS telegram_read_initial_added AFTER INSERT ON telegram_read_state
              BEGIN INSERT INTO telegram_read_dirty VALUES (NEW.feed) ON CONFLICT(feed) DO NOTHING; END;
            CREATE TRIGGER IF NOT EXISTS telegram_read_initial_changed AFTER UPDATE OF initial,since ON telegram_read_state
              WHEN NEW.initial IS NOT OLD.initial OR NEW.since IS NOT OLD.since
              BEGIN INSERT INTO telegram_read_dirty VALUES (NEW.feed) ON CONFLICT(feed) DO NOTHING; END;
            """)
            for row in db.execute("SELECT feed_id,members FROM items WHERE viewed=1 AND members!='[]'").fetchall():
                self.eligible(row['feed_id'], json.loads(row['members']), 'viewed')
        self.sync()

    def observed(self, feed: str, members: tuple[int, ...], cursor: str) -> None:
        db = self.store.db
        envelope = HistoryEnvelope.decode(cursor, adapter='client')
        db.execute('INSERT OR IGNORE INTO telegram_read_state(feed,since) VALUES (?,?)',
                   (feed, envelope.selection.since))
        selection = envelope.selection
        if selection.scan_complete and selection.oldest:
            db.execute('UPDATE telegram_read_state SET initial=? WHERE feed=? AND initial IS NULL', (selection.oldest - 1, feed))
        db.execute('UPDATE telegram_read_state SET signature=NULL WHERE feed=?', (feed,))
        db.executemany('INSERT INTO telegram_read_members(feed,member) VALUES (?,?) ON CONFLICT(feed,member) DO UPDATE SET missing=0',
                       [(feed, member) for member in members])

    def eligible(self, feed: str, members: list[int], reason: str) -> None:
        self.store.db.executemany('''INSERT INTO telegram_read_members(feed,member,eligible,reason) VALUES (?,?,1,?)
          ON CONFLICT(feed,member) DO UPDATE SET eligible=1,reason=excluded.reason''',
                                  [(feed, member, reason) for member in members])

    def request(self, feed: str) -> dict[str, Any] | None:
        configs = self.store.subscriptions.feeds(feed)
        if not configs:
            return None
        binding = self.store.subscriptions.telegram.binding(feed)
        profile = self.store.subscriptions.telegram.profile(binding['role']) if binding and binding['kind'] == 'client' else None
        return self._request(configs[0], binding, profile)

    def _request(self, config: dict[str, Any], binding: dict[str, Any] | None, profile: dict[str, Any] | None) -> dict[str, Any] | None:
        feed = config['id']
        history = config.get('initial_history')
        if history and history['mode'] == 'count' and (not history['frozen'] or not history.get('scan_complete', True)):
            return None
        state = self.store.db.execute('SELECT * FROM telegram_read_state WHERE feed=?', (feed,)).fetchone()
        if not state or config['transport'] != 'telegram' or config['auto_read'] is None:
            return None
        if not binding or binding['kind'] != 'client' or config['connection'] == 'deleting':
            return None
        return dict(account=profile['account'] if profile else config['telegram_account'], peer=config['telegram_peer'],
                    username=config.get('telegram_username'), since=state['since'],
                    initial=state['initial'], read_history=config['read_history'], auto_read=config['auto_read'],
                    eligible=[r[0] for r in self.store.db.execute(
                        "SELECT member FROM telegram_read_members WHERE feed=? AND eligible=1 AND missing=0 AND (?=1 OR reason='viewed') ORDER BY member", (feed, config['auto_read']))])

    def signature(self, request: dict[str, Any]) -> str:
        return json.dumps({k: v for k, v in request.items() if k != 'initial'}, sort_keys=True)

    def sync(self) -> None:
        db = self.store.db
        revision = self.store.subscriptions.source_revision()
        if revision != self._configuration:
            configs = self.store.subscriptions.feeds()
        else:
            pending = [row[0] for row in db.execute('SELECT feed FROM telegram_read_dirty')]
            if not pending:
                return
            configs = [config for feed in pending for config in self.store.subscriptions.feeds(feed)]
        bindings = self.store.subscriptions.telegram.bindings()
        profiles = self.store.subscriptions.telegram.profiles()
        for config in configs:
            feed = config['id']
            binding = bindings.get(feed)
            if config['transport'] == 'telegram' and config['connection'] != 'deleting' and binding and binding['kind'] == 'client':
                with db:
                    if config['auto_read']:
                        eligible = {r[0] for r in db.execute('SELECT member FROM telegram_read_members WHERE feed=? AND eligible=1', (feed,))}
                        for row in db.execute('SELECT * FROM items WHERE feed_id=? AND complete=1 AND viewed=0', (feed,)).fetchall():
                            members = json.loads(row['members'])
                            if members and not set(members) <= eligible:
                                try:
                                    header, _ = self.store.original(row['id'])
                                except (OSError, ValueError, KeyError):
                                    continue
                                if isinstance(header.get('title'), str) and header.get('full', True) and (not header.get('members') or set(header['members']) == set(members)):
                                    self.eligible(feed, members, 'saved')
                    request = self._request(config, binding, profiles.get(binding['role']))
                    if request is not None:
                        state = db.execute('SELECT signature,waiting FROM telegram_read_state WHERE feed=?', (feed,)).fetchone()
                        if request['eligible'] or request['read_history'] or state['waiting']:
                            signature = self.signature(request)
                            if state['signature'] != signature:
                                db.execute('UPDATE telegram_read_state SET signature=? WHERE feed=?', (signature, feed))
                                db.execute("INSERT INTO jobs(kind,item) VALUES ('read',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',next=0,error=NULL,generation=generation+1", (feed,))
            with db:
                db.execute('DELETE FROM telegram_read_dirty WHERE feed=?', (feed,))
        # Deleted feeds have no configuration and need no work.
        with db:
            db.execute('DELETE FROM telegram_read_dirty WHERE feed NOT IN (SELECT id FROM feeds)')
        self._configuration = revision

    def plan(self, request: dict[str, Any], state: dict[str, Any], window: dict[str, Any] | None = None) -> ReadPlan:
        confirmed = int(state['read_max'])
        initial = int(request['initial'] if request['initial'] is not None else state['initial'])
        eligible = set(request['eligible'])
        until = max([confirmed, initial if request['read_history'] else 0, *eligible])
        candidate, waiting = confirmed, ''
        missing: tuple[int, ...] = ()
        if window is not None:
            ids = window['ids']
            read_ids = set(window.get('read_ids', []))
            if not read_ids <= set(ids):
                raise SourceError('Неверная серверная граница прочитанного Telegram')
            if ids != sorted(set(ids)) or any(not confirmed < m <= until for m in ids) or (window['more'] and not ids):
                raise SourceError('Неполная или неверная проверка истории Telegram')
            verified = ids[-1] if window['more'] else until
            missing = tuple(sorted(m for m in eligible if confirmed < m <= verified and m not in ids))
            for member in ids:
                if member in eligible or member in read_ids or (request['read_history'] and member <= initial):
                    candidate = member
                else:
                    waiting = ('Старая непрочитанная история до импорта' if member <= initial
                               else 'Ожидает более раннее сообщение Telegram')
                    break
            if not waiting and window['more']:
                waiting = 'Продолжение проверки истории Telegram'
        return ReadPlan(initial, confirmed, until, candidate, waiting, missing)

    def confirmed(self, feed: str) -> int:
        row = self.store.db.execute('SELECT confirmed FROM telegram_read_state WHERE feed=?', (feed,)).fetchone()
        return int(row[0] or 0) if row else 0

    def topic_confirmed(self, feed: str, topic: int) -> int:
        row = self.store.db.execute('SELECT confirmed FROM telegram_read_topics WHERE feed=? AND topic=?', (feed, topic)).fetchone()
        return int(row[0]) if row else 0

    def topic_completed(self, feed: str, topic: int, confirmed: int) -> None:
        with self.store.db:
            self.store.db.execute("INSERT INTO telegram_read_topics VALUES (?,?,?) ON CONFLICT(feed,topic) DO UPDATE SET confirmed=MAX(confirmed,excluded.confirmed)", (feed, topic, confirmed))

    def completed(self, job: 'Job', plan: ReadPlan, confirmed: int, now: float) -> None:
        with self.store.db:
            self.store.db.executemany('UPDATE telegram_read_members SET missing=1 WHERE feed=? AND member=?',
                                      [(job.item, member) for member in plan.missing])
            self.store.db.execute('''UPDATE telegram_read_state SET initial=COALESCE(initial,?),
              confirmed=MAX(confirmed,?),waiting=?,error=NULL WHERE feed=?''', (plan.initial, confirmed, plan.waiting, job.item))
            if plan.waiting:
                self.store.db.execute("UPDATE jobs SET status='pending',next=?,error=?,problem_code=NULL WHERE id=? AND generation=? AND status='running'", (now + (.1 if plan.waiting == 'Продолжение проверки истории Telegram' else 30), plan.waiting, job.id, job.generation))
            else:
                self.store.finish(job)

    def suspend(self, job: 'Job') -> None:
        with self.store.db:
            self.store.finish(job)

    def failed(self, feed: str, label: str) -> None:
        with self.store.db:
            self.store.db.execute('UPDATE telegram_read_state SET error=? WHERE feed=?', (label, feed))

    def statuses(self) -> list[dict[str, Any]]:
        result = []
        for row in self.store.db.execute('SELECT * FROM telegram_read_state').fetchall():
            request = self.request(row['feed'])
            if request is None:
                continue
            result.append(dict(feed_id=row['feed'], initial=row['initial'], confirmed=row['confirmed'],
                               pending=sum(m > row['confirmed'] for m in request['eligible']),
                               waiting=row['waiting'] or '', error=row['error'] or ''))
        return result
