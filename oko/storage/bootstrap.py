"""Choose the current pipeline only for a new/empty store under the instance lock."""
from pathlib import Path
import sqlite3


def prepare(root: Path) -> None:
    path=root/'state.sqlite'
    if path.exists():
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            current=db.execute("SELECT value FROM state WHERE key='publication-pipeline'").fetchone() if 'state' in tables else None
            if current and current[0]=='complete':
                return
            populated=any(db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone() for table in ('items','feeds') if table in tables)
        if populated:
            raise ValueError('Нужен офлайн-перенос: остановите прежний выпуск и выполните python -m oko.storage.migration SOURCE NEW_DIRECTORY. Исходное хранилище не переключено.')
    elif any((root/name).exists() for name in ('subscriptions.opml','feeds.opml','content','staging')):
        raise ValueError('Найдены старые файлы без SQLite; требуется проверяемый офлайн-перенос хранилища.')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT)')
        db.execute("INSERT INTO state VALUES ('publication-pipeline','complete') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
