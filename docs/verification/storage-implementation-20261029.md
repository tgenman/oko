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
- Migration 030 moves Telegram binding/profile source-revision changes to their
  Python owner. No-op binding and repeated identity checks do not advance the
  revision; actual identity changes, deletion, rollback and restart are tested.
  Six triggers/functions were removed.
- The affected migration, Telegram, worker and recovery suites passed 233
  Python tests; types and eight Playwright scenarios passed, and a mobile
  settings screenshot was inspected. Version 30 has 23 tables and 55 triggers.
  History revision, remaining projections and the full redesign are still open.
- Migration 031 moves history-selection and Telegram initial-boundary revision
  updates into their Python owners. Progress-only writes do not advance the
  revision; selection, pause/resume, initial boundary and rollback are covered.
  Four triggers/functions were removed.
- The affected history, Telegram, migration and recovery suites passed 186
  tests; types and six Playwright scenarios passed. A history-outcomes screenshot
  was added and inspected. Version 31 has 23 tables and 51 triggers; dirty queues,
  status projections and final project verification remain open.
- Migration 032 removes three settings-to-status triggers. The status ETag now
  includes the transaction-owned source revision, so a settings change is
  reflected without an extra status-version write.
- The affected status, settings, migration, cost and recovery suites passed 166
  tests; types and ten Playwright scenarios passed, and a status screenshot was
  inspected. Version 32 has 23 tables and 48 triggers. GET reconciliation of
  problem records and other status version triggers remain to move.
- Migration 033 removes six Telegram binding/profile status triggers. Their
  observable changes already advance the Python-owned source revision included
  in the status ETag; the separate profile-message trigger remains.
- The affected migration, Telegram, status and recovery suites passed 197
  tests; types and ten Playwright scenarios passed, and a synthetic Telegram
  screenshot was inspected. Version 33 has 23 tables and 42 triggers. The
  remaining status, membership and collection triggers and dirty queues remain.
- Migration 034 removes three history-to-status triggers. The status validator
  reads the saved history row versions directly, including progress changes that
  intentionally do not advance the source-configuration revision.
- The migration, history, status, cost and recovery suites passed 140 tests;
  types and seven Playwright scenarios passed, and the history screenshot was
  inspected. Version 34 has 23 tables and 39 triggers. Status GET writes,
  remaining status triggers and both processing dirty queues are still open.
- Migration 035 moves history-member status freshness to the Python owners of
  insert, outcome change, viewing and cleanup. Duplicate inserts and unchanged
  outcomes do not create a new signal; an outcome and its status signal roll
  back together. Three triggers/functions were removed.
- The affected migration, history, Telegram and recovery suites passed 222
  tests; types and eight Playwright scenarios passed, and the history screenshot
  was inspected. Version 35 has 23 tables and 36 triggers. Status GET writes and
  processing queues still require the remaining implementation.
- Migration 036 moves Telegram read-state and member status signals into their
  Python owners. Inserts, changed eligibility, confirmed boundaries, missing
  messages, errors and deletion update status in the owning transaction; no-op
  and rollback behavior are covered. Six triggers/functions were removed.
- The affected Telegram, status, migration and recovery suites passed 238 tests;
  types and ten Playwright scenarios passed, and a Telegram access screenshot was
  inspected. Version 36 has 23 tables and 30 triggers. The remaining status
  owners, processing dirty queues and final review are still open.
- Migration 037 removes three folder reader-version triggers. Navigation reads
  folders directly; a folder rename changes navigation but does not invalidate
  an unchanged runtime status. Startup no longer seeds unused per-resource
  reader versions across the library.
- The affected run passed 188 tests and exposed one stale fixture that mutated a
  Telegram profile through SQL after its trigger had been removed. The fixture
  now uses the profile owner and the targeted test passed. Types and eight
  Playwright scenarios passed; the feed-tree screenshot was inspected. Version
  37 has 23 tables and 27 triggers; final implementation remains open.
- Migration 038 removes three feed reader-version triggers. Source changes use
  the owned source revision; title changes update status freshness in the feed
  owner without invalidating source configuration. Navigation reads live facts.
- The affected migration, feed, status, Telegram and recovery suites passed 202
  tests; types and 14 Playwright scenarios passed, and a compact feed-details
  screenshot was inspected. Version 38 has 23 tables and 24 triggers. Both
  dirty queues, status reconciliation and final project verification remain open.
