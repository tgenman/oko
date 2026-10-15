# Проверка задачи 04: история и одиночные подключения RSS/YouTube

Date: 2026-10-15
GitHub: https://github.com/tgenman/oko/issues/4
Branch: codex/independent-api-task4
Base: 6f0a811034a587e1082f4ca054a8889ea59d8021 (main после слияния PR #15)
Status: поведение задачи проверено; общий v3 остаётся candidate / PENDING_REVIEW

## Результат

Одиночные RSS/YouTube verify/add, каталог подписок, YouTube PATCH и управление историей используют canonical v3 и generated DTO/SDK. Команды сохраняют typed receipts. Отсутствующая initial_history получает count="20"; null, пустая/неполная ветвь, недопустимые count/date/timezone дают 422 без записи. Count остаётся точной десятичной строкой в UI и API, включая 9007199254740993. Date содержит только date/timezone. Общий выбор истории используется формой Telegram; его собственная миграция и acceptance относятся к задаче 05.

HistoryState различает absent, not_started, discovering, fetching, waiting, paused, complete, cancelled; сохраняет прогресс, ошибки и границы выбора. Для сохранённой истории без run используется детерминированный response-only идентификатор: чтение не создаёт manifest, не меняет cursor и не запускает архив. Старые фиды без выбора выдают absent и известную legacy_since либо null. Пауза до старта имеет paused_phase=not_started. Credentials keep/replace/clear явные, секреты не возвращаются и не попадают в ошибки/диагностику. Поздний status response не заменяет подтверждённое изменение истории/YouTube.

OPML удалён из UI, HTTP routes, DTO, Runtime и парсера; существующие фиды сохраняются. Старый baseline не изменён: временный generated legacy bridge строится воспроизводимо с исключением снятого OPML-семейства. Сохранённые исторические receipts OPML остаются доступны для чтения. Общий legacy bridge удаляется в задаче 07.

Setup проверок добавляет источники по одному через v3 verify/add и закрывает setup tab. Сценарии миграции старого сохранённого состояния используют явно подготовленную временную SQLite до старта приложения. Синтетические RSS/YouTube адаптеры исключают реальную сеть. На узком экране настройки фида переходят в одну колонку, чтобы история оставалась читаемой.

## Фактическая проверка

1. TDD HTTP: новый сценарий verify/add сначала получил 405; после реализации проверен через canonical JSON Schema. Отдельные повторы: 3 passed, затем полный новый файл — 5 passed. Покрыты omitted/null/value, точная строка, date/timezone, неверные/лишние поля, credentials, legacy absent, pause/cancel/restart, conflicts/receipts, удалённые OPML endpoints.
2. Промежуточные целевые прогоны выявляли старые OPML fixtures, устаревшие frontend assets, лишний setup lease, старые expectations и неполный synthetic status. Исправления проверялись ограниченно; 27 Python passed после сборки, затем 6 passed для lifecycle/setup. Исторические логи: /private/tmp/oko-task4-python-target.log, /private/tmp/oko-task4-python-recheck.log, /private/tmp/oko-task4-browser-target.log, /private/tmp/oko-task4-browser-second.log.
3. Единственный полный прогон: `./scripts/check all --candidate api/openapi.yaml --report-dir /private/tmp/oko-task4-full`. Отчёт: `/private/tmp/oko-task4-full/20261015T170553Z-c25cdee2/report.json`, 148.74 s. Generation, drift, contracts, tooling types, syntax, lint, Python/browser types, build, HTTP (9), entrypoint, diff и tool tests (115) PASS. Python: 341 passed / 5 failed. Browser: 107 passed / 9 failed / 2 ранее согласованных skips. Падения связаны с миграцией setup и mock payloads; этот результат не переписан как полный PASS.
4. Первый повтор на candidate: `/private/tmp/oko-task4-recheck/20261015T171321Z-7107c74d/report.json`: 5 Python passed; browser 26 passed / 4 failed / 1 существующий skip. Дополнительно guard обнаружил временный диагностический test-файл, созданный во время прогона: runner правильно отказался считать снимок неизменным. Этот файл существует только в временном candidate и не входит в коммит. Прямой запуск collection в рабочем worktree отказал из-за stale build; повтор на свежей сборке — 16 passed / 1 failed (исправлен source_feed fixture: нужен id, а не объект).
5. Проверка всех исправленных падений: `./scripts/check build browser python types lint --candidate api/openapi.yaml --files tests/browser/test_connections_v3_http.ts tests/browser/test_feed_debug.ts tests/browser/test_feed_tree.ts tests/browser/test_http_isolation.ts tests/browser/test_settings_navigation.ts tests/browser/test_telegram_connection.ts tests/browser/test_transport.ts tests/reader/test_v3_connections.py tests/collection/test_rss.py tests/collection/test_youtube_theses_freshness.py --report-dir /private/tmp/oko-task4-final-check`. Отчёт: `/private/tmp/oko-task4-final-check/20261015T171508Z-e90127af/report.json`, 63.24 s. **22 Python passed; 30 browser passed, 1 существующий skip; generation/drift/contracts/tooling/types/lint/build PASS.**

6. Финальная проверка после замечания о GUID и мобильного CSS: `./scripts/check build browser python --candidate api/openapi.yaml --files tests/browser/test_connections_v3_http.ts tests/collection/test_rss.py --report-dir /private/tmp/oko-task4-visual-final`. Отчёт `/private/tmp/oko-task4-visual-final/20261015T171635Z-14105120/report.json`, 66.99 s. **12 Python passed, 1 browser passed; generation/drift/contracts/tooling/browser types/build PASS.**

Полный набор после исправлений повторно не запускался. Ограниченные повторы относятся к выбранным файлам, не означают нового полного PASS. Новых skips нет. Из main сохранены ускорения runner и удаление автоматического hook; файлы runner не менялись.

## Ревью и ограничения

Независимое code-review по базе main: Standards — 0 оставшихся замечаний; Spec — 0 оставшихся замечаний. Исправлены paused_phase до старта и ослабление проверки повторного GUID: тест явно запрашивает повторный collect и ждёт обработки конфликтующего RSS перед проверкой сохранённого teaser. Повторные ревью ограничены исправленными сценариями и мобильной раскладкой.

Candidate compatibility остаётся **PENDING_REVIEW**, baseline/canonical не утверждались и не менялись. Выпуск независимого API и отключение оставшегося legacy принадлежат задаче 07. Личные источники, Telegram-аккаунты и реальные модели не использовались, пользовательский экземпляр не перезапускался.

Просмотрены финальные Playwright screenshots: [desktop](evidence/task4/desktop.png), [редактор при mobile viewport](evidence/task4/mobile.png). На mobile история и точный count читаемы, поля и кнопки доступны; редактор имеет ширину больше 280 px при viewport 390 px. Первый мобильный screenshot выявил сжатие редактора до 18 px; исправление включено и повторно проверено.
