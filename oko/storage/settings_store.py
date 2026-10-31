"""Overrides in the target settings schema; transaction ownership stays with the caller."""
import json
from typing import Any, Literal

from oko.storage.database import Database


class SettingsStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _owner(scope: str, owner: str) -> tuple[str | None, str | None]:
        if scope == 'oko' and owner == 'global':
            return None, None
        if scope == 'folder' and owner:
            return owner, None
        if scope == 'feed' and owner:
            return None, owner
        raise ValueError('Invalid settings scope or owner')

    def values(self, scope: Literal['oko', 'folder', 'feed'], owner: str) -> dict[str, Any]:
        folder, feed = self._owner(scope, owner)
        return {row[0]: row[1] for row in self.db.execute(
            'SELECT field, value FROM settings WHERE folder_id IS NOT DISTINCT FROM %s AND feed_id IS NOT DISTINCT FROM %s',
            (folder, feed))}

    def patch(self, scope: Literal['oko', 'folder', 'feed'], owner: str, patch: dict[str, Any]) -> None:
        folder, feed = self._owner(scope, owner)
        for field, value in patch.items():
            if value is None:
                self.db.execute('DELETE FROM settings WHERE folder_id IS NOT DISTINCT FROM %s AND feed_id IS NOT DISTINCT FROM %s AND field = %s', (folder, feed, field))
            else:
                self.db.execute('''INSERT INTO settings(folder_id, feed_id, field, value)
                    VALUES (%s, %s, %s, %s::jsonb) ON CONFLICT ON CONSTRAINT settings_one_value
                    DO UPDATE SET value = excluded.value''', (folder, feed, field, json.dumps(value)))
