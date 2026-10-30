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
- Migration 015 removes `reader_action_superseded` and its three triggers and
  functions after checking that the projection agrees with receipt history.
  Cleanup now walks the `(completed, sequence)` index in bounded pages and checks
  for a newer sequence by identity; the Store cursor rolls back on transaction
  failure and restarts from the beginning after a process restart.
- Synthetic 5000-receipt tests verify bounded reads and the DELETE plan, latest
  receipt retention, eventual cleanup and cursor rollback. Receipt/problem
  retention no longer runs in GET status; expired problems are filtered from the
  response and local housekeeping removes them even when work is disabled.
  `Problems.reconcile` still performs other writes during GET and remains to move.
- The affected migration, transaction, problem, receipt and query-cost suites passed
  113 Python tests; 11 Playwright status/navigation scenarios passed.
- Catalog measurement on an empty database migrated through version 15: 33 tables
  and 129 non-internal triggers. The target remains 20 tables and zero triggers.
- Migration 016 removes three time-derived reader tables and their seven
  triggers/functions. Conditional responses now account for pending job deadlines,
  YouTube pause expiry and problem retention directly, using one clock snapshot
  per request. A partial job index bounds the deadline lookup.
- Four HTTP tests cover time transitions without background writes. The first
  affected run passed 125 tests and failed two ETag tests while startup recovery
  was still creating Jobs. The two steady-state tests were corrected to wait for
  that independently tested recovery; the affected HTTP suites then passed 21
  tests. The initial run also passed the migration, transaction and 20,000-job
  archive checks. Types and 10 Playwright scenarios passed; the YouTube settings
  screenshot was inspected.
- Catalog measurement after version 16: 30 tables and 122 non-internal triggers.
  Remaining projections, direct event-driven scheduling and the target physical
  schema are unfinished; status reconciliation still writes during GET.
- Migration 017 moves outstanding Telegram read-sync markers into indexed
  `telegram_read_state.needs_sync`, removes the old table and six triggers, and
  preserves pending markers, including feeds not yet observed. Python marks
  affected feeds within the same transactions as collection, media, viewing and
  membership changes. A new marker during a running pass survives its completion.
- The first migration test failed before the new column existed; after the
  migration the affected Telegram, web, migration, CLI and transaction suites
  passed 115 tests. A new shared-owner deletion test found a missed marker and
  passed after the fix; the shared-publication, startup and bounded-background
  suites then passed 26 tests. Types and 8 Playwright scenarios passed; a
  synthetic Telegram publication screenshot was inspected.
- Catalog measurement after version 17: 29 tables and 116 non-internal triggers.
  The current read-sync pass still has the legacy saved-content scan and the
  target separate saved/viewed eligibility facts remain unfinished.
- Migration 018 separates saved and viewed Telegram read reasons, preserving
  legacy eligible rows and refusing unknown eligibility values or reasons.
  Recording a saved message no longer overwrites an explicit viewed reason;
  disabling auto-read still admits explicitly viewed messages. The existing
  status projection trigger is adapted and remains transitional.
- The focused regression first failed because the independent columns did not
  exist. After implementation, 102 Python tests and type checks passed, followed
  by four migration checks for valid and malformed legacy facts. One Playwright
  Telegram access scenario passed and its screenshot was inspected. The legacy
  saved-content scan still remains and must be replaced by event-owned facts.
- Collection and media acceptance now record a full Telegram publication's
  saved reason in the same transaction even when auto-read is off. Linked
  publication changes re-evaluate their parents; an incomplete media download
  does not create the fact. Unchanged reasons do not schedule another pass, and
  repeated discovery checks only new or changed memberships/publications.
- Tests initially exposed both the missing saved fact with auto-read off and an
  unnecessary signal on an unchanged fact. The affected collection, Telegram,
  shared-publication and transaction suites passed 57 tests after the fixes;
  a separate media failure/retry test passed, as did two Playwright scenarios.
  The partial-download screenshot was inspected. Legacy-data backfill and removal
  of the old feed scan remain unfinished.
- Migration 019 schedules a resumable saved-fact backfill for existing Telegram
  client feeds; switching from web to client schedules it as well. Each commit
  advances at most eight membership keys with its saved facts; a new Store resumes
  the cursor, and read RPC admission waits until it completes. Normal read sync
  no longer scans publication membership for the whole feed. This cursor is a
  transitional column and still needs alignment with the final physical schema.
- The no-scan test failed before the old loop was removed. A first broad run
  passed 150 Python tests but failed a source-configuration cost assertion; the
  pending lookup was simplified and the affected cost and read-sync tests passed.
  Separate tests cover interruption, a real Store restart, migration scheduling,
  and withholding external calls while backfill is incomplete. Types and two
  Playwright scenarios passed; a synthetic client-publication screenshot was
  inspected. The full redesign and its independent review remain outstanding.
