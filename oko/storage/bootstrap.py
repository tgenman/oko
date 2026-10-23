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
                if 'telegram_bindings' in tables and db.execute("SELECT 1 FROM telegram_bindings WHERE json_extract(binding,'$.kind') NOT IN ('web','client') LIMIT 1").fetchone():
                    raise ValueError('Неподтверждённый старый доступ Telegram. Настройте профиль в прежнем выпуске 9ae1b50.')
                if 'items' in tables and db.execute('SELECT 1 FROM items WHERE platform IS NULL LIMIT 1').fetchone():
                    raise ValueError('В базе остались публикации старого формата. Завершите перенос прежним выпуском 9ae1b50.')
                if 'feeds' in tables and db.execute("SELECT 1 FROM feeds WHERE kind='telegram' AND json_extract(config,'$.source_kind') IN ('group','supergroup') LIMIT 1").fetchone():
                    raise ValueError('Группы Telegram пока не поддерживаются; используйте прежний выпуск для доступа к сохранённой группе.')
                if 'initial_history' in tables and db.execute("SELECT 1 FROM initial_history WHERE json_extract(selection,'$.mode') != 'count' LIMIT 1").fetchone():
                    raise ValueError('Выбор истории по дате больше не поддерживается. Настройте выбор по количеству в прежнем выпуске 9ae1b50.')
                return
            populated=any(db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone() for table in ('items','feeds') if table in tables)
        if populated:
            raise ValueError('Нужен офлайн-перенос: остановите прежний выпуск и выполните python -m oko.storage.migration SOURCE NEW_DIRECTORY в прежнем выпуске 9ae1b50. Текущий выпуск старое хранение не поддерживает.')
    elif any((root/name).exists() for name in ('subscriptions.opml','feeds.opml','content','staging')):
        raise ValueError('Найдены старые файлы без SQLite; требуется проверяемый офлайн-перенос хранилища.')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT)')
        db.execute("INSERT INTO state VALUES ('publication-pipeline','complete') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