- Migration 039 removes three YouTube status triggers. The status validator reads
  the saved admission deadline and strike count directly, and still changes at
  pause expiry without a background write.
- The affected migration, YouTube, status and worker suites passed 222 tests;
  types and seven Playwright scenarios passed, and a YouTube settings screenshot
  was inspected. Version 39 has 23 tables and 21 triggers; dirty queues and status
  GET reconciliation remain unfinished.
- Migrations 040–041 move Telegram profile-message freshness to its Python owner
  and derive status freshness from saved work-enabled, source-status and Codex
  state. Four triggers/functions were removed; message changes do not alter the
  source revision. The affected status, settings, Telegram and recovery suites
  passed 228 tests; types and 11 Playwright scenarios passed, with a model-settings
  screenshot inspected. Version 41 has 23 tables and 17 triggers. The remaining
  Job, item, journal and membership triggers and both dirty queues remain open.
- Migration 042 moves primary membership selection and replacement to Python,
  serialized by a transaction-scoped graph lock. It rejects pre-existing
  memberships without exactly one primary. Synthetic fixtures now supply that
  state explicitly; archived membership flags continue to match viewed state.
- The affected broad run passed 216 tests and failed one new migration assertion
  because it counted other test schemas; after fixing the assertion and lock
  ordering for source removal, the targeted repeat passed 154 tests and types.
  A first run without the test database URL failed at setup. Four Playwright
  scenarios passed and the shared-publication screenshot was inspected. A fresh
  version 42 schema has 23 tables and 15 non-internal triggers. Processing dirty
  queues, status reconciliation, remaining triggers and final review remain open.
- Migration 043 moves the transitional membership viewed flag to Python and
  repairs it from publication state, including legacy NULL values. The first
  affected run found PostgreSQL boolean adaptation errors (186 passed, 7 failed);
  after correction the repeat passed 193 tests and types. A targeted legacy NULL
  migration check and three Playwright scenarios also passed; the viewed shared
  publication screenshot was inspected. The flag itself remains transitional.
- Migration 044 removes two queue-time triggers. Collection Job owners now set
  queue time on creation or a transition back to pending, retaining it on repeated
  admission; existing timestamps survive migration. Affected recovery, collection,
  shared-publication and migration suites passed 194 tests; targeted enqueue,
  retry and rollback checks passed after removing unrelated SQL changes. Types and
  eight Playwright scenarios passed; a source-collection screenshot was inspected.
  A fresh schema has 23 tables and 12 non-internal triggers. The remaining status
  owners, transitional tables, final schema and full review remain open.
- Migration 045 moves action receipt status freshness to the Python owner,
  including superseded receipt cleanup. Duplicate starts do not invalidate status
  and rollback restores both the receipt and its status version. The affected
  journal, HTTP status and transaction suites passed 145 tests, types passed, and
  16 Playwright scenarios passed; the status panel screenshot was inspected.
  Problem, publication and Job status triggers remain, as do the transitional
  queues and final schema work.
- Migration 046 moves problem-record status freshness to its Python owner and
  batches the status bump for reconciliation. Creation, changes, resolution,
  cleanup, duplicate snapshots and rollback retain their previous semantics.
  The affected journal, status, transaction and load suites passed 154 tests;
  types and 16 Playwright scenarios passed, with a current-status screenshot
  inspected. Publication and Job status triggers and the transitional queues remain.
- Migration 047 removes the three publication status triggers. Publication owners
  update the status version when relevant facts change; transaction rollback and
  repeated unchanged viewed transitions are covered. Per-publication trigger
  versions have no remaining reader. The affected suites passed 225 tests and
  types; nine Playwright scenarios passed, and the viewed shared-publication
  screenshot was inspected. Job status triggers, dirty queues, the final schema
  and full review remain open.
- Migration 048 removes the last three Job status triggers. Explicit Job writes
  update status freshness in the same transaction; repeated unchanged fetch
  admission and unchanged recovery deadlines avoid a false status update. The
  broad lifecycle and SQL-cost run passed 233 tests. An added no-op test exposed
  a false invalidation; after correction, 97 lifecycle and recovery tests passed.
  Types and nine Playwright scenarios passed; the collection screenshot was
  inspected. The fresh version 48 schema has 23 tables and zero non-internal
  triggers. This is not completion: dirty queues, the target physical schema,
  problem reconciliation, full validation and independent review remain open.
