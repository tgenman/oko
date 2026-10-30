"""Durable initial-history selection, independent from processing and reading."""
from oko.storage.database import expect_row, Database, Row
from oko.storage.source_configuration import changed as source_changed
from datetime import date, datetime, time
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


class History:
    def __init__(self, db: Database) -> None:
        self.db = db
        pass

    def member(self, item: str) -> bool:
        return self.db.execute('SELECT 1 FROM history_members WHERE item = %s', (item,)).fetchone() is not None

    def failed(self, item: str, unavailable: bool = False) -> None:
        self.db.execute("UPDATE history_members SET outcome = %s WHERE item = %s AND outcome NOT IN ( 'loaded' , 'incomplete' , 'viewed' , 'unavailable' )", ('unavailable' if unavailable else 'retrying', item))

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
        self.db.execute('INSERT INTO initial_history ( feed , selection ) VALUES ( %s , %s )', (feed, json.dumps(dict(selection.selection(),run=secrets.token_hex(16)))))
        source_changed(self.db)

    def start(self, feed: str, selection: InitialHistory) -> dict[str, Any]:
        current = self.status(feed)
        if current and current['state'] not in ('done', 'cancelled'):
            raise ValueError('Сначала остановите текущую загрузку истории')
        self.db.execute('DELETE FROM history_members WHERE feed = %s', (feed,))
        value = dict(**selection.selection(), run=secrets.token_hex(16), resume=0)
        self.db.execute('INSERT INTO initial_history ( feed , selection ) VALUES ( %s , %s ) ON CONFLICT ( feed ) DO UPDATE SET selection = excluded . selection , frozen = 0 , paused = 0 , scan_complete = 0',
            (feed, json.dumps(value)))
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
        self.db.executemany('INSERT INTO history_members ( feed , item ) VALUES ( %s , %s ) ON CONFLICT DO NOTHING', ((feed, item) for item in items))
        self.db.execute('UPDATE initial_history SET frozen = 1 WHERE feed = %s', (feed,))
        source_changed(self.db)

    def enroll(self, feed: str, items: list[str], complete: bool) -> None:
        self.db.executemany('INSERT INTO history_members ( feed , item ) VALUES ( %s , %s ) ON CONFLICT DO NOTHING', ((feed, item) for item in items))
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
        self.db.execute('UPDATE history_members SET outcome = %s WHERE item = %s', ('loaded' if full else 'incomplete', item))

    def viewed(self, item: str) -> None:
        self.db.execute("UPDATE history_members SET outcome = 'viewed' WHERE item = %s AND outcome IN ( 'pending' , 'retrying' )", (item,))

    def status(self, feed: str) -> dict[str, Any] | None:
        row = self.db.execute('SELECT * FROM initial_history WHERE feed = %s', (feed,)).fetchone()
        if not row:
            return None
        counts = {r['outcome']: r['n'] for r in self.db.execute('SELECT outcome , count ( * ) n FROM history_members WHERE feed = %s GROUP BY outcome', (feed,))}
        errors = [dict(item=r['item'],message=r['error']) for r in self.db.execute("SELECT h . item , ( SELECT j . error FROM jobs j WHERE j . item = h . item AND j . kind = 'fetch' ) AS error FROM history_members h WHERE h . feed = %s AND h . outcome IN ( 'retrying' , 'unavailable' )",(feed,)) if r['error'] is not None]
        return self._status(row,counts,errors)

    def statuses(self, feeds: list[str]) -> dict[str,dict[str,Any]]:
        if not feeds:
            return {}
        selected=json.dumps(feeds)
        rows=self.db.execute('SELECT * FROM initial_history WHERE feed IN ( SELECT value FROM oko_json_each ( %s ) )',(selected,)).fetchall()
        if not rows:
            return {}
        counts: dict[str,dict[str,int]]={}
        errors: dict[str,list[dict[str,Any]]]={}
        for row in self.db.execute("""SELECT h.feed, h.outcome, count(*) AS n,
            jsonb_agg(jsonb_build_object('item',h.item,'message',j.error))
              FILTER (WHERE h.outcome IN ('retrying','unavailable') AND j.error IS NOT NULL) AS errors
            FROM history_members h LEFT JOIN jobs j ON j.item=h.item AND j.kind='fetch'
            WHERE h.feed IN (SELECT value FROM oko_json_each(%s)) GROUP BY h.feed,h.outcome""", (selected,)):
            counts.setdefault(row['feed'],{})[row['outcome']]=row['n']
            errors.setdefault(row['feed'],[]).extend(row['errors'] or [])
        return {row['feed']:self._status(row,counts.get(row['feed'],{}),errors.get(row['feed'],[])) for row in rows}

    @staticmethod
    def _status(row: Row, counts: dict[str,int], errors: list[dict[str,Any]]) -> dict[str,Any]:
        result: dict[str, Any] = json.loads(row['selection'])
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
        return result
