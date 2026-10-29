"""Settings reads and writes on the caller's database connection."""
import json
from typing import Any

from oko.storage.database import Connection
from oko.storage import material_read


class SettingsRepository:
    _STATE_KEYS = frozenset(('processor:codex', 'processor:llamacpp'))

    def __init__(self, db: Connection) -> None:
        self.db = db

    def feed_values(self, identity: str) -> dict[str, Any]:
        with self.db.access_lock:
            inputs = material_read.current(self.db)
            row = inputs.feeds.get(identity) if inputs and identity in inputs.feeds else self.db.execute('SELECT id, kind, folder_id, importance, public_ai, transport FROM feeds WHERE id = %s', (identity,)).fetchone()
            if row is None:
                raise ValueError('Feed not found')
            result = {key: row[key] for key in ('id', 'kind', 'folder_id', 'importance', 'public_ai', 'transport')}
            result['public_ai'] = None if result['public_ai'] is None else bool(result['public_ai'])
            return result

    def state_value(self, key: str) -> str | None:
        if key not in self._STATE_KEYS:
            raise ValueError('Unsupported settings key')
        with self.db.access_lock:
            return material_read.state(self.db, key)

    def set_state(self, key: str, value: str) -> None:
        if key not in self._STATE_KEYS:
            raise ValueError('Unsupported settings key')
        self.db.execute('INSERT INTO state VALUES ( %s , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value', (key, value))

    def values(self, scope: str, owner: str) -> dict[str, Any]:
        with self.db.access_lock:
            inputs = material_read.current(self.db)
            if inputs is not None and (scope, owner) in inputs.settings:
                return dict(inputs.settings[(scope, owner)])
            return {row['field']: json.loads(row['value']) for row in self.db.execute(
                'SELECT field, value FROM settings WHERE scope = %s AND owner = %s', (scope, owner))}

    def patch(self, scope: str, owner: str, patch: dict[str, Any]) -> None:
        for field, value in patch.items():
            if value is None:
                self.db.execute('DELETE FROM settings WHERE scope = %s AND owner = %s AND field = %s', (scope, owner, field))
            else:
                self.db.execute('INSERT INTO settings VALUES ( %s , %s , %s , %s ) ON CONFLICT ( scope , owner , field ) DO UPDATE SET value = excluded . value',
                                (scope, owner, field, json.dumps(value)))

    def clear(self, scope: str, owner: str) -> None:
        self.db.execute('DELETE FROM settings WHERE scope = %s AND owner = %s', (scope, owner))