- Migration 049 removes `reader_entities`, discards unread per-resource versions
  and carries the existing status revision and timestamp into one transitional
  `state` value. It fails rather than overwriting an existing key. This value
  still needs the target direct status ETag; moving it is not the final status
  design. Migration, status, settings, recovery and SQL-cost suites passed 248
  tests; types and nine Playwright scenarios passed, with the status screenshot
  inspected. The fresh schema has 22 tables and zero non-internal triggers.
- Accepted publications now reconcile filter and theses Jobs directly, without
  writing a temporary stage marker; existing queued stage markers are still
  consumed by the same transaction. Targeted worker, shared-publication and
  transaction suites passed 99 tests and types; processing-idle and recovery
  suites passed 26 tests. Two Playwright scenarios passed and the retained
  result after failed regeneration screenshot was inspected. Settings and
  recovery still use both transitional dirty tables and remain unfinished.
- Migration 050 removes four obsolete SQL clock/max functions. PostgreSQL's
  native expressions preserve the old nullable Telegram confirmation behavior
  and media retry deadlines. Migration, Telegram, media and CLI suites passed
  195 tests and types; two Playwright scenarios passed, and a media screenshot
  was inspected. Four JSON compatibility helpers remain installed.
- Migration 051 replaces the four JSON compatibility functions with native
  PostgreSQL JSON operators and array parameters, and removes the functions.
  The affected migration, history, Telegram, YouTube, status, media and worker
  suites passed 314 tests and types; eight Playwright scenarios passed and the
  Telegram publication screenshot was inspected. A fresh schema has 22 tables,
  zero non-internal triggers and zero schema-local functions. The two dirty
  tables and remaining physical/model changes are still unfinished.
- Migration 052 converts `settings` to nullable folder/feed foreign keys and
  JSONB, with a single NULLS NOT DISTINCT uniqueness constraint. It preserves
  false and empty strings, rejects orphan owners, JSON null and invalid JSON,
  and rolls back without modifying legacy values. Inheritance, settings, feed
  cleanup, recovery and migration suites passed 265 tests and types; two invalid
  value migration cases and 14 SQL-cost checks also passed. Nine Playwright
  settings/feed scenarios passed and the filter switch screenshot was inspected.
  The dirty processing queues and duplicate feed configuration remain open.
- Migration 053 removes the unused feed-level filter/summary JSON copies only
  when both are empty. Nonempty values and malformed JSON stop migration without
  changing the old data, requiring reconciliation instead of guessing which
  settings win. Migration and inheritance suites passed 164 tests and types;
  eight Playwright scenarios passed and feed details were inspected. The two
  processing dirty queues and the rest of the physical schema remain open.
- Migration 054 replaces the per-stage dirty table with publication-owned
  reconciliation of both Jobs in one transaction. Pending stage markers are
  copied to the remaining publication queue before the table is dropped; the
  workers no longer scan a separate stage queue. Legacy attempt revision backfill
  precedes startup reconciliation so terminal failures are retained.
- The first broad run was interrupted after exposing a missing transaction
  boundary; a second passed 231 tests and exposed three legacy terminal-outcome
  regressions. After fixing recovery ordering, all 234 affected tests passed;
  after removing the obsolete global candidate API, 186 targeted tests and types
  passed again. Four Playwright scenarios passed and the saved result screenshot
  was inspected. A fresh schema has 21 tables, no application triggers and no
  schema-local functions. The remaining `processing_dirty` queue and final
  physical schema still require work.
- Filter Job claims and outcomes now reconcile dependent theses and publication
  state inside the same transaction without adding a publication queue marker.
  An existing broader publication marker still takes precedence. Tests cover an
  obsolete running theses attempt being revoked together with filter claim and
  restored on rollback. The affected processing and recovery suites passed 126
  tests and types; two Playwright scenarios passed and the hidden publication
  screenshot was inspected. Settings, membership and media paths still use the
  remaining publication dirty queue.
- Collection, linked-parent and subtitles acceptance/failure/retry now reconcile
  affected processing Jobs directly; they no longer enqueue and immediately
  consume a publication marker. Collection and linked-parent tests verify both
  atomic Job changes and absence of a transient queue insert. The affected
  media, YouTube, processing and recovery suites passed 169 tests and types;
  four Playwright scenarios passed and the ready-captions screenshot was inspected.
  Membership changes, settings and startup still use the publication dirty queue.
- Discovery membership additions now reconcile affected Jobs directly without
  writing a temporary publication marker. A regression checks Job creation and
  rollback in the same transaction and verifies that no marker was inserted.
  The affected worker, shared-publication, Telegram and YouTube suites passed
  116 tests and types; two Playwright scenarios passed and the shared publication
  screenshot was inspected. Membership removal and settings still use the queue.
