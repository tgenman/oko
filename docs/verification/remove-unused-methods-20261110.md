# Проверка удаления неиспользуемых методов

Удалены шесть методов без вызовов внутри репозитория и связанные объявления протоколов. Действующие сбор, история, транзакции и миграции сохранены; запрет изменяющих CTE остаётся безусловным.

- Поиск по исходникам, тестам и скриптам и независимое статическое ревью не обнаружили вызовов удалённых методов. Возможные внешние потребители внутренних Python-методов не проверены.
- `./scripts/check types lint`: успешно.
- `./scripts/check python --files tests/reader/test_postgres_transactions.py tests/reader/test_initial_history.py tests/reader/test_incomplete_event_transition_external.py tests/reader/test_external_telegram_media.py tests/reader/test_worker_transactions.py --compact`: 74 passed, 0 skipped; синтетическая PostgreSQL.
- Независимое ревью также проверило отклонение изменяющих CTE и приём CTE для чтения.