- Migration 020 removes the unused reader-list and inherited-originals
  projections and six related triggers/functions. The current publication HTTP
  response derives the inherited setting directly; changing the global value and
  adding a feed override both change the response and ETag.
- The initial affected run passed 123 tests and exposed four failures in an old
  synthetic fixture passing a boolean as an integer, plus an ETag test racing
  startup recovery. After fixing the fixture and isolating steady-state testing,
  both affected suites passed 25 tests. Types and 8 Playwright settings, tree and
  reading scenarios passed; a settings screenshot was inspected.
- Catalog measurement after version 20: 27 tables and 110 non-internal triggers.
  Remaining work includes direct processing Jobs, first-started feed selection,
  remaining reader projections and the final schema, checks and review.
- Migration 021 removes persisted feed/folder counts and membership-dirty work.
  Navigation derives counts from membership and visibility in one read snapshot;
  a publication shared by two feeds in one folder counts once for the folder.
  Twelve triggers and their functions were removed; the remaining visibility and
  processing effects retain their current owners.
- The affected run passed 160 tests including reading and database work budgets,
  and exposed three failures: one concurrency test waited for the removed SQL,
  while status performed per-feed settings reads and per-problem inserts. The
  synchronization point was updated, settings and new problem writes were
  batched, and 30 affected tests including query budgets and concurrent snapshots
  passed. Types and 13 Playwright scenarios passed; a screenshot was inspected.
  Status GET still writes problem records and must be moved to event owners.
- Catalog measurement after version 21: 24 tables and 98 non-internal triggers.
  Full implementation, final target schema and independent review are outstanding.
- Migration 022 moves hiddenness from `reader_visibility` to publication status,
  preserving current visibility and rejecting invalid legacy values. A current
  hidden filter takes precedence over a new filter attempt; a current summary
  remains ready during regeneration. Pending Jobs remain eligible for those
  explicit retries even when a saved result determines the publication status.
- The regression initially showed hidden status becoming `pending_filtering`.
  An initial broad run passed 175 tests but exposed two retries blocked by the
  old status-based dispatch condition. After adjusting dispatch, the affected
  tests and a broader 151-test processing, migration and cost run passed. Types
  and 10 Playwright scenarios passed; the filter settings screenshot was inspected.
- Catalog measurement after version 22: 23 tables and 97 non-internal triggers.
  Dirty processing queues, first-started selection, reader versions, final schema,
  complete checks and independent review remain unfinished.
- Acceptance of changed publication content now reconciles its filter and theses
  Jobs in the same transaction, including when background processing is enabled.
  A rollback test verifies the content and Job revision revert together; a late
  adapter response leaves the already-created replacement generation untouched.
  This is only one event path: the dirty tables and other event owners remain.
- The first affected run passed 76 tests and found an old assertion expecting the
  replacement Job to appear later. The test now checks that the new pending
  generation exists before the late response and remains unchanged afterwards;
  the complete 33-test worker-transaction suite passed. Types and three Playwright
  processing/linked-publication scenarios passed; a screenshot was inspected.
- A changed linked article now reconciles each parent publication's Jobs in the
  same transaction. A focused test first exposed the missing replacement and now
  verifies both generation change and rollback. The linked, worker, processing
  and reader suites passed 75 tests, types and two browser scenarios passed; the
  synthetic linked RSS screenshot was inspected. Other dirty-queue producers remain.
- An attempted synchronous per-publication reconciliation on global settings
  changes passed 113 functional/cost tests but failed three slow-recovery tests:
  two HTTP requests timed out and a simulated recovery failure became a 500 on
  the settings request. That implementation was reverted; all six startup and
  responsiveness tests then passed. Settings still use the transitional dirty
  queue, and their atomic, responsive replacement needs a bounded design.
- Complete publications accepted from a discovery page now receive their
  processing Jobs before the page transaction commits. A regression test first
  demonstrated the missing Job and now checks both commit and rollback. The
  affected worker, collection, startup and reading/cost suites passed 108 tests;
  types and two Playwright scenarios passed, and a screenshot was inspected.
- A previously saved processing feed now supplies settings, stage switches and
  worker ownership even when the display's primary feed differs. A regression
  test first showed the display feed taking precedence; it now covers independent
  rules, changes to the other feed and Store restart. The affected suites passed
  98 tests, types and two Playwright scenarios; a screenshot was inspected.
  The initial candidate race and atomic selection by the first successful claim
  are not implemented yet, so this is not completion of the first-started rule.