- Membership removal now reconciles surviving shared owners directly and revokes
  own filter/theses Jobs when the last membership disappears. A viewed article
  kept as another publication's dependency retains its source content while its
  own results and context are cleared; rollback restores both membership and Job.
  The first broad run passed 155 tests and failed the slow-database reader timeout;
  after avoiding an unnecessary metadata write, the isolated regression and a
  172-test processing, reading, completion and recovery run passed. Fourteen
  Playwright scenarios passed and linked RSS content was inspected. Settings,
  startup and repair paths still use the remaining dirty queue.
- Removed the now-unused option that enqueued membership changes. Three targeted
  discovery, shared-owner removal and linked-content rollback checks passed;
  all membership callers now reconcile their own consequences directly.
- Startup recovery now walks unviewed publication IDs directly in bounded
  transactions rather than seeding `processing_dirty`. Migration 055 adds a
  partial ID index; the tested plan on 20,000 viewed and 400 unviewed synthetic
  rows uses it. A first run failed because PostgreSQL could not infer the type
  of a null cursor; the corrected keyset query passed 10 focused recovery cases,
  then 207 recovery, background-cost, processing and migration tests plus types.
  Seven Playwright scenarios passed and the mobile status screenshot was inspected.
  Settings invalidation and the transitional repair path still use the queue.
- The deferred in-memory repair mechanism now reconciles one publication
  directly instead of adding a database marker. A new rollback test exposed that
  the old implementation lost its in-memory request on rollback; the corrected
  path retains it. Startup/rollback checks passed eight tests, and the affected
  processing, recovery and YouTube suites passed 93 tests; types and three
  Playwright scenarios passed, with the unavailable-captions screenshot inspected.
  This tests the repair mechanism directly; the current code has no caller that
  schedules it from a read, so end-to-end read-detected repair remains open.
- Settings writes now compare each running AI attempt's stage-specific signature
  with its actual current input and revoke a changed generation in the settings
  transaction. A -> B -> A and rollback are covered; a late cancellation-resistant
  model response is rejected over HTTP and a replacement is run. Disabling a stage,
  changing only summary rules or an overridden ancestor rule does not cancel an
  unchanged filter attempt; display-only settings do not interrupt collection.
  Acceptance still checks the owned Job generation and the stage signature.
- The first broad run passed 175 tests and found one prior test expecting a restart
  for unchanged input plus three legacy-schema test queries. Those assertions were
  updated to the agreed unchanged-input behavior and new settings schema. The full
  affected repeat passed 181 tests and types; seven focused transaction/HTTP checks
  passed after removing the unused affected-feed result. Three Playwright scenarios
  passed and the filter-switch screenshot was inspected. The settings dirty queue
  remains and this does not finish the target schema.
- Display-only `originals` changes no longer enqueue processing or alter a filter
  Job. The targeted regression first failed on the old queue insertion; after the
  change, 48 settings and transaction tests passed, plus 28 Playwright scenarios.
  The switched open-material screenshot was inspected.
- Settings changes now reconcile affected Jobs and publication statuses in one
  transaction using batched reads and writes. A linked source's AI permission
  change also includes its parents. A sequential first attempt took 13.79 seconds
  through HTTP on 400 synthetic publications and failed the two-second check;
  the batched version passes that check, including a bound below 100 SQL commands.
  A separate local transaction measurement took 0.138 seconds and 47 SQL commands
  for 400 publications; the two measurements have different boundaries.
- The first broad batch run passed 194 tests and found one obsolete test that
  expected settings to use background recovery. That test now injects a real
  startup recovery failure. A repeat passed 300 tests and types. A further run
  passed 138 tests and found two stale test-helper calls to the removed stage
  queue; after updating the helper, 48 targeted read/HTTP tests passed. Four
  Playwright scenarios passed and the saved-result screenshot was inspected.
- A legacy version 55 synthetic database with pending settings work was restarted:
  its marker remained until recovery, which updated the Job and consumed the
  marker. Auto-review rejected the proposed unguarded removal of `processing_dirty`
  because preservation of outstanding work was not proven; no removal was applied.
  The table and its consumers remain in place pending a separately reviewed safe
  cutover; no claim of the target table count is made here.
