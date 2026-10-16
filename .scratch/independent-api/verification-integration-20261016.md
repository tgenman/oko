# Финальная приёмка #7 и шести задач

Дата: 2026-10-16. Ветка `codex/telegram-remaining`, отдельный worktree. Main `2ef0a86` получен повторным fetch и включён через `798cd0b`; ускорения uv/runner сохранены. База diff #7 — `9162fe9`, база всего пакета — `2ef0a86`.

## Результат и доказательства

`./scripts/check all tools` **PASS, 105.20 s**, run `20261016T084446Z-c3f77d8b`. Все 13 наборов успешны: contracts, tooling-types, syntax, lint, types, browser-types, build, http, browser, diff, python-tests, entrypoint, tool-tests. Python 435 (226+209), HTTP 9, tooling 111 (50+61), browser **122 passed, 0 failed, 2 прежних skip**. Новых skips нет. [Сохранённый вывод](evidence/task7/all-tools.log), [receipt с manifest](evidence/task7/receipt.json). Полный исходный report находится в `.scratch/check-runs/20261016T084446Z-c3f77d8b/report.json` локального worktree.

Canonical SHA256: `284735af401c899214e04cd1564448d61419a38d640ecd0a15e7b5bfb6d0f409`. Manifest кода/контракта/тестов зафиксирован в receipt; SHA256 `359f9224296dc2a7233420715521556eb1b6166d7e50339c26a08a4cad68685e` (имена отсортированы, имя NUL bytes NUL; docs/reports исключены).

Пять предыдущих задач имеют отдельные коммиты и фактические отчёты:

| Issue | Коммит | Отчёт |
| --- | --- | --- |
| #19 workers/lanes/read sync | `9e75b0d` | [workers](../telegram-access/workers-verification-20261015.md) |
| #20 web/history/Telegraph/media | `0a6bfca` | [web](../telegram-access/web-verification-20261016.md) |
| #5 Telegram API/UI | `d51ed76` | [API/UI](../telegram-access/api-ui-verification-20261016.md) |
| #21 Telegram acceptance | `0fa766b` | [12 сценариев §14](../telegram-access/acceptance-verification-20261016.md) |
| #6 status/recovery | `9162fe9` | [status](verification-status-20261016.md) |
| #7 cutover | отдельный заключительный коммит с этим отчётом | этот документ |

## Аудит C, D и stories

Все 106 уникальных C-критериев сохранены в семи issue-файлах. Ниже — итоговая трассировка по семействам; полный составной критерий учитывает все владельцы из `tasks.md`, включая Telegram и RSS/YouTube. Исторические отчёты описывают свои версии; актуальное выполнение подтверждает полный run выше.

| Критерии | Наблюдаемая граница и evidence |
| --- | --- |
| C01.01–10, C02.01–10 | `tests/api/`: closed schema/variants, strict JSON vs textual query/path, formats, generated validation, handler/error/binary contracts, static negative examples; [foundation](verification-foundation-20261015.md) |
| C03.01–07 | `tests/lifecycle/test_independent_lifetime.py`, process/lifecycle/runtime_actions; explicit без heartbeat, presence leases/grace, stop и поздние ответы |
| C04.01–06 | v3_reading + reading_composition/folder_completion/source_revisions; native browser reading/opened material/media; [reading](verification-reading-20261015.md) |
| C05.01–08 | v3_settings + initial/existing history + settings browser; omission/null/value, no identity PATCH, defaults/count/date/absent; Telegram web/client проверены в v3_telegram и native UI |
| C06.01–12 | v3_connections/v3_telegram/telegram_bindings + processors/YouTube credentials; RSS/YouTube single forms, immutable claims/profile/delete, OPML/topics сняты; [connections](verification-connections-20261015.md) и API/UI #5 |
| C07.01–09 | v3_problems/status_problems + status_v3_http/status_panel; closed recovery, legacy facts, producer codes и запрет reconnect; отчёт #6 |
| C08.01–06 | v3_settings/diagnostics + process/unknown outcome; persisted receipts, lost response, duplicate observations, discard; [settings](verification-settings-20261015.md) |
| C09.01–06 | independent_sessions/transport/http_isolation + API lexical/security; bearer своего origin, no-store, session version/digest, BFCache и поздний ответ |
| C10.01–07 | reading/opened_material и status_v3_http: content/provenance, stale results, stable problem focus, unknown receipt без replay; отчёты #3/#6 |
| C11.01–10 | settings_v3_http/connections_v3_http/telegram_v3_http/youtube_history_http: drafts/reset, source forms, history/default20/absent, recovery navigation и stable ID |
| C12.01–07 | independent_lifetime/process/frontend_delivery/v3_cutover: API-only без assets/Node, v3 browser, typed410 после security без mutation, сохранение данных и совместный rollback |
| C13.01–08 | independent stdlib client, schema/SDK inventory, полный run, два review, docs, own digest3.0.1 и unsupported major, native legacy/new binding parity; policy изменения см. ниже |

