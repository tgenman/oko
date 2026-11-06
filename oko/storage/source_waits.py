"""Persist source-wide HTTP waits independently of individual Jobs."""
import json
from typing import Any

from oko.storage.database import Connection


class SourceWaits:
    def __init__(self, db: Connection) -> None:
        self.db = db

    def source_until(self, scope: str) -> float:
        row=self.db.execute('SELECT value FROM state WHERE key=%s',('source-http:'+scope,)).fetchone()
        return float(row[0]) if row else 0.

    def telegram_state(self, group: str) -> dict[str,Any]:
        row=self.db.execute('SELECT value FROM state WHERE key=%s',('telegram-http:'+group,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_source(self, scope: str, until: float) -> None:
        if not self.db.transaction_depth:
            raise RuntimeError('Source wait requires an owning transaction')
        self.db.execute('INSERT INTO state VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=excluded.value',
                        ('source-http:'+scope,str(until)))

    def save_telegram(self, group: str, until: float, strikes: int) -> None:
        if not self.db.transaction_depth:
            raise RuntimeError('Source wait requires an owning transaction')
        self.db.execute('INSERT INTO state VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=excluded.value',
                        ('telegram-http:'+group,json.dumps(dict(until=until,strikes=strikes))))
