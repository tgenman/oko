"""Durable, content-free eligibility and cumulative Telegram confirmation."""
import json
from oko.storage.reading import reading_value
from oko.storage.source_configuration import changed as source_changed
from oko.storage.reader_status import changed as status_changed
from dataclasses import dataclass
from oko.collection.contracts import SourceError
from oko.collection.telegram.contracts import HistoryEnvelope
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from oko.storage.store import Store


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

    def recover(self) -> None:
        """Reconcile current channel read eligibility on startup."""
        self.store.jobs.sync_reads()

    def pending(self) -> bool:
        return (self._configuration != self.store.subscriptions.source_revision()
                or self.store.db.execute('SELECT 1 FROM telegram_read_state WHERE needs_sync OR saved_backfill_after IS NOT NULL LIMIT 1').fetchone() is not None)

    def mark_feed(self, feed: str) -> None:
        self.store.db.execute("""INSERT INTO telegram_read_state(feed, needs_sync)
            SELECT id, true FROM feeds WHERE id=%s AND transport='telegram' AND connection IS DISTINCT FROM 'deleting'
            ON CONFLICT(feed) DO UPDATE SET needs_sync=true WHERE NOT telegram_read_state.needs_sync""", (feed,))

    def schedule_saved_backfill(self, feed: str) -> None:
        self.mark_feed(feed)
        self.store.db.execute("UPDATE telegram_read_state SET saved_backfill_after='' WHERE feed=%s", (feed,))

    def mark_item(self, item: str) -> None:
        self.store.db.execute("""INSERT INTO telegram_read_state(feed, needs_sync)
            SELECT DISTINCT f.id, true FROM reading_memberships m JOIN feeds f ON f.id=m.feed_id
            WHERE m.publication_id=%s AND f.transport='telegram' AND f.connection IS DISTINCT FROM 'deleting'
            ON CONFLICT(feed) DO UPDATE SET needs_sync=true WHERE NOT telegram_read_state.needs_sync""", (item,))


    def observed(self, feed: str, members: tuple[int, ...], cursor: str) -> None:
        db = self.store.db
        envelope = HistoryEnvelope.decode(cursor, adapter='client')
        previous = db.execute('SELECT since,initial FROM telegram_read_state WHERE feed=%s', (feed,)).fetchone()
        db.execute('INSERT INTO telegram_read_state ( feed , since ) VALUES ( %s , %s ) ON CONFLICT (feed) DO UPDATE SET since=COALESCE(telegram_read_state.since,excluded.since)',
                   (feed, envelope.selection.since))
        selection = envelope.selection
        boundary_changed = False
        if selection.scan_complete and selection.oldest:
            if db.execute('UPDATE telegram_read_state SET initial = %s WHERE feed = %s AND initial IS NULL', (selection.oldest - 1, feed)).rowcount:
                source_changed(db)
                boundary_changed = True
        db.execute('UPDATE telegram_read_state SET signature = NULL WHERE feed = %s', (feed,))
        # Re-observing an unchanged page still requires checking the remote
        # read frontier. Member upserts alone may not change any row.
        self.mark_feed(feed)
        member_changed = False
        if members:
            member_changed = db.execute('''INSERT INTO telegram_read_members(feed,member)
                SELECT %s,unnest(%s::bigint[]) ON CONFLICT(feed,member) DO UPDATE SET missing=0
                WHERE telegram_read_members.missing IS DISTINCT FROM 0 RETURNING member''', (feed,sorted(set(members)))).fetchone() is not None
        if previous is None or previous['since'] is None or boundary_changed or member_changed:
            status_changed(db)

    def eligible(self, feed: str, members: list[int], reason: str) -> None:
        if reason not in ('saved', 'viewed'):
            raise ValueError('Invalid Telegram read reason')
        if not members:
            return
        changed = self.store.db.execute(f'''INSERT INTO telegram_read_members (feed,member,{reason})
            SELECT %s,member,true FROM unnest(%s::bigint[]) AS member
            ON CONFLICT (feed,member) DO UPDATE SET {reason}=true WHERE NOT telegram_read_members.{reason}
            RETURNING member''', (feed, sorted(set(members)))).fetchone()
        if changed:
            self.mark_feed(feed)
            status_changed(self.store.db)

    def saved_publication(self, item: str) -> None:
        db = self.store.db
        feeds = [r[0] for r in db.execute("""SELECT f.id FROM reading_memberships m JOIN feeds f ON f.id=m.feed_id
            WHERE m.publication_id=%s AND f.transport='telegram' AND f.connection IS DISTINCT FROM 'deleting'""", (item,))]
        if not feeds:
            return
        row = db.execute('SELECT complete,members FROM items WHERE id=%s AND viewed=0', (item,)).fetchone()
        if not row or not row['complete']:
            return
        members = json.loads(row['members'])
        if not members:
            return
        try:
            header, _ = self.store.original(item)
        except (OSError, ValueError, KeyError):
            return
        if not isinstance(header.get('title'), str) or not header.get('full', True) or (header.get('members') and set(header['members']) != set(members)):
            return
        for feed in feeds:
            self.eligible(feed, members, 'saved')

    def request(self, feed: str) -> dict[str, Any] | None:
        configs = self.store.subscriptions.feeds(feed, transport='telegram')
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
        state = self.store.db.execute('SELECT * FROM telegram_read_state WHERE feed = %s', (feed,)).fetchone()
        if not state or state['since'] is None or state['saved_backfill_after'] is not None or config['transport'] != 'telegram' or config['auto_read'] is None:
            return None
        if not binding or binding['kind'] != 'client' or config['connection'] == 'deleting':
            return None
        return dict(account=profile['account'] if profile else config['telegram_account'], peer=config['telegram_peer'],
                    username=config.get('telegram_username'), since=state['since'],
                    initial=state['initial'], read_history=config['read_history'], auto_read=config['auto_read'],
                    eligible=[r[0] for r in self.store.db.execute(
                        'SELECT member FROM telegram_read_members WHERE feed = %s AND missing = 0 AND ( viewed OR ( %s = 1 AND saved ) ) ORDER BY member', (feed, config['auto_read']))])

    def signature(self, request: dict[str, Any]) -> str:
        return json.dumps({k: v for k, v in request.items() if k != 'initial'}, sort_keys=True)


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
        row = self.store.db.execute('SELECT confirmed FROM telegram_read_state WHERE feed = %s', (feed,)).fetchone()
        return int(row[0] or 0) if row else 0



    def completed(self, feed: str, plan: ReadPlan, confirmed: int) -> None:
        previous = self.store.db.execute('SELECT initial,confirmed,waiting,error FROM telegram_read_state WHERE feed=%s', (feed,)).fetchone()
        member_changed = False
        if plan.missing:
            member_changed = bool(self.store.db.execute('UPDATE telegram_read_members SET missing=1 WHERE feed=%s AND member=ANY(%s) AND missing IS DISTINCT FROM 1', (feed,list(plan.missing))).rowcount)
        self.store.db.execute('UPDATE telegram_read_state SET initial = COALESCE ( initial , %s ) , confirmed = CASE WHEN confirmed IS NULL THEN NULL ELSE greatest(confirmed,%s) END , waiting = %s , error = NULL WHERE feed = %s',(plan.initial,confirmed,plan.waiting,feed))
        if previous is not None and previous[0] is None:
            source_changed(self.store.db)
        current = self.store.db.execute('SELECT initial,confirmed,waiting,error FROM telegram_read_state WHERE feed=%s', (feed,)).fetchone()
        if member_changed or previous != current:
            status_changed(self.store.db)
        if plan.missing:
            self.mark_feed(feed)

    def failed(self, feed: str, label: str) -> None:
        with self.store.db:
            if self.store.db.execute('UPDATE telegram_read_state SET error = %s WHERE feed = %s AND error IS DISTINCT FROM %s', (label,feed,label)).rowcount:
                status_changed(self.store.db)

    @reading_value
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
