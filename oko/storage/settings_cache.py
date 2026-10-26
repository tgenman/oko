"""One connection-owned settings snapshot with transactional in-memory writes."""
import json
from typing import Any

from oko.storage.database import Connection


class SettingsCache:
    _STATE_KEYS = frozenset(('processor:codex', 'processor:llamacpp'))

    def __init__(self, db: Connection) -> None:
        self.db = db
        self._values: dict[tuple[str, str], dict[str, Any]] = {}
        for row in db.execute('SELECT scope , owner , field , value FROM settings'):
            self._values.setdefault((row['scope'], row['owner']), {})[row['field']] = json.loads(row['value'])
        self._state = {str(row['key']): str(row['value']) for row in db.execute(
            "SELECT key , value FROM state WHERE key IN ( 'processor:codex' , 'processor:llamacpp' )")}
        self._feeds = {str(row['id']): dict(row) for row in db.execute(
            'SELECT id , kind , folder_id , importance , public_ai , transport FROM feeds')}

    def feed_values(self, identity: str) -> dict[str, Any]:
        with self.db.access_lock:
            if identity not in self._feeds:
                raise ValueError('Feed not found')
            result = dict(self._feeds[identity])
            result['public_ai'] = None if result['public_ai'] is None else bool(result['public_ai'])
            return result

    def refresh_feed(self, identity: str) -> None:
        """A configuration writer publishes its row inside the same transaction."""
        row = self.db.execute('SELECT id , kind , folder_id , importance , public_ai , transport FROM feeds WHERE id = %s', (identity,)).fetchone()
        value = dict(row) if row is not None else None
        previous = self._feeds.get(identity)
        if previous == value:
            return

        def undo() -> None:
            if previous is None:
                self._feeds.pop(identity, None)
            else:
                self._feeds[identity] = previous

        self.db.after_rollback(undo)
        if value is None:
            self._feeds.pop(identity, None)
        else:
            self._feeds[identity] = value

    def state_value(self, key: str) -> str | None:
        if key not in self._STATE_KEYS:
            raise ValueError('Unsupported settings key')
        with self.db.access_lock:
            return self._state.get(key)

    def set_state(self, key: str, value: str) -> None:
        previous = self.state_value(key)
        self.db.execute('INSERT INTO state VALUES ( %s , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value', (key, value))
        if previous == value:
            return

        def undo() -> None:
            if previous is None:
                self._state.pop(key, None)
            else:
                self._state[key] = previous

        self.db.after_rollback(undo)
        self._state[key] = value

    def values(self, scope: str, owner: str) -> dict[str, Any]:
        with self.db.access_lock:
            return dict(self._values.get((scope, owner), {}))

    def _publish(self, key: tuple[str, str], values: dict[str, Any]) -> None:
        previous = self._values.get(key)
        if previous == values or previous is None and not values:
            return

        def undo() -> None:
            if previous is None:
                self._values.pop(key, None)
            else:
                self._values[key] = previous

        self.db.after_rollback(undo)
        if values:
            self._values[key] = values
        else:
            self._values.pop(key, None)

    def patch(self, scope: str, owner: str, patch: dict[str, Any]) -> None:
        values = self.values(scope, owner)
        for field, value in patch.items():
            if value is None:
                self.db.execute('DELETE FROM settings WHERE scope = %s AND owner = %s AND field = %s', (scope, owner, field))
                values.pop(field, None)
            else:
                self.db.execute('INSERT INTO settings VALUES ( %s , %s , %s , %s ) ON CONFLICT ( scope , owner , field ) DO UPDATE SET value = excluded . value',
                                (scope, owner, field, json.dumps(value)))
                values[field] = value
        self._publish((scope, owner), values)

    def clear(self, scope: str, owner: str) -> None:
        self.db.execute('DELETE FROM settings WHERE scope = %s AND owner = %s', (scope, owner))
        self._publish((scope, owner), {})