- Migration 056 renames the existing `items` relation to `publications` in place;
  a migration test verifies rows and dependent views survive. The full Python
  run passed 1,215 tests and skipped two, with nine failures. Seven failures were
  stale schema/trace fixtures or service-layer SQL; the SQL was moved into Store
  and all seven targeted checks passed. Two review-scenario failures concern
  retained versus stale processing results and remain open for the next change.
  Types passed; three Playwright scenarios passed and the viewed shared-publication
  screenshot was inspected. The guarded queue-removal draft was not installed:
  auto-review rejected removal of its remaining consumers as potentially losing
  pending work, so `processing_dirty` remains in the active schema.
- Confirmed content changes now discard results whose saved source-block hash no
  longer matches, atomically with the new input and Job; rollback restores them.
  Changes to settings alone and selected-feed replacement retain saved results,
  while their signatures decide whether they are current. Disabled stages retain
  and reuse still-valid results. The two review scenarios that failed the full run
  now pass under these agreed semantics. Processing, linked inputs, YouTube,
  recovery and source-deletion suites passed 139 tests and types; five Playwright
  scenarios passed, with the failed-regeneration screenshot inspected.
- A ready subtitle whose local file is missing now schedules an in-memory
  reconciliation from both point and batched reads, without writing in the
  read-only snapshot. A repair already in progress cannot enqueue itself again,
  and rollback retains the request. The focused test first failed because no
  read path scheduled repair; after the fix 124 processing, YouTube, read-cost
  and recovery tests passed with types. Three Playwright scenarios passed and
  the missing-captions screenshot was inspected. This supersedes the earlier
  note that read-detected repair had no caller.
- Reachability cleanup now walks only the undirected component containing the
  changed publications, then retains the part reachable from its remaining feed
  roots. Disconnected orphaned content is no longer cleared by an unrelated view.
  The first component query still caused PostgreSQL to scan 5,003 rows with a
  5,000-publication foreign graph; indexed lateral lookups reduced the observed
  scan to four rows in the corresponding synthetic measurement. Tests also cover
  a retained root and cycles. The broad run passed 160 tests and found one stale
  query-trace matcher; after updating it, the one-pass and two graph-cost cases
  passed. Fourteen Playwright scenarios passed and a linked Telegram screenshot
  was inspected. Larger connected components and concurrent graph changes remain
  part of the final acceptance/review.
- Processing settings, relevant feed changes and processor configuration now take
  the transaction-scoped graph lock before their writes and affected-graph reads.
  Two-connection tests prove a settings write waits for an in-flight membership
  commit and then includes the new owner. Processor changes no longer interrupt
  unrelated collection, and changing only the model for future work does not
  cancel a running filter. The initial focused tests exposed missing locks and an
  unrelated fetch cancellation; after correction the affected suites passed 200
  tests and types. Five Playwright scenarios passed and the model-settings
  screenshot was inspected. Queue and source-ID cutovers remain blocked by the
  separate auto-review rejections recorded above.
- Delayed discovery continuation now stores an absolute wall-clock deadline in
  the existing retry field and reconstructs the process-local deadline on restart.
  A synthetic test with changed monotonic and wall clocks first exposed the
  previously lost delay, then passed. The affected collection, history, Telegram,
  YouTube and recovery suites passed 78 tests and types; three Playwright scenarios
  passed, and the mobile collection-status screenshot was inspected. The final
  `next_run_at` physical schema remains to be implemented.
- A discovery page, its finished Job and a delayed continuation now commit in the
  same transaction. A fault after continuation scheduling first exposed the old
  gap; the corrected path rolls back the page and continuation together. The
  affected worker, history, Telegram, YouTube and recovery suites passed 150 tests
  and types; two Playwright scenarios passed, and a Telegram screenshot was inspected.
- Discovery now takes the graph lock before resolving publication identities,
  and refuses membership in a deleting feed. The first lock-order and lifecycle
  tests exposed both omissions; after correction the affected collection and
  dispatch run passed 157 tests. Resolution is performed in page order so a
  linked article that also appears as a feed publication shares one row in either
  order. The source-local identity is retained and repaired for an existing
  link placeholder; rollback and restart are covered. A subsequent affected run
  passed 183 tests and types; four focused lock/identity cases passed after the
  legacy-placeholder correction. Three Playwright cases passed and linked RSS
  content was inspected.
- A separate 50-publication discovery transaction under the graph lock measured
  1.841 seconds and 5,158 SQL statements on the local synthetic database. A proposed
  broad batching rewrite was rejected by auto-review because job/result safety was
  insufficiently established. The failing cost regression is retained as a draft
  under `.scratch/storage-redesign`; no batching rewrite was applied. This measured
  lock duration and SQL cost remain an open performance issue.