D01–09/D23–25: contracts/lifetime/transport; D10–12/D16: reading/versions/processing; D13–15: settings/history/connections; D17–19: status/problems/recovery; D20–22: receipts/errors/diagnostics/time. D26: весь browser consumer использует generated v3; D27–30: compatibility/data/final gates с действующей policy main. Их evidence — соответствующие семейства таблицы и полный общий run.

Stories 1–8: contract/lifetime; 9–16: reading/versions; 17–22: settings/history; 23–25: source connections/YouTube; 26–27: processing/provenance; 28–34: status/recovery/drafts/receipts/diagnostics; 35–38: generation/build/HTTP; 39: late browser/media/focus; 40: major/digest; 41: saved data; 42: integrated evidence; 43–46: Telegram duplicate/delete/profiles/Telegraph; 47: compatible different digest. Все 47 покрываются указанными настоящими HTTP/process/browser наборами; нового клиента приложения не создано.

Testing Decisions 1–2,6–11,14: существующие HTTP/domain/schema/type/lifecycle suites с временными данными, отрицательными запросами и проверкой отсутствия эффекта. 3: отдельный stdlib HTTP client без Oko/Store/React. 4–5: процесс API-only из копии без assets/Node, explicit/presence/stop/restart. 12: Playwright desktop/mobile и просмотр снимков. 13: strict deterministic generation, stale/index, other digest и unsupported major; исторические package/baseline пункты заменены текущей policy. 15: этот отчёт и отдельные отчёты выше сохраняют неудачи, повторы и ограничения.

## Точный переход и совместимость

Сохранены **50 уникальных operationIds** reviewed registry `semantic-review-v3-draft.md`. Для 77 исторических v1 schemas из `8617c285eec71d792b2f4e81a021b491a7354a6d` существует mapping §6 contract-design; v2 база `a0a12bfe9cec548ca9f38999ffad9a8069590c63` содержит 72 имени и тоже покрыта. Снятие OPML/topics/group types явно документировано. Новые profile PUT/feed DELETE приняты предметными #18/#5; отрицательные HTTP requests не меняют данные.

Старые handlers, generated v2 SDK/DTO и bridge удалены. `snapshots.py` — внутренние факты Store/Runtime, не второй HTTP API. Только read-only проекция исторических problems/receipts остаётся для сохранения данных. Старые `/api[/...]` получают generated `api_version_removed`410 после Host/Origin/bearer checks; stop/settings/connect не исполняются. Runtime/Store сохраняют материалы, настройки, identity, files и исторические исходы.

Независимый `tests/fixtures/independent_v3_client.py` загружает собственную схему3.0.1 с другим описанием и вычисляет собственный SHA256. Читает/настраивает/запускает collect, теряет тело ответа, восстанавливает исход по observation/receipt без replay, выполняет stop. Unsupported major2 отклоняется до mutation. Чтение материала не означает viewed.

