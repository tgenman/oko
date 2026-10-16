# Checkpoint: #7, переход API v3

2026-10-16. Активное поручение: закончить последовательно #19, #20, #5, #21, #6, #7; один коммит на задачу, один PR только в конце. Goal остаётся active. Worktree `/Users/dabondarev/.codex/worktrees/telegram-remaining/oko`, ветка `codex/telegram-remaining`. Основной checkout не трогать, там чужие untracked публикационные файлы.

## Коммиты

#19 `9e75b0d`; #20 `0a6bfca`; merge ускорений main `798cd0b` (main `2ef0a86`); #5 `d51ed76`; #21 `0fa766b`; **#6 `9162fe9`**. #7 ещё не коммитить: общий gate не прошёл. PR/push ещё не выполнены.

#6:39Python+30browserPASS; api check/validate/build/types/stagedPASS; Spec и Standards0. Отчёт verification-status-20261016.md, screenshots evidence/task6. Commit9162fe9.

## Реализовано #7

- app.py: старые handlers удалены; unversioned /api[/...] после Host/Origin/bearer получают generated `ApiVersionRemovedError` 410, ни mutations, ни action-context не исполняются. Canonical схема не менялась относительно #6, содержит этот тип заранее.
- Последний UI catalogue и fetch client используют только generated SDKv3; v2 directories, baseline.json, legacy_api.py и генерационный bridge удалены. contracts.py экспортирует только v3.
- snapshots.py: узкие внутренние валидированные снимки Store/Runtime (45 classes), не второй HTTP interface. Все внешние ответы generatedv3. Legacy command context mapping в commands_v3 остаётся read-only для исторических receipts.
- Store/versions выдают media URLs v3 прямо. Проекции больше не подменяют старый prefix.
- Реальные найденные дефекты исправлены: поздний500 после forgotten material не получает пустой diagnosticID; проекция typed context не восстанавливает affected/recovery удалённого материала; late observation получает сохранённый abandoned без UI-ресурсов; YouTube retry deadline доступен в retrying problem.
- Review P2: reading_v3.feeds не передавал подтверждённую binding, выдавал legacy_client в composition/cards/material. Исправлено; два новых native parity tests RED→GREEN (новый client и подтверждение прежнего alias). После этого независимый Spec review2PASS0.70s, замечаний0.
- README/api README/frontend architecture/package docs актуализированы. wheel/install и approval-baseline были сняты раньше по действующей policy; не восстанавливать, но честно отметить ограничение при итоговой приёмке.
- Новый stdlib client `tests/fixtures/independent_v3_client.py`: read/config/collect, потерянное тело ответа, receipt без replay, stop, unsupported major; без Oko/React/Store/heartbeat. Процессный тест1PASS1.52s. Helpers launch(api_only=True), opaque presence sessions.

## Проверки и текущие ошибки

- `/private/tmp/oko7-lifecycle14.log`: **104PythonPASS47.67s**, включая lifecycle/process, processing, filters, Telegram read sync, YouTube access, HTTP contract inventory.
- `/private/tmp/oko7-parity-green.log`: **21PythonPASS**, contracts/typesPASS4.26s (`20261016T075149Z-767959ca`); npm typecheckPASS после browser route edits.
- Current canonical digest **284735af401c899214e04cd1564448d61419a38d640ecd0a15e7b5bfb6d0f409**. Ровно50 unique operationIds совпали с reviewed registry. Из git8617c285 взяты77v1 schema names, все присутствуют в section6 mapping; a0a12v2 содержит72, тоже покрыты. Spec reviewer независимо подтвердил50/77.
- Первый all tools `20261016T074344Z-3eda6bee` остановился на migration syntax/type/whitespace prerequisites. Исправлены; не PASS.
- Второй all tools **`20261016T074518Z-cae29541`**, лог `/private/tmp/oko7-all-tools2.log`: contracts/tooling/syntax/lint/types/browser-types/build/httpPASS; browser75PASS47FAIL2историческихskip; Python shard2 168PASS38FAIL; tools107PASS4FAIL; Python shard1 прерван после216s (всё ещё первые collection/test_telegram, старые shape assertions/ожидания, не ждать повторно вслепую). Полный gate НЕ PASS. Новых skips нет.
- Tool failures: bulk route rewrite задел физические пути `frontend/src/api/generated` и `tests/api/...` в4testfiles; исправлены после full gate, нужно ограниченно перепроверить tools. Не возвращать v3 в filesystem paths.
- v3_settings failures исправлены: historical processor теперь seed Store, новый PATCH unknown processor422; historical receipt identity должен остаться `POST:/api/import:` (не v3), это история. Перепроверены в21PASS.
- Browser keys после full gate: строки `/api/status`, `/api/subscriptions`, `/api/debug` и native helper переводятся на v3; filesystem/import paths не переписываются. Синтетические inputs остаются тестовыми историческими данными (`scenario-data.ts`,1677строк), доставленные reading/status replies валидируются canonicalv3. В production никаких v2 adapters нет.
- `/private/tmp/oko7-browser-keys-check.log`: **6browserPASS5FAIL**,12.27s (`20261016T075235Z-42df9b62`) — улучшение после исправления ключей. Это4файла: existing_history и connections_v3_http теперьPASS, feed_debugчастичноPASS; остаются helpers и assertions ниже.