- The shared-publication path now prepares possible first filter and summary
  Jobs from participating feeds. The winning claim locks the publication and
  saves the selected feed with the claim; the other stage is blocked if needed
  later or cancelled if not applicable. Rollback removes the choice, and restart
  keeps it. Candidate admission considers public-AI permission and configured
  processor; unavailable candidates do not establish a feed, and a later
  configuration change can admit them.
- Removing the selected feed now revokes its unfinished generation and clears
  the context; removing another membership leaves an independent running Job
  intact. Late responses from the removed feed are rejected. Outdated saved
  filter/summary results and provenance remain stored but are not treated as
  current after rules change.
- Regressions first showed missing candidate Jobs, an inadmissible choice,
  incorrect competing-stage states and overwritten old results. The final
  affected run passed 151 Python tests and types; four Playwright scenarios
  passed and a shared-publication screenshot was inspected. The dirty queues,
  full event-driven settings path, all-trigger removal, final schema and
  independent project review remain unfinished.
- Migration 023 removes the filter-Job change trigger and function. The Python
  Job owner records dependent work in the same transaction for claim, outcome,
  retry, reconciliation and cancellation. Tests cover rollback for claim,
  finish, retry, cancel and block. The legacy dirty queue remains in use.
- The affected migration, worker, processing, recovery and background-cost suites
  passed 181 tests; types and two Playwright scenarios passed, and a screenshot
  was inspected. The synthetic version-23 catalog has 23 tables and 96
  non-internal triggers. Remaining event paths and the target schema are open.
- Migration 024 removes the two publication-to-processing dirty triggers. Page
  and material acceptance already reconcile affected publications through their
  Python owners; membership changes still have transitional dirty triggers.
  The affected migration, worker, collection, linked/shared, reader and startup
  suites passed 207 tests; types and four browser scenarios passed. A linked
  publication screenshot was inspected. Version 24 has 23 tables and 94 triggers.
- Migration 025 removes three membership-effect triggers/functions. The page,
  viewed, feed removal and cleanup owners record affected reader versions and
  transitional processing signals in their transaction, batching page effects.
  A rollback test covers membership, status version and signal together.
- The first broad run passed 213 tests and found one historical migration test
  expecting a removed trigger after all upgrades. That test now pins the old
  migration it documents; 163 affected tests then passed. Types and five
  Playwright scenarios passed, and a shared-publication screenshot was inspected.
  Version 25 has 23 tables and 91 non-internal triggers; dirty queues remain.
- Migration 026 removes 15 triggers/functions that only updated obsolete feed,
  material, filter, summary or media resource versions. The current reader does
  not consume those versions; the unused batch-version accessor was removed.
  Card and status version mechanisms remain in place for now.
- The affected migration, processing, status and reading suites passed 224
  Python tests; types and 12 Playwright scenarios passed, and a feed-details
  screenshot was inspected. Version 26 has 23 tables and 76 non-internal
  triggers. Remaining dirty queues, status GET writes and final review are open.
- Migration 027 replaces the card's Job-version dependency with validators
  derived from its own row, selected feed, fetch/media deadlines and linked
  publication rows. Nine Job triggers/functions were removed. A new regression
  exposed a stale 304 when a media retry became due; it now changes the ETag
  without a background write. Linked content changes also invalidate the card
  without a parent-version write, and card/media use one clock snapshot.
- The migration, reading and query-cost suites passed 165 tests; three focused
  clock/link tests and six Playwright scenarios passed, and a screenshot was
  inspected. Version 27 has 23 tables and 67 non-internal triggers. Status
  versions, dirty queues and GET problem reconciliation remain unfinished.
- Migration 028 removes three settings-to-source-revision triggers. The Python
  settings repository advances the monotonic revision in the same transaction
  only for actual changes. Tests cover no-op, reset, A → B → A and rollback.
- The affected migration, settings, processing, Telegram and recovery suites
  passed 229 tests; types and five Playwright scenarios passed, and a settings
  screenshot was inspected. Version 28 has 23 tables and 64 triggers. Other
  source-configuration triggers and the remaining project work are still open.
- Migration 029 moves feed source-revision changes to the Python mutation
  owners, including creation, source settings, access change and deletion.
  Rename alone does not advance the revision; source changes, A → B → A and
  rollback are tested. Three feed triggers/functions were removed.
- The affected migration, settings, Telegram, shared-feed, worker and recovery
  suites passed 246 tests; types and eight Playwright scenarios passed, and a
  Telegram access screenshot was inspected. Version 29 has 23 tables and 61
  triggers. Other revision owners and the complete redesign remain unfinished.