- Migration 057 adds the agreed publication and Job status constraints. It
  rejects unknown or null legacy Job states without changing saved data; valid
  states survive. The affected migration, processing, history and recovery
  suites passed 281 tests and types; three Playwright scenarios passed and the
  collection status screenshot was inspected.
- Migration 058 constrains Job operation names and nonnegative attempt,
  generation and retry-delay counters. Unknown, null or negative legacy values
  stop migration without rewriting them. The relevant migration, worker, history,
  deletion and recovery suites passed 298 tests and types; three Playwright cases
  passed and the collection screenshot was inspected.

### Follow-up checks on 30 October

The following are focused checks against synthetic PostgreSQL and temporary
files, not evidence of deployment or a passing full suite:

- Media attempts use distinct final keys. A retry no longer overwrites an
  uncommitted file; 44 media and YouTube tests passed on the redesign branch.
- Processor configuration and processing reconciliation now share a
  transaction. The rollback scenario and 64 settings/transaction tests passed.
- A confirmed title change invalidates old results, while a YouTube metadata
  change keeps results based on unchanged captions. Processing and YouTube:
  98 tests passed; rollback is covered.
- Linked ownership and completion changes reconcile parent Jobs atomically.
  The 99-test affected run passed, followed by 20 focused cases after batching
  parent lookup. The privacy change and its rollback were exercised.
- Missing subtitles are marked pending and re-enqueued with their owner; restart
  before and after repair and rollback were checked. The affected run passed
  119 tests; the focused restart cases and an HTTP re-download case also passed.
- Changing feed processing rules or folder no longer cancels collection. The
  affected run passed 119 tests; 38 cases passed after refining unchanged fields.
- Persisted media keys are constrained to the managed media directory. A
  synthetic HTTP request for an outside file returned 404, and 52 media/reading
  tests passed. This is not a claim about adversarial filesystem races.
- Discovery/fetch outcomes and successful media outcomes are committed with the
  accepted result. Failures and their diagnostics share a transaction; fault
  tests cover rollback and a stopped process. Affected collection runs passed
  119 tests, and media runs passed 50, 29 and 38 tests for their respective
  snapshots. The release-manifest fixture was updated to schema 58; 48 release
  tests passed.
- Changing only the summary prompt leaves the completed filter Job unchanged;
  157 processing/settings tests and the focused post-reconciliation case passed.

The full run started on commit `9594f1c9` separately; its final result is not
included here until the run completes. Queue cutover, source identity cutover,
review and publication remain unresolved as described above.

The full run at `9594f1c9` ended unsuccessfully: HTTP/build/types, 116 Node
and 315 tooling tests passed; Playwright reported 215 passed, 3 skipped and 35
failures after an HTTP fixture preparation timed out. The first timed-out browser
case passed in an isolated repeat (20.27 seconds); this does not establish the
cause of the timeout under parallel load. Python reported 943 passed, 2 skipped
and two failures in tests that incorrectly used a media ID as a filename; those
files were corrected and their complete targeted suites passed (15 and 11).
The remaining Python shard was terminated after identifying its indefinite
wait: a power test expected processor configuration to close Telegram, which
is no longer its behavior. The test now uses a real cancellation action and a
bounded wait; all six power tests passed. The full run did not verify the newer
fixes and must be repeated.

The next full run at `e8ebcdc9` finished with 1,270 Python tests passed, two
skipped and one failed: the collection worker directly accessed `Store.db` to
register a completion callback, violating the layer boundary. The callback now
passes through JobRepository; 11 affected boundary, rollback and crash cases
passed, and mypy passed. Node (116), tooling (315), build, types and HTTP passed.
Playwright reported 214 passed, three skipped and 36 failed: 35 followed a
fixture preparation timeout and one independent feed-details case failed. The
first timed-out case and the independent feed-details case each passed in
isolated repeats; neither repeat proves the full concurrent browser run. A new
browser-only run with two workers is in progress and is not reported as passed.

A browser-only repeat with two workers completed 254 tests successfully and
skipped three, but the runner reported `checkout_changed`: documentation and a
scratch draft were written while it ran. Therefore the browser test results are
useful evidence, but the command as a whole did not pass; repeat on a stable
checkout. This does not erase the failed concurrent full-browser run.

