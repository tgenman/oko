"""Durable initial-history selection, independent from processing and reading."""
from datetime import date, datetime, time
import json
import sqlite3
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

    def configure(self, feed: str, selection: InitialHistory) -> None:
        self.db.execute('INSERT INTO initial_history(feed,selection) VALUES (?,?)', (feed, json.dumps(selection.selection())))

    def freeze(self, feed: str, items: list[str]) -> None:
        row = self.db.execute('SELECT frozen FROM initial_history WHERE feed=?', (feed,)).fetchone()
        if not row or row['frozen']:
            return
        self.db.executemany('INSERT OR IGNORE INTO history_members(feed,item) VALUES (?,?)', ((feed, item) for item in items))
        self.db.execute('UPDATE initial_history SET frozen=1 WHERE feed=?', (feed,))

    def saved(self, item: str, full: bool) -> None:
        self.db.execute("UPDATE history_members SET outcome=? WHERE item=?", ('loaded' if full else 'incomplete', item))

    def viewed(self, item: str) -> None:
        self.db.execute("UPDATE history_members SET outcome='viewed' WHERE item=? AND outcome='pending'", (item,))

    def status(self, feed: str) -> dict[str, Any] | None:
        row = self.db.execute('SELECT * FROM initial_history WHERE feed=?', (feed,)).fetchone()
        if not row:
            return None
        result: dict[str, Any] = json.loads(row['selection'])
        result['count_text'] = str(result['count']) if result['count'] is not None else None
        counts = {r['outcome']: r['n'] for r in self.db.execute('SELECT outcome,count(*) n FROM history_members WHERE feed=? GROUP BY outcome', (feed,))}
        loaded = counts.get('loaded', 0) + counts.get('incomplete', 0)
        result.update(loaded=loaded, viewed=counts.get('viewed', 0), incomplete=counts.get('incomplete', 0), available=sum(counts.values()), paused=bool(row['paused']), frozen=bool(row['frozen']))
        result['state'] = 'pending' if not row['frozen'] else 'loading' if counts.get('pending', 0) else 'done'
        return result