Четыре замечания повторного ревью покрыты native tests: legacy_client до/после подтверждения и duplicate_source; default count="20" для web/client; absent старой истории без запуска; отсутствие reconnect/hidden upsert. Дополнительно исправлено непереданное подтверждённое binding в composition/cards/material; старый RSSHub без binding честно unconfigured/needs_connection во всех проекциях, RSS/content сохранены.

## Действующая policy и откат

Main ранее убрал wheel/install и approval baseline/candidate gate (`7d11c006b0e3f7bba63b995bb9b4f6f60cb4d17f`). Пользователь потребовал сохранить ускорения. Поэтому C13.03/C13.05 и Testing Decisions4/13 принимаются через действующие checkout/build/HTTP/drift/staged проверки; wheel/install и обновление baseline **не выполнялись и не объявляются PASS**. Политика изложена в `api/README.md` и `docs/development/package.md`; старые task/spec документы сохранены как источник исходных требований.

Откат: stop, завершение workers, копия всего data directory включая SQLite WAL/SHM/content/settings, согласованный прежний checkout+assets+client major. Предметные изменения данных откатываются из backup при необходимости. Не запускать две версии на одной базе. Сам HTTP cutover не сбрасывает данные и не требует переподключения источников.

## Неудачные запуски и исправления

- `20261016T074344Z-3eda6bee`: prerequisites syntax/type/whitespace FAIL; не полный прогон.
- `20261016T074518Z-cae29541`: browser75PASS47FAIL2skip, Python2 168PASS38FAIL, tooling107PASS4FAIL, Python1 прерван216s. Старые shape/assertions/fixture paths перенесены на v3; это не PASS.
- `20261016T082227Z-02dfc465`: browser122PASS2skip/tools111PASS, Python434PASS1FAIL. Каталог законно менялся при initial history; comparison ждёт завершения истории.
- `20261016T083442Z-5c7eb338`: Python434PASS1FAIL, остальные наборы PASS. Короткая setup session оказалась последней и законно отменила captions RPC: fixture теперь открывает foreground reader до subscribe, без увеличения таймаутов. Целевой повтор13PASS6.54s.
- Затем полный `20261016T084446Z-c3f77d8b`: **все наборы PASS**. Последний source код не менялся после этого прогона; далее только evidence/checkpoints.

## Ревью и визуальная проверка

Две независимые оси code-review по базе `9162fe9`: Standards0 hard violations/0 actionable smells, Spec0 после исправлений. Reviewer самостоятельно проверил new/legacy binding parity2PASS0.70s и RSSHub/independent-client parity2PASS2.20s. Дополнительно исправлены privacy defects: поздний500 после forgotten material имеет безопасный diagnosticID; recovery/affected не возвращает забытый ID; abandoned observation не воскрешает материал. Изменения production после review отсутствуют.

Снимки [evidence/task7](evidence/task7) просмотрены: profiles desktop/mobile, connect desktop, status desktop/mobile. Стабильный дополнительный native Playwright capture1PASS2.011s использовал two animation frames после resize и animations disabled; viewport390×844, одна stop-кнопка. Полный run тоже снимает реальные HTTP UI; никаких Computer Use или личных данных.

Ограничения: синтетические Telegram SDK/HTML fixtures доказывают локальные запросы и безопасные границы, не семантику реального Telegram сервера и не будущую разметку. Неизвестное web-переименование/передача username не гарантирует глобальную идентичность. Уже отправленный внешний ack нельзя отменить. Личные источники/аккаунты/модели и пользовательский процесс не запускались. Remote/mobile приложение и wheel/install workflow не поставляются.

Точный staged/index gate: `./scripts/check staged` PASS11.84s, report `20261016T085812Z-6e269c3d`. Проверены staged контракт/generated Python+TypeScript и оба TypeScript configs; затем добавлена только эта строка отчёта. Diff whitespace чистый.