## Следующие шаги (не повторять полный gate до исправления этих классов)

1. Browser native helper `tests/browser/http-reader.ts` строит current URL `origin+'/api/'+(url.startsWith('v3/')?url:'v3/'+url)`. В `tests/browser/connections.ts` остался вызов `tabs/.../heartbeat`, заменить на opaque client session create/close. Остались native `r.api('debug',...)`: callers должны использовать diagnostics. После этого wrappers processor GET.configuration, Codex GET.configuration/availability, reading.content/source/theses.result — поправить native assertions, не делать response adapter обратно в v2. `ApiReply` в fixture-types.ts пока старые scenario types для native paths: разделить input scenario и current native DTO типы, typecheck поможет найти старые assertions.
2. Остальные browser failures полного gate в parallel/browser/output.log. Большинство старых fixture keys уже исправлены после full run, поэтому сначала ограниченный/затем полный browser набор. Если Telegram synthetic catalogue lacks access, создавать честный typed access из исторических фактов; никаких выдуманных roles. Не добавлять skips.
3. Python shard1 застрял на `tests/collection/test_telegram.py` (29 результатов): эти старые HTTP assertions ещё complete/full/text/members/detail/telegram_account. Перенести по аналогии104PASS на availability/content/source.message_ids(str IDs)/typed errors/immutable access. Не менять pure Source/Store unit assertions на wire shape. Сейчас синтаксис этого файла исправлен, одна строка была повреждена AST col_offset с кириллицей, проверка всего AST затемPASS.
4. Python shard2 failures: `tests/reader/test_telegram_web.py`13, `tests/collection/test_source_revisions.py`9, `tests/collection/test_youtube.py`9, tests/development/test_test_environment.py4, test_v3_connections.py1. Full per-shard output есть в `.scratch/check-runs/20261016T074518Z-cae29541/parallel/python-tests-2/20261016T074526Z-7b7c2cdd/output.log`. Большинство — старые wire shape predicates; JSON GET material теперь content, source.captions, theses.result/attempt; card availability. Не ослаблять реальное поведение, raw test SQL/fixtures остаются историческими.
5. Helpers `tests/support/telegram.py`: `connect` явно задаёт date yesterdayUTC для live-focused scenarios (product default20 отдельно native verified); `connect_request` old scenario input→current request, unknown fields теперь НЕ скрывает, отправляет к canonical422. Поэтому old feed_id upsert expectations обновлять на422/duplicate без mutation. Base Telegram теперь имеет synthetic history_page. Удалённый результат source access проверять черезcatalog.
6. test early ack пришлось сделать устойчивым к порядку истории: сперва готовый11auto-acked, затем появляется29сblockedfetch; всё ещё проверяется ack11 до release29. Это не новый skip и не ослабление read safety. 104PASS включает сценарий.
7. Завершить audit C/D/stories/testing в новом final verification report, screenshots desktop/mobile свежего gate просмотреть/сохранить; исторические отчёты не редактировать. Два review agents `/root/task6_standards_review`, `/root/task6_spec_review` финальные по #7:0 замечаний после parity. При новых production changes — focused review.
8. После targeted fixes выполнить полный `./scripts/check all tools` (предыдущий INTERRUPTED/FAIL не заменять выбранным PASS), staged, отдельный commit#7, fetch/merge latest main при необходимости сохранив ускорения, push и **один общий PR**, attach_artifact. Прочитать pr skill перед body. Объективно проверить все6 и PR, затем goal complete. До этого goal active; пользовательского подтверждения не требуется.

Все commands с записью в managed worktree требуют require_escalated (authorized worktree outside writable roots), auto-review разрешает. Ни личные источники, ни аккаунты, ни пользовательский процесс не трогались. Code Review skill разрешает две review оси; implementation один исполнитель. Перезапуск пользовательского экземпляра не делать. Memory read использован MEMORY.md217–218; при финальном ответе нужна ровно одна memory citation block, memory не писать.

## Итог, заменяющий прежний список следующих шагов

Полная реализация #7 закончена. Финальный all tools 20261016T084446Z-c3f77d8b PASS105.20s, HTTP9+Python435+tools111+browser122, 2 прежних skips. Spec и Standards0, production после review не менялся. Аудит C/D/stories/testing и фактические неудачи сохранены в verification-integration-20261016.md; снимки/receipt в evidence/task7. Остались staged, commit#7, push, один общий PR, remote audit и закрытие goal.
