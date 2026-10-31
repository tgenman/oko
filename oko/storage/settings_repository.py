"""Settings reads and writes on the caller's database connection."""
import json
from typing import Any

from oko.storage.database import Connection
from oko.storage import material_read
from oko.storage.source_configuration import changed as source_changed
from oko.storage.settings_store import SettingsStore


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
            folder, feed = SettingsStore._owner(scope, owner)
            return {row['field']: row['value'] for row in self.db.execute(
                'SELECT field, value FROM settings WHERE folder_id IS NOT DISTINCT FROM %s AND feed_id IS NOT DISTINCT FROM %s', (folder, feed))}

    def patch(self, scope: str, owner: str, patch: dict[str, Any]) -> None:
        folder, feed = SettingsStore._owner(scope, owner)
        changed = False
        for field, value in patch.items():
            if value is None:
                changed = bool(self.db.execute('DELETE FROM settings WHERE folder_id IS NOT DISTINCT FROM %s AND feed_id IS NOT DISTINCT FROM %s AND field=%s', (folder,feed,field)).rowcount) or changed
            else:
                changed = bool(self.db.execute('''INSERT INTO settings(folder_id,feed_id,field,value) VALUES (%s,%s,%s,%s::jsonb)
                    ON CONFLICT ON CONSTRAINT settings_one_value DO UPDATE SET value=excluded.value
                    WHERE settings.value IS DISTINCT FROM excluded.value''', (folder,feed,field,json.dumps(value))).rowcount) or changed
        if changed:
            source_changed(self.db)

    def clear(self, scope: str, owner: str) -> None:
        folder, feed = SettingsStore._owner(scope, owner)
        if self.db.execute('DELETE FROM settings WHERE folder_id IS NOT DISTINCT FROM %s AND feed_id IS NOT DISTINCT FROM %s', (folder,feed)).rowcount:
            source_changed(self.db)