The full run at `2e6a0953` with two browser workers passed 254 browser tests
(three skipped), 116 Node tests, 315 tooling tests, build, HTTP, and type checks.
Python had 1,270 passed, two skipped and one failure: a clock test advanced both
session expiry and shutdown to the same deadline, so it could observe either 410
or the stopped-runtime 409. The test now separates those deadlines; its seven
lifecycle tests passed and the individual case passed eight consecutive repeats.
The full run preceded subsequent application changes and is not a final pass.

For the missing Codex configuration problem, settings/configuration writes now
persist or resolve that problem within their transaction. A focused HTTP test
observed no SQL writes from status for this scenario, including clearing it;
rollback and restart were checked. The isolated prototype passed 143 related
settings/transaction/worker tests, 24 status/settings tests, and the 400-item
settings cost case. After transfer to the branch three focused tests, layer
boundaries and mypy passed. Status GET can still write other problem categories;
this does not establish the target read-only status contract as complete.
The targeted Playwright status, settings and Codex HTTP suites subsequently
passed 10 tests with two workers. The status screenshot showing the missing
Codex model was inspected. Other problem categories remain outside this slice.

Command-result problems are now created, updated and resolved alongside their
individual command outcome. Pending commands from a previous process are
recovered in bounded batches; a rollback restores the in-memory pending marker.
Synthetic HTTP checks cover failure, successful retry and restart without status
GET writes for these cases. The affected status, command, settings, archive and
transaction suites passed 80 tests, followed by three focused recovery/rollback
cases after batching. Mypy passed. Ten Playwright cases passed with two workers;
the mobile status screenshot was inspected. Other problem producers still need
migration, so status GET as a whole is not yet read-only.

Telegram profile problems now follow profile checks, verified configuration,
connection/access changes, feed deletion and startup recovery. A synthetic HTTP
case verifies the profile problem record does not change during status reads;
rollback, restoration, restart and two feeds sharing one profile were checked.
The first broad run passed 155 tests and failed one new test that treated an
independent Job error as the same profile problem. After identifying the profile
by its saved ID, 76 affected tests, 17 focused tests and the shared-profile case
passed. Mypy and seven Playwright cases passed; the Telegram profile screenshot
was inspected. Other problem producers remain on the old status-reconciliation
path, so GET status is still not generally read-only.

Profile reconciliation for a changed role now loads feed details only for its
bindings and prior problem references; startup may still enumerate profiles.
A synthetic check with 150 unrelated feeds verified that it does not issue the
unfiltered feed-details query. A second check compared the saved and read
problem ordering across 405 dependent feeds. The affected suites passed 79
tests; the two new focused cases passed separately, as did seven Playwright
cases with the Telegram profile screenshot inspected. Mypy passed. Binding
lookup itself still scans its existing relation; no role index was added or
performance claim made for an arbitrarily large number of bindings.

The subtitle admission slot is now owned through result acceptance, rather
than released as soon as bytes return. A cancelled request retains the slot
while its physical call is still running; after it returns, its stale owner
cannot release a newer attempt. The first broad attempt exposed an overlap with
an intentionally cancellation-resistant request and was terminated because its
test could not exit until that request was released. After correcting the slot
protocol, 63 YouTube/media tests, mypy and eight Playwright cases passed; the
YouTube settings screenshot was inspected.

A separate draft to persist the shared YouTube wait was not integrated. On a
synthetic comparison with 100 versus 10,000 unrelated failed Jobs, an initial
query scanned 10,513 versus 30,011 relation rows and touched 114 versus 34,225
shared buffers. A source-scoped variant avoided that foreign traversal, but
when 10,000 other publications belonged to the same YouTube feed it still
scanned 30,009 relation rows and 52,010 shared buffers. These are instrumented
EXPLAIN measurements, not latency benchmarks. The draft and its tests are kept
in `.scratch/storage-redesign/youtube-pause-producer-draft.patch`; further work
must update affected owners without a feed-wide traversal and preserve linked
ownership, expiry, retry, rollback and restart. Status GET can still persist the
shared YouTube problem in the committed code.

After the user explicitly accepted losing outstanding `processing_dirty` markers,
migration 059 drops that queue and the application no longer consumes it. A
synthetic migration with pending and orphan markers preserved the tested saved
publication and Job; a fault during migration rolled the schema, marker and Job
back. The resulting synthetic catalogue contained 20 tables including the
migration ledger and no non-internal triggers; remaining compatibility views and
other transitional fields mean the full target schema is not yet established.
The temporary first run passed 252 tests and had one failure caused by a fixture
cursor; the corrected focused case passed. In the task branch, the migration,
worker, recovery, settings, YouTube and release suites passed 257 tests, with
two additional migration failure/success cases and nine Playwright cases passed.
Mypy passed and the status screenshot was inspected. No working database was
migrated as part of these checks.

