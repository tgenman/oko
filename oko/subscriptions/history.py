"""Durable initial-history selection, independent from processing and reading."""
from oko.storage.database import expect_row, Database, Row
from oko.domain.initial_history import History as HistoryFacts
from dataclasses import dataclass
from oko.storage.source_configuration import changed as source_changed
from oko.storage.reader_status import changed as status_changed
from datetime import date, datetime, time, timezone
import json
import secrets
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from oko.collection.telegram.contracts import HistorySelection

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class InitialHistory(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    mode: Literal['count'] = 'count'
    count: int | None = Field(default=20, ge=1)

    @field_validator('count', mode='before')
    @classmethod
    def exact_integer(cls, value: Any) -> Any:
        # Browsers transport integers beyond JSON's exact range as decimal text.
        if isinstance(value, str) and value.isascii() and value.isdecimal():
            return int(value)
        return value

    @model_validator(mode='after')
    def valid_boundary(self) -> 'InitialHistory':
        if self.count is None:
            raise ValueError('Укажите количество публикаций от 1')
        return self

    def selection(self) -> dict[str, Any]:
        result = self.model_dump()
        result['since']=None
        return result


class InvalidHistoryError(ValueError):
    """The saved history cannot be used without repair."""


@dataclass(frozen=True)
class InvalidHistory:
    feed: str


_OUTCOMES = ('pending','retrying','unavailable','loaded','incomplete','viewed')


def _valid_transitions(value: Any) -> bool:
    return (isinstance(value,dict) and set(value)==set(_OUTCOMES)
            and all(isinstance(outcome,str) and outcome in _OUTCOMES for outcome in value.values()))


class History:
    def __init__(self, db: Database) -> None:
        self.db = db
        pass

    def member(self, item: str) -> bool:
        return self.db.execute('SELECT 1 FROM history_members WHERE item = %s', (item,)).fetchone() is not None

    def _add_members(self, feed: str, items: list[str]) -> None:
        ordered = sorted(set(items))
        changed = False
        for start in range(0,len(ordered),400):
            changed = bool(self.db.execute("""INSERT INTO history_members(feed,item)
                SELECT %s,unnest(%s::text[]) ON CONFLICT DO NOTHING""", (feed,ordered[start:start+400])).rowcount) or changed
        if changed:
            status_changed(self.db)

    def warnings(self, feed: str, warnings: list[str]) -> None:
        row = self.db.execute('SELECT selection FROM initial_history WHERE feed = %s', (feed,)).fetchone()
        if row:
            selection = json.loads(row['selection'])
            selection['warnings'] = warnings
            self.db.execute('UPDATE initial_history SET selection = %s WHERE feed = %s', (json.dumps(selection), feed))

    def telegram_state(self, feed: str, progress: HistorySelection) -> None:
        row = self.db.execute('SELECT selection FROM initial_history WHERE feed = %s', (feed,)).fetchone()
        if not row:
            return
        selection = json.loads(row['selection'])
        selection.update(warnings=list(progress.warnings), discovery_error=progress.error,
                         discovery_retry_at=progress.retry_at)
        self.db.execute('UPDATE initial_history SET selection = %s WHERE feed = %s', (json.dumps(selection), feed))

    def discovery_state(self, feed: str, cursor: dict[str, Any]) -> None:
        row = self.db.execute('SELECT selection FROM initial_history WHERE feed = %s', (feed,)).fetchone()
        if not row:
            return
        selection = json.loads(row['selection'])
        selection['warnings'] = cursor.get('warnings', selection.get('warnings', []))
        selection['discovery_error'] = cursor.get('history_error')
        selection['stop_reason'] = cursor.get('stop_reason',selection.get('stop_reason'))
        selection['discovery_retry_at'] = cursor.get('history_retry_at')
        selection['waiting'] = cursor.get('history_waiting', False)
        selection['requests'] = cursor.get('history_requests', selection.get('requests',0))
        self.db.execute('UPDATE initial_history SET selection = %s WHERE feed = %s', (json.dumps(selection), feed))

    def configure(self, feed: str, selection: InitialHistory) -> None:
        run = secrets.token_hex(16)
        self.db.execute('INSERT INTO initial_history ( feed , selection ) VALUES ( %s , %s )', (feed, json.dumps(dict(selection.selection(),run=run))))
        self.db.execute('UPDATE feeds SET initial_history_expected=true,initial_history_run=%s WHERE id=%s', (run,feed))
        source_changed(self.db)

    def start(self, feed: str, selection: InitialHistory, *, restore: bool = False) -> dict[str, Any]:
        current = self.status(feed)
        if not restore and current and current['state'] not in ('done', 'cancelled'):
            raise ValueError('Сначала остановите текущую загрузку истории')
        if self.db.execute('DELETE FROM history_members WHERE feed = %s', (feed,)).rowcount:
            status_changed(self.db)
        run = secrets.token_hex(16)
        value = dict(**selection.selection(), run=run, resume=0)
        self.db.execute('INSERT INTO initial_history ( feed , selection ) VALUES ( %s , %s ) ON CONFLICT ( feed ) DO UPDATE SET selection = excluded . selection , frozen = 0 , paused = 0 , scan_complete = 0',
            (feed, json.dumps(value)))
        self.db.execute('UPDATE feeds SET initial_history_expected=true,initial_history_run=%s WHERE id=%s', (run,feed))
        source_changed(self.db)
        result = self.status(feed)
        assert result is not None
        return result

    def cancel(self, feed: str) -> dict[str, Any]:
        current = self.status(feed)
        if current is None:
            raise ValueError('История не настроена')
        row = expect_row(self.db.execute('SELECT selection FROM initial_history WHERE feed = %s', (feed,)).fetchone())
        value = json.loads(row['selection'])
        value.update(cancelled=True, waiting=False, stop_reason='cancelled')
        self.db.execute('UPDATE initial_history SET selection = %s , paused = 1 WHERE feed = %s', (json.dumps(value), feed))
        if not current.get('cancelled') or not current['paused']:
            source_changed(self.db)
        result = self.status(feed)
        assert result is not None
        return result

    def freeze(self, feed: str, items: list[str]) -> None:
        row = self.db.execute('SELECT frozen FROM initial_history WHERE feed = %s', (feed,)).fetchone()
        if not row or row['frozen']:
            return
        self._add_members(feed,items)
        self.db.execute('UPDATE initial_history SET frozen = 1 WHERE feed = %s', (feed,))
        source_changed(self.db)

    def enroll(self, feed: str, items: list[str], complete: bool) -> None:
        self._add_members(feed,items)
        if self.db.execute('UPDATE initial_history SET frozen = 1 , scan_complete = %s WHERE feed = %s AND (frozen IS DISTINCT FROM 1 OR scan_complete IS DISTINCT FROM %s)', (complete, feed, complete)).rowcount:
            source_changed(self.db)

    def pause(self, feed: str, paused: bool) -> dict[str, Any]:
        status = self.status(feed)
        if status is None:
            raise ValueError('Начальная история не настроена')
        if status.get('cancelled'):
            raise ValueError('История остановлена; начните новый подбор')
        changed = False
        if not paused and not status.get('stop_reason'):
            row = expect_row(self.db.execute('SELECT selection FROM initial_history WHERE feed = %s', (feed,)).fetchone())
            selection = json.loads(row['selection'])
            selection.update(resume=selection.get('resume', 0) + 1, waiting=False, discovery_error=None)
            self.db.execute('UPDATE initial_history SET selection = %s WHERE feed = %s', (json.dumps(selection), feed))
            changed = True
        if status['state'] != 'done':
            changed = bool(self.db.execute('UPDATE initial_history SET paused = %s WHERE feed = %s AND paused IS DISTINCT FROM %s', (paused, feed, paused)).rowcount) or changed
        if changed:
            source_changed(self.db)
        result = self.status(feed)
        assert result is not None
        return result

    def saved(self, item: str, full: bool) -> None:
        outcome = 'loaded' if full else 'incomplete'
        self._events([item], 'saved', outcome)

    def viewed(self, item: str) -> None:
        self.viewed_many([item])

    def viewed_many(self, items: list[str]) -> None:
        self._events(items, 'viewed', 'viewed')

    def _events(self, items: list[str], operation: Literal['saved', 'viewed'], outcome: str) -> None:
        """Apply healthy history outcomes; durably defer the rest in order."""
        from oko.storage import memberships
        if not items:
            return
        if not self.db.in_transaction:
            raise RuntimeError('History outcomes require an owning transaction')
        memberships.lock_graph(self.db)
        selected = sorted(set(items))
        for start in range(0, len(selected), 400):
            batch = selected[start:start+400]
            rows = self.db.execute('''SELECT m.feed,m.item,m.outcome AS base_outcome FROM history_members m
                WHERE m.item=ANY(%s) ORDER BY m.feed,m.item FOR UPDATE OF m''', (batch,)).fetchall()
            if not rows:
                continue
            feeds = sorted({row['feed'] for row in rows})
            # Lock both sources of run identity through event insertion. A valid
            # but different selection must never be merged into the old run.
            admission_rows = self._admission_rows(feeds, lock=True)
            invalid = self._invalid_admission_rows(feeds, admission_rows, require_rows=True)
            selections = {row['feed']:row for row in admission_rows}
            event_runs: dict[str, str | None] = {}
            for feed in feeds:
                row = selections.get(feed)
                if row is None:
                    invalid.add(feed)
                    event_runs[feed] = None
                    continue
                marker = row['initial_history_run']
                marker = marker if isinstance(marker, str) and marker else None
                if row['selection_jsonb_valid'] is not True:
                    # Invalid JSON cannot supply a competing run. The marker is
                    # the last identity atomically installed by History.start.
                    event_runs[feed] = marker
                    continue
                try:
                    selection = json.loads(row['selection'])
                except (TypeError, ValueError, RecursionError):
                    event_runs[feed] = None
                    invalid.add(feed)
                    continue
                run = selection.get('run') if isinstance(selection, dict) else None
                run = run if isinstance(run, str) and run else None
                if run != marker or not isinstance(selection, dict):
                    # No evidence that the outcome belongs to either run.
                    invalid.add(feed)
                    event_runs[feed] = None
                else:
                    event_runs[feed] = run
            deferred = [(row['feed'],row['item'],event_runs[row['feed']],row['base_outcome']) for row in rows if row['feed'] in invalid]
            if deferred:
                latest = {(row['feed'],row['item']):row for row in self.db.execute('''SELECT DISTINCT ON (e.feed,e.item)
                    e.id,e.feed,e.item,e.run_id,e.transitions FROM history_deferred_events e
                    JOIN unnest(%s::text[],%s::text[]) v(feed,item) ON e.feed=v.feed AND e.item=v.item
                    ORDER BY e.feed,e.item,e.id DESC''', ([row[0] for row in deferred],[row[1] for row in deferred]))}
                inserts = []
                updates = []
                for feed,item,run,base in deferred:
                    previous = latest.get((feed,item))
                    same_run = run is not None and previous is not None and previous['run_id'] == run and _valid_transitions(previous['transitions'])
                    transition = dict(previous['transitions']) if same_run and previous is not None else {state:state for state in _OUTCOMES}
                    for state in _OUTCOMES:
                        value = transition[state]
                        if operation=='saved' or value in ('pending','retrying'):
                            transition[state] = outcome
                    encoded = json.dumps(transition)
                    if same_run and previous is not None:
                        updates.append((previous['id'],encoded))
                    else:
                        inserts.append((feed,item,run,base,encoded))
                if updates:
                    self.db.execute('''UPDATE history_deferred_events e SET transitions=v.transitions::jsonb
                        FROM unnest(%s::bigint[],%s::text[]) v(id,transitions) WHERE e.id=v.id''',
                        ([row[0] for row in updates],[row[1] for row in updates]))
                if inserts:
                    self.db.execute('''INSERT INTO history_deferred_events(feed,item,run_id,base_outcome,transitions)
                        SELECT feed,item,run_id,base,transitions::jsonb FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::text[]) v(feed,item,run_id,base,transitions)''',
                        tuple([row[i] for row in inserts] for i in range(5)))
            predicate = " AND outcome IN ('pending','retrying')" if operation == 'viewed' else ''
            changed = self.db.execute('UPDATE history_members SET outcome=%s WHERE item=ANY(%s) AND NOT (feed=ANY(%s)) AND outcome IS DISTINCT FROM %s'+predicate,
                                      (outcome,batch,sorted(invalid),outcome)).rowcount
            if changed or deferred:
                status_changed(self.db)

    def replay_deferred(self, feed: str, *, limit: int = 200) -> tuple[bool, bool]:
        """Replay one bounded page; return whether work advanced and remains.

        Call in an owning transaction after repair. Unknown or different run
        identities remain pending for explicit recovery rather than guessing.
        """
        from oko.storage import memberships
        if not self.db.in_transaction or not 1 <= limit <= 200:
            raise ValueError('Replay requires an owning transaction and a bounded limit')
        memberships.lock_graph(self.db)
        owner = self.db.execute('SELECT deleted FROM feeds WHERE id=%s FOR UPDATE', (feed,)).fetchone()
        if owner is None or owner['deleted']:
            return False, False
        if feed in self.invalid_feeds([feed], lock=True, require_rows=True, allow_deferred=True):
            return False, bool(self.db.execute('SELECT 1 FROM history_deferred_events WHERE feed=%s LIMIT 1',(feed,)).fetchone())
        row = self.db.execute('''SELECT f.initial_history_run,f.history_replay_after,h.selection FROM feeds f
            JOIN initial_history h ON h.feed=f.id WHERE f.id=%s FOR UPDATE OF f''', (feed,)).fetchone()
        if not row:
            return False, bool(self.db.execute('SELECT 1 FROM history_deferred_events WHERE feed=%s LIMIT 1',(feed,)).fetchone())
        run = json.loads(row['selection']).get('run')
        marker = row['initial_history_run']
        events = self.db.execute('SELECT id,item,run_id,base_outcome,transitions FROM history_deferred_events WHERE feed=%s AND id>%s ORDER BY id LIMIT %s FOR UPDATE', (feed,row['history_replay_after'],limit)).fetchall()
        items = sorted({event['item'] for event in events})
        first = {r['item']:r['id'] for r in self.db.execute('SELECT item,min(id) AS id FROM history_deferred_events WHERE feed=%s AND item=ANY(%s) GROUP BY item', (feed,items))}
        saved = {r['item']:r['outcome'] for r in self.db.execute('SELECT item,outcome FROM history_members WHERE feed=%s AND item=ANY(%s) FOR UPDATE', (feed,items))}
        outcomes = dict(saved)
        consumed = []
        accepted: set[str] = set()
        blocked: set[str] = set()
        for event in events:
            item = event['item']
            if item in blocked:
                continue
            if (item not in outcomes or (item not in accepted and first.get(item)!=event['id'])
                    or not isinstance(run,str) or not run or marker != run or event['run_id'] != run):
                blocked.add(item)
                continue
            prior = outcomes[item]
            transition = event['transitions']
            base = event['base_outcome']
            if not _valid_transitions(transition) or base not in _OUTCOMES:
                blocked.add(item)
                continue
            result = transition[base]
            if prior == base:
                outcomes[item]=result
            elif prior not in (result, 'viewed'):
                blocked.add(item)
                continue
            accepted.add(item)
            consumed.append(event['id'])
        if consumed:
            changed = [(item,outcome) for item,outcome in outcomes.items() if outcome != saved[item]]
            if changed:
                self.db.execute('''UPDATE history_members h SET outcome=v.outcome
                    FROM unnest(%s::text[],%s::text[]) v(item,outcome)
                    WHERE h.feed=%s AND h.item=v.item''', ([row[0] for row in changed],[row[1] for row in changed],feed))
            self.db.execute('DELETE FROM history_deferred_events WHERE id=ANY(%s)', (consumed,))
            status_changed(self.db)
        pending = bool(self.db.execute('SELECT 1 FROM history_deferred_events WHERE feed=%s LIMIT 1', (feed,)).fetchone())
        cursor = events[-1]['id'] if len(events)==limit and pending else 0
        if cursor != row['history_replay_after']:
            self.db.execute('UPDATE feeds SET history_replay_after=%s WHERE id=%s', (cursor,feed))
        return bool(consumed), pending

    def status(self, feed: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT h.*,pg_input_is_valid(h.selection,'jsonb') AS selection_jsonb_valid,f.id AS history_owner_id,f.initial_history_run,EXISTS(SELECT 1 FROM history_deferred_events e WHERE e.feed=h.feed) AS has_deferred FROM initial_history h LEFT JOIN feeds f ON f.id=h.feed WHERE h.feed=%s", (feed,)).fetchone()
        if not row:
            if self._invalid_missing([feed]):
                raise InvalidHistoryError('Повреждена сохранённая история фида')
            return None
        if row['has_deferred']:
            raise InvalidHistoryError('История фида ожидает согласования')
        counts = {r['outcome']: r['n'] for r in self.db.execute('SELECT outcome , count ( * ) n FROM history_members WHERE feed = %s GROUP BY outcome', (feed,))}
        errors = [dict(item=r['item'],message=r['error']) for r in self.db.execute("SELECT h . item , ( SELECT j . reason_message FROM jobs j WHERE j . item = h . item AND j . kind = 'fetch' ) AS error FROM history_members h WHERE h . feed = %s AND h . outcome IN ( 'retrying' , 'unavailable' )",(feed,)) if r['error'] is not None]
        return self._status(row,counts,errors)

    def statuses(self, feeds: list[str], *, lock: bool = False) -> dict[str,dict[str,Any] | InvalidHistory]:
        if not feeds:
            return {}
        selected=feeds
        if lock:
            self._lock_feeds(selected)
        rows=self.db.execute("SELECT h.*,pg_input_is_valid(h.selection,'jsonb') AS selection_jsonb_valid,f.id AS history_owner_id,f.initial_history_run,EXISTS(SELECT 1 FROM history_deferred_events e WHERE e.feed=h.feed) AS has_deferred FROM initial_history h LEFT JOIN feeds f ON f.id=h.feed WHERE h.feed=ANY(%s)" + (' FOR UPDATE OF h' if lock else ''),(selected,)).fetchall()
        present = {row['feed'] for row in rows}
        missing = [feed for feed in selected if feed not in present]
        result: dict[str,dict[str,Any] | InvalidHistory] = {feed:InvalidHistory(feed) for feed in self._invalid_missing(missing)}
        if not rows:
            return result
        counts: dict[str,dict[str,int]]={}
        errors: dict[str,list[dict[str,Any]]]={}
        for row in self.db.execute("""SELECT h.feed, h.outcome, count(*) AS n,
            jsonb_agg(jsonb_build_object('item',h.item,'message',j.reason_message))
              FILTER (WHERE h.outcome IN ('retrying','unavailable') AND j.reason_message IS NOT NULL) AS errors
            FROM history_members h LEFT JOIN jobs j ON j.item=h.item AND j.kind='fetch'
            WHERE h.feed = ANY(%s) GROUP BY h.feed,h.outcome""", (selected,)):
            counts.setdefault(row['feed'],{})[row['outcome']]=row['n']
            errors.setdefault(row['feed'],[]).extend(row['errors'] or [])
        for row in rows:
            try:
                if row['has_deferred']:
                    raise InvalidHistoryError('История фида ожидает согласования')
                result[row['feed']] = self._status(row,counts.get(row['feed'],{}),errors.get(row['feed'],[]))
            except InvalidHistoryError:
                result[row['feed']] = InvalidHistory(row['feed'])
        return result

    def _invalid_missing(self, feeds: list[str]) -> set[str]:
        if not feeds:
            return set()
        return {row['feed'] for row in self.db.execute("""SELECT target.feed FROM unnest(%s::text[]) AS target(feed)
            WHERE EXISTS (SELECT 1 FROM feeds f WHERE f.id=target.feed AND f.initial_history_expected)
               OR EXISTS (SELECT 1 FROM history_members m WHERE m.feed=target.feed)
               OR EXISTS (SELECT 1 FROM history_deferred_events e WHERE e.feed=target.feed)
               OR EXISTS (SELECT 1 FROM reader_problems p WHERE p.identity='history-invalid:'||target.feed AND p.finished IS NULL)""", (feeds,))}

    def _lock_feeds(self, feeds: list[str]) -> None:
        # All callers holding the graph lock take owners in the same order
        # before locking the matching history rows.
        if feeds:
            self.db.execute('SELECT id FROM feeds WHERE id=ANY(%s) ORDER BY id FOR UPDATE', (sorted(set(feeds)),)).fetchall()

    def _admission_rows(self, feeds: list[str], *, lock: bool = False) -> list[Row]:
        if lock:
            self._lock_feeds(feeds)
        query = """SELECT h.*,h.xmin::text AS history_revision,f.id AS history_owner_id,f.initial_history_run,
            pg_input_is_valid(h.selection,'jsonb') AS selection_jsonb_valid,
            EXISTS (SELECT 1 FROM history_deferred_events e WHERE e.feed=h.feed) AS has_deferred,
            EXISTS (SELECT 1 FROM history_members m JOIN jobs j ON j.item=m.item AND j.kind='fetch'
                WHERE m.feed=h.feed AND m.item='' AND m.outcome IN ('retrying','unavailable')
                AND j.reason_message IS NOT NULL) AS invalid_error_item,
            EXISTS (SELECT 1 FROM history_members m WHERE m.feed=h.feed
                AND (m.outcome IS NULL OR m.outcome NOT IN ('pending','retrying','unavailable','loaded','incomplete','viewed'))) AS invalid_outcome
            FROM initial_history h LEFT JOIN feeds f ON f.id=h.feed WHERE h.feed=ANY(%s)"""
        return list(self.db.execute(query + (' FOR UPDATE OF h' if lock else ''), (feeds,)).fetchall())

    def admission(self, feed: str, *, lock: bool = False, allow_deferred: bool = False) -> tuple[dict[str, Any] | None, str | None]:
        rows = self._admission_rows([feed], lock=lock)
        if not rows:
            if self._invalid_missing([feed]):
                raise InvalidHistoryError('Повреждена сохранённая история фида')
            return None, None
        row = rows[0]
        status = self._status(row, {}, [])
        if row['invalid_outcome'] or row['invalid_error_item'] or row['has_deferred'] and not allow_deferred:
            raise InvalidHistoryError('Повреждена сохранённая история фида')
        return status, str(row['history_revision'])

    def invalid_feeds(self, feeds: list[str], *, lock: bool = False, require_rows: bool = False, allow_deferred: bool = False) -> set[str]:
        if not feeds:
            return set()
        rows = self._admission_rows(feeds, lock=lock)
        return self._invalid_admission_rows(feeds, rows, require_rows=require_rows, allow_deferred=allow_deferred)

    def _invalid_admission_rows(self, feeds: list[str], rows: list[Row], *, require_rows: bool = False, allow_deferred: bool = False) -> set[str]:
        present = {row['feed'] for row in rows}
        missing = [feed for feed in feeds if feed not in present]
        invalid = set(missing) if require_rows else self._invalid_missing(missing)
        for row in rows:
            try:
                self._status(row, {}, [])
                if row['invalid_outcome'] or row['invalid_error_item'] or row['has_deferred'] and not allow_deferred:
                    raise InvalidHistoryError('Повреждена сохранённая история фида')
            except InvalidHistoryError:
                invalid.add(row['feed'])
        return invalid

    def revision(self, feed: str) -> str | None:
        row = self.db.execute('SELECT xmin::text FROM initial_history WHERE feed=%s', (feed,)).fetchone()
        return str(row[0]) if row else None

    @staticmethod
    def _status(row: Row, counts: dict[str,int], errors: list[dict[str,Any]]) -> dict[str,Any]:
        try:
            if 'selection_jsonb_valid' in row.keys() and row['selection_jsonb_valid'] is not True:
                raise ValueError('Saved history is not valid PostgreSQL JSON')
            result = History._validated_status(row, counts, errors)
            if row['history_owner_id'] is None:
                raise ValueError('Saved history has no owning feed')
            if (result.get('run') or None) != (row['initial_history_run'] or None):
                raise ValueError('Saved history run does not match its owner')
            return result
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            raise InvalidHistoryError('Повреждена сохранённая история фида') from error

    @staticmethod
    def _validated_status(row: Row, counts: dict[str,int], errors: list[dict[str,Any]]) -> dict[str,Any]:
        if any(type(row[field]) is not int or row[field] not in (0, 1) for field in ('paused', 'frozen', 'scan_complete')):
            raise ValueError('Invalid saved history flags')
        if any(outcome not in ('pending','retrying','unavailable','loaded','incomplete','viewed') for outcome in counts):
            raise ValueError('Invalid saved history member outcome')
        result: dict[str, Any] = json.loads(row['selection'])
        if 'restore_unread' in result and type(result['restore_unread']) is not bool:
            raise ValueError('Invalid restore flag')
        result.pop('restore_unread',None)
        result.pop('date',None)
        result.pop('timezone',None)
        result['count_text'] = str(result['count']) if result['count'] is not None else None
        loaded = counts.get('loaded', 0) + counts.get('incomplete', 0)
        result.update(loaded=loaded, viewed=counts.get('viewed', 0), incomplete=counts.get('incomplete', 0), available=sum(counts.values()), paused=bool(row['paused']), frozen=bool(row['frozen'] and row['scan_complete']))
        result['retrying'] = counts.get('retrying', 0)
        result['unavailable'] = counts.get('unavailable', 0)
        result['errors'] = errors
        result['scan_complete'] = bool(row['scan_complete'])
        result['state'] = 'pending' if not row['frozen'] else 'loading' if counts.get('pending', 0) or counts.get('retrying', 0) or not row['scan_complete'] else 'done'
        if row['frozen'] and not result['scan_complete'] and result['mode'] == 'count':
            result['state'] = 'selecting'
        if row['paused'] and result['state'] != 'done':
            result['state'] = 'paused'
        elif result.get('waiting') and result['state'] != 'done':
            result['state'] = 'waiting'
        if result.get('cancelled'):
            result['state'] = 'cancelled'
        HistoryFacts.model_validate(result)
        if type(result['count']) is not int or result['count'] < 1 or len(str(result['count'])) > 4096:
            raise ValueError('Invalid saved history count')
        retry_at = result.get('discovery_retry_at')
        if retry_at:
            try:
                datetime.fromtimestamp(retry_at, timezone.utc)
            except (OverflowError, OSError, ValueError) as error:
                raise ValueError('Invalid saved history retry time') from error
        if result.get('stop_reason') not in (None, 'count_reached', 'date_boundary', 'no_continuation',
                'archive_end', 'request_failed', 'cycle', 'page_limit', 'ambiguous_continuation', 'cancelled'):
            raise ValueError('Invalid saved history stop reason')
        return result
