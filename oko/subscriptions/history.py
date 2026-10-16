"""Durable initial-history selection, independent from processing and reading."""
from datetime import date, datetime, time
import json
import sqlite3
import secrets
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from oko.collection.telegram.contracts import HistorySelection

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class InitialHistory(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    mode: Literal['count', 'date'] = 'count'
    count: int | None = Field(default=20, ge=1)
    date: str | None = None
    timezone: str = 'UTC'

    @field_validator('count', mode='before')
    @classmethod
    def exact_integer(cls, value: Any) -> Any:
        # Browsers transport integers beyond JSON's exact range as decimal text.
        if isinstance(value, str) and value.isascii() and value.isdecimal():
            return int(value)
        return value

    @model_validator(mode='after')
    def valid_boundary(self) -> 'InitialHistory':
        try:
            zone = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Неизвестный часовой пояс') from None
        if self.mode == 'count':
            if self.count is None or self.date is not None:
                raise ValueError('Укажите количество публикаций от 1')
        else:
            if self.count is not None or self.date is None:
                raise ValueError('Выберите дату вместо количества')
            try:
                day = date.fromisoformat(self.date)
            except ValueError:
                raise ValueError('Неверная дата') from None
            if day > datetime.now(zone).date():
                raise ValueError('Дата не может быть в будущем')
        return self

    def selection(self) -> dict[str, Any]:
        result = self.model_dump()
        result['since'] = datetime.combine(date.fromisoformat(self.date), time(), ZoneInfo(self.timezone)).timestamp() if self.date else None
        return result


class History:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        db.executescript('''
        CREATE TABLE IF NOT EXISTS initial_history (feed TEXT PRIMARY KEY, selection TEXT, frozen INTEGER DEFAULT 0, paused INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS history_members (feed TEXT, item TEXT, outcome TEXT DEFAULT 'pending', PRIMARY KEY(feed,item));
        ''')
        if 'scan_complete' not in {r['name'] for r in db.execute('PRAGMA table_info(initial_history)')}:
            db.execute('ALTER TABLE initial_history ADD COLUMN scan_complete INTEGER DEFAULT 1')

    def member(self, item: str) -> bool:
        return self.db.execute('SELECT 1 FROM history_members WHERE item=?', (item,)).fetchone() is not None

    def failed(self, item: str, unavailable: bool = False) -> None:
        self.db.execute("UPDATE history_members SET outcome=? WHERE item=? AND outcome NOT IN ('loaded','incomplete','viewed','unavailable')", ('unavailable' if unavailable else 'retrying', item))

    def warnings(self, feed: str, warnings: list[str]) -> None:
        row = self.db.execute('SELECT selection FROM initial_history WHERE feed=?', (feed,)).fetchone()
        if row:
            selection = json.loads(row['selection'])
            selection['warnings'] = warnings
            self.db.execute('UPDATE initial_history SET selection=? WHERE feed=?', (json.dumps(selection), feed))

    def telegram_state(self, feed: str, progress: HistorySelection) -> None:
        row = self.db.execute('SELECT selection FROM initial_history WHERE feed=?', (feed,)).fetchone()
        if not row:
            return
        selection = json.loads(row['selection'])
        selection.update(warnings=list(progress.warnings), discovery_error=progress.error,
                         discovery_retry_at=progress.retry_at)
        self.db.execute('UPDATE initial_history SET selection=? WHERE feed=?', (json.dumps(selection), feed))

    def discovery_state(self, feed: str, cursor: dict[str, Any]) -> None:
        row = self.db.execute('SELECT selection FROM initial_history WHERE feed=?', (feed,)).fetchone()
        if not row:
            return
        selection = json.loads(row['selection'])
        selection['warnings'] = cursor.get('warnings', selection.get('warnings', []))
        selection['discovery_error'] = cursor.get('history_error')
        selection['discovery_retry_at'] = cursor.get('history_retry_at')
        if 'youtube_history' in cursor:
            selection['waiting'] = cursor.get('history_waiting', False)
            selection['requests'] = cursor.get('history_requests', 0)
        self.db.execute('UPDATE initial_history SET selection=? WHERE feed=?', (json.dumps(selection), feed))

    def configure(self, feed: str, selection: InitialHistory) -> None:
        self.db.execute('INSERT INTO initial_history(feed,selection) VALUES (?,?)', (feed, json.dumps(selection.selection())))

    def start(self, feed: str, selection: InitialHistory) -> dict[str, Any]:
        current = self.status(feed)
        if current and current['state'] not in ('done', 'cancelled'):
            raise ValueError('Сначала остановите текущую загрузку истории')
        if selection.mode != 'count':
            raise ValueError('Догрузка YouTube поддерживает последние N видео')
        self.db.execute('DELETE FROM history_members WHERE feed=?', (feed,))
        value = dict(**selection.selection(), run=secrets.token_hex(16), resume=0)
        self.db.execute('''INSERT INTO initial_history(feed,selection) VALUES (?,?)
            ON CONFLICT(feed) DO UPDATE SET selection=excluded.selection,frozen=0,paused=0,scan_complete=0''',
            (feed, json.dumps(value)))
        result = self.status(feed)
        assert result is not None
        return result

    def cancel(self, feed: str) -> dict[str, Any]:
        current = self.status(feed)
        if current is None:
            raise ValueError('История не настроена')
        row = self.db.execute('SELECT selection FROM initial_history WHERE feed=?', (feed,)).fetchone()
        value = json.loads(row['selection'])
        value.update(cancelled=True, waiting=False)
        self.db.execute('UPDATE initial_history SET selection=?,paused=1 WHERE feed=?', (json.dumps(value), feed))
        self.db.execute("UPDATE jobs SET status='done',generation=generation+1 WHERE kind='fetch' AND item IN (SELECT item FROM history_members WHERE feed=?)", (feed,))
        result = self.status(feed)
        assert result is not None
        return result

    def freeze(self, feed: str, items: list[str]) -> None:
        row = self.db.execute('SELECT frozen FROM initial_history WHERE feed=?', (feed,)).fetchone()
        if not row or row['frozen']:
            return
        self.db.executemany('INSERT OR IGNORE INTO history_members(feed,item) VALUES (?,?)', ((feed, item) for item in items))
        self.db.execute('UPDATE initial_history SET frozen=1 WHERE feed=?', (feed,))

    def enroll(self, feed: str, items: list[str], complete: bool) -> None:
        self.db.executemany('INSERT OR IGNORE INTO history_members(feed,item) VALUES (?,?)', ((feed, item) for item in items))
        self.db.execute('UPDATE initial_history SET frozen=1,scan_complete=? WHERE feed=?', (complete, feed))

    def pause(self, feed: str, paused: bool) -> dict[str, Any]:
        status = self.status(feed)
        if status is None:
            raise ValueError('Начальная история не настроена')
        if status.get('cancelled'):
            raise ValueError('История остановлена; начните новый подбор')
        if not paused:
            row = self.db.execute('SELECT selection FROM initial_history WHERE feed=?', (feed,)).fetchone()
            selection = json.loads(row['selection'])
            selection.update(resume=selection.get('resume', 0) + 1, waiting=False, discovery_error=None)
            self.db.execute('UPDATE initial_history SET selection=? WHERE feed=?', (json.dumps(selection), feed))
        if status['state'] != 'done':
            self.db.execute('UPDATE initial_history SET paused=? WHERE feed=?', (paused, feed))
        result = self.status(feed)
        assert result is not None
        return result

    def saved(self, item: str, full: bool) -> None:
        self.db.execute("UPDATE history_members SET outcome=? WHERE item=?", ('loaded' if full else 'incomplete', item))

    def viewed(self, item: str) -> None:
        self.db.execute("UPDATE history_members SET outcome='viewed' WHERE item=? AND outcome IN ('pending','retrying')", (item,))

    def status(self, feed: str) -> dict[str, Any] | None:
        row = self.db.execute('SELECT * FROM initial_history WHERE feed=?', (feed,)).fetchone()
        if not row:
            return None
        result: dict[str, Any] = json.loads(row['selection'])
        result['count_text'] = str(result['count']) if result['count'] is not None else None
        counts = {r['outcome']: r['n'] for r in self.db.execute('SELECT outcome,count(*) n FROM history_members WHERE feed=? GROUP BY outcome', (feed,))}
        loaded = counts.get('loaded', 0) + counts.get('incomplete', 0)
        result.update(loaded=loaded, viewed=counts.get('viewed', 0), incomplete=counts.get('incomplete', 0), available=sum(counts.values()), paused=bool(row['paused']), frozen=bool(row['frozen'] and row['scan_complete']))
        result['retrying'] = counts.get('retrying', 0)
        result['unavailable'] = counts.get('unavailable', 0)
        result['errors'] = [dict(item=r['item'], message=r['error']) for r in self.db.execute("SELECT h.item,j.error FROM history_members h JOIN jobs j ON j.item=h.item AND j.kind='fetch' WHERE h.feed=? AND h.outcome IN ('retrying','unavailable') AND j.error IS NOT NULL", (feed,))]
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
