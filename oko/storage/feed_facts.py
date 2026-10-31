"""The stored subscription fields shared by reader projections."""
import json
from typing import Any

from oko.storage.database import Row


_FIELDS = ('id', 'identity', 'title', 'kind', 'folder_id', 'original_urls',
           'connection', 'transport', 'importance', 'initial_days', 'public_ai',
           'read_history', 'auto_read', 'telegram_peer', 'telegram_account',
           'telegram_username')

READER_FEED_COLUMNS = (*_FIELDS, 'avatar_digest')


def feed_facts(row: Row) -> dict[str, Any]:
    """Select reader fields explicitly and normalize their saved values."""
    feed = {name: row[name] for name in _FIELDS}
    if isinstance(feed['original_urls'],str):
        feed['original_urls'] = json.loads(feed['original_urls'])
    for name in ('public_ai', 'read_history', 'auto_read'):
        feed[name] = None if feed[name] is None else bool(feed[name])
    return feed
