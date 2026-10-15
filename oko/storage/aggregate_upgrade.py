"""One-time removal of legacy multi-publication data, preserving accepted results."""
import hashlib
import json
import os
import shutil
from typing import TYPE_CHECKING, Any

from oko.processing.contracts import FilterResult, Theses

if TYPE_CHECKING:
    from oko.storage.store import Store


_REMOVED = dict(events=True, event_hours=24, digests=False,
                prompt_digest='Собери темы и основные тезисы за период.', schedule='off', digest_days=1)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _legacy_settings(store: 'Store', feed: str) -> dict[str, Any]:
    settings = store.subscriptions.effective('feed', feed)
    # Reconstruct the historical signature, including retired null metadata.
    settings['feed']['telegram_topic'] = None
    settings['effective'].update({k: dict(value=v, scope='default', owner=None) for k, v in _REMOVED.items()})
    for scope, owner in [('oko', 'global'), ('folder', settings['feed']['folder_id']), ('feed', feed)]:
        for row in store.db.execute('SELECT field,value FROM settings WHERE scope=? AND owner=?', (scope, owner)):
            if row['field'] in _REMOVED:
                settings['effective'][row['field']] = dict(value=json.loads(row['value']), scope=scope, owner=owner)
                if scope == 'feed':
                    settings['overrides'][row['field']] = json.loads(row['value'])
    return settings


def _relabel(store: 'Store', item: str, kind: str, old: str, new: str) -> None:
    table = 'filter_inputs' if kind == 'filter' else 'thesis_inputs'
    row = store.db.execute(f'SELECT signature FROM {table} WHERE item=?', (item,)).fetchone()
    state = store.db.execute('SELECT status FROM jobs WHERE kind=? AND item=?', (kind, item)).fetchone()
    if not row or row[0] not in (old, new) or not state or state[0] != 'done':
        return
    # YouTube can retain accepted caption theses in a previous metadata version.
    versions = store.db.execute('SELECT number FROM content_versions WHERE item=?', (item,)).fetchall()
    accepted = False
    for version in versions:
        path = store.versions.path(item, version['number']).parent / (kind + '.md')
        try:
            raw, text = path.read_text().split('\n\n', 1)
            header = json.loads(raw)
            if header['signature'] not in (old, new):
                continue
            (FilterResult if kind == 'filter' else Theses).model_validate(header['result'])
        except (OSError, ValueError, KeyError):
            continue
        header['signature'] = new
        pending = path.with_suffix('.upgrade')
        with pending.open('w') as out:
            out.write(json.dumps(header, ensure_ascii=False) + '\n\n' + text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(pending, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        store.fault('after_aggregate_result_relabel')
        accepted = True
    if accepted:
        table = 'filter_inputs' if kind == 'filter' else 'thesis_inputs'
        with store.db:
            store.db.execute(f'UPDATE {table} SET signature=? WHERE item=?', (new, item))
            store.db.execute('UPDATE execution_settings SET signature=? WHERE signature=? AND job IN (SELECT id FROM jobs WHERE kind=? AND item=?)', (new, old, kind, item))


def remove_aggregates(store: 'Store') -> None:
    db = store.db
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name IN ('event_inputs','digest_sets')").fetchone():
        return
    # Settings are retained until all relabels complete. Repeating after an
    # interruption accepts either old or new headers, never an unrelated result.
    for row in db.execute('SELECT * FROM items WHERE viewed=0 AND complete=1').fetchall():
        item = row['id']
        if not store.path(item).is_file():
            continue
        settings = _legacy_settings(store, row['feed_id'])
        config = store.processor_config()
        local = config.model_dump() if config else None
        old_filter = _hash([store.content_version(item), settings, store.settings_version(), local])
        current_filter = store.filters.signature(item)
        if current_filter:
            _relabel(store, item, 'filter', old_filter, current_filter)
        filtered = store.filters.read(item)
        current = store.processing.theses.signature(item)
        version = store.content_version(item)
        filter_input = filtered
        if settings['feed']['kind'] == 'youtube':
            raw, text = store.path(item).read_text().split('\n\n', 1)
            if json.loads(raw).get('captions') != 'available' or not store.subscriptions.youtube_theses(row['feed_id']):
                continue
            version = hashlib.sha256(text.encode()).hexdigest()
            filter_input = {}
            # Caption results remain stored while a filter is pending or hidden.
            # Migrate their freshness without changing their reading admission.
            values = store.subscriptions.effective('feed', row['feed_id'])['effective']
            current = _hash([version, {}, row['members'], store.settings_version(),
                             {k: v['value'] for k, v in values.items()},
                             settings['feed']['public_ai'], local])
        if current is None:
            continue
        old = _hash([version, filter_input, row['members'], store.settings_version(),
                     {k: v['value'] for k, v in settings['effective'].items()},
                     settings['feed']['public_ai'], local])
        _relabel(store, item, 'theses', old, current)
    for name in ('events', 'digests'):
        path = store.root / name
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    with db:
        db.execute("DELETE FROM execution_settings WHERE job IN (SELECT id FROM jobs WHERE kind IN ('events','digest') OR (kind='delete' AND (item LIKE 'event-%' OR item LIKE 'digest-%')))")
        db.execute("DELETE FROM jobs WHERE kind IN ('events','digest') OR (kind='delete' AND (item LIKE 'event-%' OR item LIKE 'digest-%'))")
        db.execute('DROP TABLE IF EXISTS event_inputs')
        db.execute('DROP TABLE IF EXISTS digest_sets')
        db.execute("DELETE FROM state WHERE key LIKE 'digest-tick:%'")
        db.executemany('DELETE FROM settings WHERE field=?', [(key,) for key in _REMOVED])
        row = db.execute("SELECT value FROM state WHERE key='processor:codex'").fetchone()
        if row:
            config_data = json.loads(row[0])
            for key in ('events', 'digest'):
                config_data.pop(key, None)
            db.execute("UPDATE state SET value=? WHERE key='processor:codex'", (json.dumps(config_data),))
