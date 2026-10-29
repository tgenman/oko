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
- The `filter_enabled` switch is exposed in the OpenAPI settings contract and
  built-in editor. HTTP tests cover inherited false, reset, prompt preservation,
  and rejection/rollback of enabled filtering with an empty effective prompt.
  The previous closed settings response schema rejects the added field; strict
  external clients must update, as recorded in `api/README.md`.
- On the current schema, synthetic worker/HTTP tests cover disabling either
  stage before a call, accepting an in-flight success, retaining ready results,
  blocking an in-flight failure with its error visible, re-enabling, and restarting
  an interrupted disabled filter. High-importance YouTube now respects explicit
  `theses=false`. These are transitional semantics; they do not establish removal
  of the dirty tables or triggers, nor first-started-feed ownership.
- An earlier processing run failed the old YouTube test that expected importance
  to override explicit `false`; the scenario now tests enabled processing and a
  separate HTTP test covers explicit disable. After the failure-visibility fix,
  `./scripts/check python --files tests/reader/test_postgres_processing.py
  tests/reader/test_worker_transactions.py tests/reader/test_v3_settings.py
  tests/reader/test_youtube_publications.py tests/reader/test_v3_problems.py
  tests/reader/test_status_problems.py tests/reader/test_material_read_cost.py
  tests/api/test_contract.py` passed 114 tests on synthetic PostgreSQL.
- `./scripts/check browser --files tests/browser/test_publication_processing_http.ts
  tests/browser/test_status_v3_http.ts tests/browser/test_settings_v3_http.ts`
  passed 9 Playwright tests. The filter switch screenshot was inspected. Mypy
  passed 151 files; `npm run api:check` passed; settings-owner Node suite passed
  109 tests. An earlier sandboxed Node attempt could not inspect processes and
  did not run; the authorized retry passed.
- Migration 007 moves the existing UUID from `reader_identity` into `state`
  as a JSON string and removes the old table. Tests cover an existing UUID,
  a conflicting value, an invalid UUID, rollback and restart. Restore tests
  cover legacy `reader_identity`, text `state`, and JSONB `state`, using separate
  synthetic databases; restore creates a new identity and disables work.
- Migration/transaction/HTTP/backup suites passed 62 Python tests. A separate
  startup/browser/CLI run passed 19 tests and failed one stale CLI expectation
  (`version 6` after migration 7); after correcting the expectation, the affected
  4-test CLI suite passed. The PostgreSQL browser screenshot was inspected.
- A fresh schema migrated through version 7 has 43 tables and 149 application
  triggers, measured from the PostgreSQL catalog. This is an intermediate result,
  not the target of 20 tables and zero application triggers.
- Migration 008 moves Telegram avatar bytes, MIME and digest into feeds and
  drops `telegram_avatars` and its three trigger functions. Preflight rejects
  orphaned, oversized, invalid-format or digest-mismatched avatars atomically.
  Python updates feed/status reader versions in the same transaction as an avatar
  change; the navigation path selects the digest without loading avatar bytes.
- The avatar/migration/Telegram/navigation/transaction and existing publication
  cost suites passed 85 Python tests. The production PostgreSQL browser test and
  the avatar Playwright scenario passed; their screenshots were inspected.
  An initial test accidentally counted matching triggers in all test schemas;
  restricting the catalog query to the current schema fixed the test.
- Catalog measurement on an empty database migrated through version 8: 42 tables
  and 146 non-internal triggers. The other application triggers and target schema
  transition remain unfinished.
- Migration 009 moves collection attempts to `jobs.feed_id` and a transitional
  JSONB attempt field, and per-feed results to `feeds.last_collection_result`.
  It rejects orphaned jobs/feeds, invalid JSON, non-object data and unexpected job
  kinds without dropping source tables. The two collection cleanup triggers and
  their functions are removed; feed deletion clears retained job diagnostics in
  Python. A partial index bounds lookup of retained attempts by feed.
- The first HTTP retry/error run found JSONB booleans returned as `true`/`false`
  instead of legacy `0`/`1`; after correction the affected tests passed. Full
  collection/migration/retention/metrics suites passed 55 Python tests, including
  existing summary/detail limits up to 1000 synthetic feeds. Two collection-status
  Playwright tests passed; desktop and mobile screenshots were inspected.
