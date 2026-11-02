# Проверка удаления тестов Telegram-клиента

База: `b2274d7fccb65f192821156db63d59155a4470f8`.

- `./scripts/check python --files tests/api/test_telegram_contract.py tests/api/test_v3_schema.py tests/reader/test_postgres_migrations.py tests/reader/test_startup_recovery.py tests/reader/test_telegram_bindings.py tests/reader/test_telegram_web.py tests/reader/test_create_folder_move.py tests/reader/test_navigation_status.py` — PASS, 41 тест на временной PostgreSQL.
- `./scripts/check browser --browser-workers 2 --files tests/browser/test_separate_menus.ts tests/browser/test_status_v3_http.ts tests/browser/test_telegram_connection.ts tests/browser/test_telegram_v3_http.ts tests/browser/test_completion.ts tests/browser/test_news_context_menu.ts` — PASS, 24 сценария; browser-types и tooling-types — PASS.
- Поиск по тестовому коду клиентских профилей, аккаунтов, Telethon, sync_reads, read_sync и auto_read — совпадений нет. HTML-фикстуры публичного веба и общие тесты веб-адаптера сохранены.
- По явному запросу удалены также проверки отказа клиентского API, старого staging и безопасности миграции; соответствующее автоматическое покрытие отсутствует. Полный набор не запускался.
- Отдельный визуальный Playwright-сценарий после трёх отказов на устаревших URL/селекторах и тексте приведён к текущему интерфейсу: повтор — PASS, 13 состояний. Скриншот формы Telegram просмотрен.
