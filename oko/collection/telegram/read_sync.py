"""Durable, content-free eligibility and cumulative Telegram confirmation."""
import json
from dataclasses import dataclass
from oko.collection.contracts import SourceError
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
        store.db.executescript('''
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

    def observed(self, feed: str, members: tuple[int, ...], cursor: str) -> None:
        db = self.store.db
        db.execute('INSERT OR IGNORE INTO telegram_read_state(feed,since) VALUES (?,?)',
                   (feed, json.loads(cursor)['since']))
        selection = json.loads(cursor)
        if 'scan_complete' in selection and selection['scan_complete'] and selection.get('oldest'):
            db.execute('UPDATE telegram_read_state SET initial=? WHERE feed=? AND initial IS NULL', (selection['oldest'] - 1, feed))
        db.execute('UPDATE telegram_read_state SET signature=NULL WHERE feed=?', (feed,))
        db.executemany('INSERT INTO telegram_read_members(feed,member) VALUES (?,?) ON CONFLICT(feed,member) DO UPDATE SET missing=0',
                       [(feed, member) for member in members])

    def eligible(self, feed: str, members: list[int], reason: str) -> None:
        self.store.db.executemany('''INSERT INTO telegram_read_members(feed,member,eligible,reason) VALUES (?,?,1,?)
          ON CONFLICT(feed,member) DO UPDATE SET eligible=1,reason=excluded.reason''',
                                  [(feed, member, reason) for member in members])

    def request(self, feed: str) -> dict[str, Any] | None:
        config = next((f for f in self.store.subscriptions.feeds() if f['id'] == feed), None)
        history = config.get('initial_history') if config else None
        if history and history['mode'] == 'count' and (not history['frozen'] or not history.get('scan_complete', True)):
            return None
        state = self.store.db.execute('SELECT * FROM telegram_read_state WHERE feed=?', (feed,)).fetchone()
        if not config or not state or config['transport'] != 'telegram' or config['auto_read'] is None:
            return None
        binding = self.store.subscriptions.telegram.binding(feed)
        if not binding or binding['kind'] != 'client' or config['connection'] == 'deleting':
            return None
        profile = self.store.subscriptions.telegram.profile(binding['role'])
        return dict(account=profile['account'] if profile else config['telegram_account'], peer=config['telegram_peer'],
                    username=config.get('telegram_username'), topic_id=config.get('telegram_topic'), since=state['since'],
                    initial=state['initial'], read_history=config['read_history'], auto_read=config['auto_read'],
                    eligible=[r[0] for r in self.store.db.execute(
                        "SELECT member FROM telegram_read_members WHERE feed=? AND eligible=1 AND missing=0 AND (?=1 OR reason='viewed') ORDER BY member", (feed, config['auto_read']))])

    def signature(self, request: dict[str, Any]) -> str:
        return json.dumps({k: v for k, v in request.items() if k != 'initial'}, sort_keys=True)

    def sync(self) -> None:
        db = self.store.db
        for config in self.store.subscriptions.feeds():
            feed = config['id']
            binding = self.store.subscriptions.telegram.binding(feed)
            if config['transport'] != 'telegram' or config['connection'] == 'deleting' or not binding or binding['kind'] != 'client':
                continue
            with db:
                eligible = {r[0] for r in db.execute('SELECT member FROM telegram_read_members WHERE feed=? AND eligible=1', (feed,))}
                # Viewed tombstones predate this queue in existing installations.
                # Their service IDs are sufficient; deleted text is never needed.
                for row in db.execute('SELECT members FROM items WHERE feed_id=? AND viewed=1', (feed,)).fetchall():
                    missing = set(json.loads(row['members'])) - eligible
                    if missing:
                        self.eligible(feed, sorted(missing), 'viewed')
                        eligible.update(missing)
                if config['auto_read']:
                    for row in db.execute('SELECT * FROM items WHERE feed_id=? AND complete=1 AND viewed=0', (feed,)).fetchall():
                        members = json.loads(row['members'])
                        if members and not set(members) <= eligible:
                            material = self.store.material(row['id'])
                            if material and material['full']:
                                self.eligible(feed, members, 'saved')
                request = self.request(feed)
                if request is None:
                    continue
                state = db.execute('SELECT signature,waiting FROM telegram_read_state WHERE feed=?', (feed,)).fetchone()
                if not request['eligible'] and not request['read_history'] and not state['waiting']:
                    continue
                signature = self.signature(request)
                if state['signature'] != signature:
                    db.execute('UPDATE telegram_read_state SET signature=? WHERE feed=?', (signature, feed))
                    db.execute("INSERT INTO jobs(kind,item) VALUES ('read',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',next=0,error=NULL,generation=generation+1", (feed,))

    def plan(self, request: dict[str, Any], state: dict[str, Any], window: dict[str, Any] | None = None) -> ReadPlan:
        confirmed = int(state['read_max'])
        initial = int(request['initial'] if request['initial'] is not None else state['initial'])
        eligible = set(request['eligible'])
        until = max([confirmed, initial if request['read_history'] else 0, *eligible])
        candidate, waiting = confirmed, ''
        missing: tuple[int, ...] = ()
        if window is not None:
            ids = window['ids']
            if ids != sorted(set(ids)) or any(not confirmed < m <= until for m in ids) or (window['more'] and not ids):
                raise SourceError('Неполная или неверная проверка истории Telegram')
            verified = ids[-1] if window['more'] else until
            missing = tuple(sorted(m for m in eligible if confirmed < m <= verified and m not in ids))
            for member in ids:
                if member in eligible or (request['read_history'] and member <= initial):
                    candidate = member
                else:
                    waiting = ('Старая непрочитанная история до импорта' if member <= initial
                               else 'Ожидает более раннее сообщение Telegram')
                    break
            if not waiting and window['more']:
                waiting = 'Продолжение проверки истории Telegram'
        return ReadPlan(initial, confirmed, until, candidate, waiting, missing)

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