- Catalog measurement on an empty database migrated through version 9: 40 tables
  and 144 non-internal triggers. `collection_attempt` is transitional; the final
  target still requires the designed job fields and trigger-free schema.
- Migration 010 moves `feed_details` into `feeds` and removes the former table.
  The target schema now keeps `description` nullable: NULL means not observed,
  while an empty string means the source explicitly supplied an empty description.
  Tests cover both cases, detail-only values, conflicting copies, orphan records
  and non-finite timestamps; ambiguous data aborts the transaction.
- The affected feed/collection/migration/Telegram suites passed 66 Python tests;
  after adding migration edge cases, the 31-test migration suite passed again.
  Shared publications, folder management, pipeline and production-PostgreSQL
  browser checks passed 17 tests; 6 Playwright feed/collection scenarios passed.
  Desktop/mobile collection and PostgreSQL reader screenshots were inspected.
- Catalog measurement on an empty database migrated through version 10: 39
  tables and 144 non-internal triggers. Times remain double precision in this
  transitional schema; the final design still requires timestamptz conversion.
- Migration 011 moves source-deletion markers into `items.source_deleted`,
  rejects orphan markers and removes the old table plus three triggers/functions.
  Python writes the marker and reader freshness in the same transaction. Existing
  transitional views expose the new column; they remain scheduled for removal.
- The first affected Python run passed 131 tests and failed one fixture that copied
  the new boolean through a legacy integer adapter. Fresh synthetic publications
  now use the column default; the affected 24-test reading/query-cost suite passed.
  Tests also cover orphan rollback, HTTP deletion/recovery and conditional GETs.
  Four Playwright linked-publication/unavailable-source scenarios passed.
- Catalog measurement on an empty database migrated through version 11: 38 tables
  and 141 non-internal triggers. The application and target schema are not complete.
- Migration 012 moves execution provenance from `execution_settings` into a
  JSONB job field and removes the table. It rejects orphaned, incomplete or
  malformed snapshots atomically. The transitional JSON holds the captured
  generation, signature and actual parameters; final job conversion remains.
- Synthetic tests show an interrupted attempt's recorded parameters remain
  available, while a subsequent attempt uses the current Codex model after either
  a failure or restart. The original affected run had 127 passes and one failure:
  an old test expected the previous model choice to be reused. That assertion was
  updated to the agreed future-attempt rule; the complete 32-test worker
  transaction suite then passed. Three Playwright model/processing scenarios
  passed and the model settings screenshot was inspected.
- Catalog measurement on an empty database migrated through version 12: 37 tables
  and 141 non-internal triggers. Schema replacement and trigger removal continue.
- Migration 013 moves filter/thesis signatures from `filter_inputs` and
  `thesis_inputs` into `jobs.input_snapshot`, removes both tables and three
  filter-input triggers/functions, and rejects missing publications/jobs,
  missing signatures and invalid/non-object job payloads. The existing views,
  dirty queues and other reader projections remain transitional.
- A started processing Job now records its generation, signature, selected feed,
  rules, permission, no-input fact and source IDs/versions in the claim transaction;
  it does not copy publication text. A rollback test confirms the claim and
  snapshot revert together. Explicit Python updates reader freshness when filter
  signatures change. Sparse backfill retains a partial index and passes the
  existing 20,000-completed-job archive check.
- The affected migration, worker, Codex, processing, review and background-cost
  suites passed 134 Python tests; 2 Playwright processing/reader scenarios passed.
  The initial synthetic application run failed on the removed table before its
  consumers were moved; it passed after the implementation was connected.
- Catalog measurement on an empty database migrated through version 13: 35 tables
  and 138 non-internal triggers. Full target schema, direct event-driven Job
  scheduling and first-started-feed selection remain unfinished.
- Migration 014 replaces `reader_material_links` with GIN indexes over the
  authoritative journal objects and removes its six triggers/functions. It
  rejects malformed bodies, unsupported object shapes, invalid material IDs,
  and any mismatch between saved records and the old projection before dropping it.
- The affected migration, receipt, problem, viewing and query-cost suites passed
  101 Python tests, including the existing 5000-unrelated-receipts bound; 7
  Playwright status/reader scenarios passed. An initial HTTP viewing check failed
  before its consumer was moved and passed after the indexed lookup was connected.
- Catalog measurement on an empty database migrated through version 14: 34 tables
  and 132 non-internal triggers. Superseded-receipt cleanup and the remaining
  reader projections are not yet moved.
