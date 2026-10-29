# Проверки реализации хранения — 29 октября 2026

База: `24a5607217d974ff3b06bab00397b513ce2142dc`, ветка `codex/storage-redesign`.
Проверки выполнялись на отдельном синтетическом PostgreSQL 18 в Docker;
личные данные и источники не использовались.

- SQL-блоки `CREATE` пяти документов физической схемы выполнены в транзакции
  временной схемы PostgreSQL и откатаны: создано 20 таблиц. Это не миграция и
  не проверка переноса данных, ограничений на реальных данных или приложения.
- Тест изменяющего CTE сначала упал с `AttributeError` на отсутствующем
  `execute_write`; после реализации прошёл.
- `./scripts/check python --files tests/reader/test_postgres_transactions.py`:
  contracts и 17 тестов прошли. Проверены commit, rollback и видимость из
  второго соединения; тесты выполнялись на прежней схеме.

Эти результаты не подтверждают готовность проекта: переход схемы, триггеры,
очереди, миграция, сценарии и независимое ревью ещё не завершены.
- Для read-only snapshot тесты сначала показали отсутствие интерфейса, затем
  отдельно — утечку commit callback в последующую транзакцию. После исправлений
  повтор `./scripts/check python --files tests/reader/test_postgres_transactions.py`
  прошёл: contracts и 19 тестов. Проверены согласованность снимка, запрет записи
  PostgreSQL и отсутствие отложенного callback.
- `python -m mypy oko tests/support/codex.py tests/support/app.py` прошёл для
  150 файлов до последнего изменения callback; итоговую проверку ещё повторить.
- Запущенный `./scripts/check python types` прошёл contracts и types, но Python
  наборы прерваны: checkout изменялся во время выполнения. Итоговый runner нужен
  на зафиксированном снимке после интеграции; этот запуск не считается успешным.
- `./scripts/check python --files tests/reader/test_storage_redesign_schema.py tests/reader/test_postgres_transactions.py`: contracts и 20 тестов прошли. Проверочный SQL создаёт 20 целевых таблиц без прикладных триггеров и откатывается; это не SQL миграции.
- `./scripts/check python --files tests/reader/test_storage_redesign_schema.py tests/reader/test_postgres_transactions.py`: contracts и 22 теста прошли на синтетическом PostgreSQL. Проверены сохранение `false`, сброс отдельного переопределения и видимость после commit/rollback с двух соединений. Новый компонент настроек пока не подключён к HTTP.
- Повтор `python -m mypy oko tests/support/codex.py tests/support/app.py`: 151 файл, успешно.
- Проверка native boolean сначала выявила `DatatypeMismatch`: адаптер передавал Python `True` как число. После исправления повтор двух целевых наборов прошёл: contracts и 23 теста.

- Settings overrides, feed options and processor configuration no longer use a
  Store-wide mutable cache. The settings GET uses a read-only repeatable-read
  snapshot; two-connection tests cover commit, rollback and a concurrent update
  during an HTTP response. Publication reads batch required settings in their
  request-local input scope.
- Initial targeted settings run: 29 passed, 2 failed because tests required
  the removed memory cache; those assumptions were replaced with observable
  committed-state and broken-connection checks. A later run found publication
  open costs of 10–11 SELECTs against the existing limit of 8 and was interrupted.
  After request-local batching, `./scripts/check python --files
  tests/reader/test_settings_memory.py tests/reader/test_v3_settings.py
  tests/reader/test_postgres_transactions.py tests/reader/test_material_read_cost.py
  tests/reader/test_dispatch_cost.py` passed 76 tests, including unchanged cost
  limits, on synthetic PostgreSQL. This does not establish 20,000-item performance.
- `./scripts/check browser --files tests/browser/test_settings_v3_http.ts
  tests/browser/test_settings_navigation.ts tests/browser/test_codex_settings_http.ts`:
  6 Playwright tests passed; inspected generated settings/status and model screenshots.
- Publication, cards and navigation GETs now use read-only snapshots. Separate
  concurrent HTTP tests first reproduced mixed parent/article content, a 500
  during card membership removal, and inconsistent navigation counts; all three
  pass after the change. The snapshot covers database reads, not later rendering.
- Publication/linked-content suites: 37 Python tests and 4 Playwright tests
  passed. Cards and pagination: 22 Python and 12 Playwright tests passed.
- Navigation initially exceeded its existing statement budget (13 versus 11),
  including BEGIN/COMMIT. Request-local reuse of Telegram bindings and one combined
  history counts/errors query restored the unchanged budget. The affected
  navigation/history suite passed 38 Python tests and 8 Playwright tests.
- Existing `test_reading_cost.py` passed 5 tests, including its 20,000-row archive
  index case on the old schema; it does not measure the target-schema migration.