Independent Standards and Spec reviews of the queue-removal diff both identified
that the outcome test no longer checked the dependent theses Job. The test now
checks both stages and rollback for filter success, retry, cancellation and block,
including a validated filter result. Both reviewers confirmed the finding was
addressed. Six focused cases and the complete affected worker-transaction suite
(78 tests) passed after that test-only change. No additional application code was
changed by the review fix.


On 30 October, the missing Codex-model problem was moved out of status
reconciliation: Store startup, settings/configuration and feed creation update
it, and completion of feed deletion reevaluates it when the removed feed needed
Codex. Deleting one of two such feeds retains the problem; deleting the last
finishes it in the deletion transaction and rollback restores it. Global
settings can independently retain it. The status read no longer recreates a
missing problem. A demo feed inherits the same producer behavior.

On synthetic PostgreSQL, 143 settings, status, subscription, Telegram binding
and worker-transaction tests passed; mypy passed on 157 files. Eight Playwright
status and Codex settings scenarios passed, and the status screenshot was
inspected. Other problem categories are still reconciled by status GET; this is
not a claim that GET is fully read-only. Deleting a feed that required Codex
currently reevaluates effective feed settings, so this path can scan feeds.


Command problems are now synchronized by command events, not recomputed by the
status read. Startup performs an idempotent one-time recovery of older command
receipts, committing in batches and setting a version marker only after success.
An interruption after one batch resumes correctly. Tests cover newer successful
receipts, deleted material, interrupted pending commands, atomic error updates,
and retries sharing a timestamp. The one-time recovery can scan unresolved
receipts; pending-at-start recovery remains a separate existing startup scan.

On synthetic PostgreSQL, 115 settings, status and transaction tests passed in
the task checkout. Mypy passed on 157 files. Eight Playwright status and model
settings scenarios passed, and the status screenshot was inspected. Other
problem categories still depend on status reconciliation.


Profile problems are now read from producer-owned records instead of being
recomputed by status GET. Job problems suppressed by a current profile problem
use its saved feed references; once the profile is restored, an independently
remaining job problem becomes visible. Startup still reconciles profiles, and
profile checks, bindings and feed deletion retain their transactional updates.

A synthetic checkout run passed 76 settings, status and Telegram tests; an
expanded temporary-copy run passed 130 tests. Mypy passed on 157 files. Seven
Playwright Telegram and status scenarios passed, and the Telegram profile
screenshot was inspected. Status GET still reconciles other problem categories.


Migration 060 removes the `reading_memberships` view after application queries
were changed to read the same `feed_publications` rows directly. On synthetic
PostgreSQL, migration from version 59 preserved two memberships including their
primary and unread flags. A forced migration failure restored the view and left
the ledger at 59. No working database was migrated. The temporary-copy suites
passed 254 tests; the task checkout passed 204 migration and transaction tests.
Mypy passed on 157 files. Thirty-two Playwright reading and status scenarios
passed, and the opened-publication screenshot was inspected. Other compatibility
views and status reconciliation are still present.


Migration 061 removes `unviewed_items_by_feed` after its consumers were moved to
explicit joins of publications and memberships, retaining both primary and
unviewed membership predicates as well as the publication viewed predicate. A
synthetic upgrade preserved selection for shared, viewed and flagged records;
a forced failure restored the view and left the ledger at 60. The temporary-copy
regression passed 259 tests, including bounded reading-cost checks; the task
checkout passed 217 migration and transaction tests. Mypy passed on 157 files.
Eight Playwright collection/status scenarios passed in the task checkout; the
collection screenshot was inspected. No working database was migrated.


Queries that use only publication fields now read `publications` directly;
queries requiring the computed feed still use `items_read`. `source_id` remains
unchanged. An expanded synthetic run had 186 passes and one test failure because
the graph-lock test matched the old SQL relation name. After updating that
assertion, the affected transaction suites passed 118 tests; a checkout run of
transaction, status and reading suites passed 60. Mypy passed on 157 files.
Twenty-six Playwright material scenarios passed and the opened-publication
screenshot was inspected. The `items_read` view itself remains in use.
