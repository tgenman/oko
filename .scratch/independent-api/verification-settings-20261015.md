# Задача 02: настройки, обработчики и исходы команд

GitHub: https://github.com/tgenman/oko/issues/2
Base: 365e2b38f86d6e2bedccbb40b14c615b6f069455
Worktree: /Users/dabondarev/.codex/worktrees/independent-api-task2/oko
Branch: codex/independent-api-task2

## Реализация

- Одна generated GET/PATCH пара настроек для Око, папки и фида. Пять typed полей, omission/value/reset, typed origin, сохранение исторического processor. Non-null FeedPatch отклоняет null до записи; привязка Telegram неизменяема, недоступные отметки чтения не записываются частично.
- Существующие формы используют SDK v3. Сохраняются отдельные черновики, новый ввод при позднем ответе и различие save/reset/discard. Codex использует динамический каталог и независимые filter/theses choices; llama.cpp сообщает machine locality.
- Correlation ID создаётся до отправки. Контекст содержит operation/ресурсы/названия полей без значений и HTTP/UI адресов. Runtime сериализует начало, Store атомарно проверяет прежний context. Порядок keys/fields канонизирован. Повтор ID не обещает дедупликации исполнения.
- GET receipt и observation различают известное начало, подтверждённый результат и неизвестный исход; observation не заменяет terminal result. Клиент сверяет receipt без replay, включая сетевую отмену. Discard закрывает проблему неуспешного редактирования и не откатывает сохранённую конфигурацию.
- Диагностика v3 использует существующий ограниченный журнал: masking, дедупликация, UTC, persistence failure и забывание просмотренного. Старые timestamps и записи доступны через read-only projections; pending прежнего процесса не переписывается при открытии Store.
- Первая команда создаёт предметную v3 проблему с command_id и recovery; существующая панель показывает unknown outcome до сверки. Полная миграция панели и остальных сценариев остаётся задачей 06.

## Уточнения кандидата по фактическим сохранённым состояниям

Baseline не обновлялся; compatibility остаётся PENDING_REVIEW до задачи 07.

1. DiagnosticReceipt получил response-only ignored/material_viewed: поздняя доставка не может вернуть удалённое содержимое и не является ошибкой диска.
2. TelegramAccess получил response-only unconfigured с сохранённым source_locator. Существующий импортированный фид без выбранной привязки нельзя объявлять web или legacy_client с выдуманным alias. Состояние needs_connection сохраняется также для прежнего web/RSS подключения. Новые connect requests не принимают этот вариант.
3. Receipts используют StoredCommandContext: обычный CommandContext либо response-only legacy для снятой/невосстановимой операции. Это сохраняет подтверждённый исход старого импорта, не позволяя отправить legacy-команду или переписать историю HTTP-адресами. Observation по-прежнему принимает только предметный CommandContext.

## Фактические проверки

- Целевые HTTP-проверки сначала получили 404 для отсутствующих v3 routes, затем проходили после реализации; промежуточные ошибки DTO и synthetic setup исправлены. Последний отдельный прогон: 15 passed. Schema + HTTP до добавления последнего сценария: 29 passed. Python mypy и TypeScript проходят.
- Production HTTP/browser модели: 2 passed, включая mobile; screenshots desktop/mobile просмотрены и сохранены в evidence/task2.
- Целевой browser прогон: 19 passed / 2 failed (старый текст locality и неоднозначный selector). После исправлений и полного прогона ограниченный повтор затронутых файлов: 20 passed.
- Единственный полный candidate `check all`: HTTP 9 passed; browser 116 passed / 5 failed / 2 ранее согласованных skipped; Python 433 passed / 9 failed. Сборка, генерация/drift, types, syntax/lint, entrypoint, candidate wheel install прошли. 426.77 s. Отчёт: /private/tmp/oko-task2-full/20261015T145803Z-18da0573/report.json.
- Пять browser failures были устаревшими assertions v2/49 операций, адресами PATCH фида и подсчётом нейтральной сессии как пользовательской записи. Их ограниченный повтор: 20 passed, /private/tmp/oko-task2-recheck/20261015T150253Z-fe969fae/report.json.
- Повтор Python по затронутым файлам: 44 passed / 1 failed. Исправлены stale canonical-v2 assertions и прежняя нормализация diagnostic timestamp. Оставшийся synthetic YouTube-сценарий зависел от истёкшей client_presence; переведён на explicit API-only и отдельно прошёл (1 passed).
- Три теста обычного runner перенесены в собственный synthetic checkout, чтобы внешний candidate-контекст не требовал Node там, где проверяется обычный Python-only режим. Ограниченный повтор: 3 passed.
- Полный набор повторно не запускался. Ограниченные повторы не выдаются за новый полный PASS. Исторические результаты №1 сохранены, личные источники и пользовательский экземпляр не запускались.

## Ревью

Code-review: Standards первоначально 1 P2 (race context/start), Spec 3 P2 (порядок fields, legacy processor, необратимая startup запись). Исправлены; повтор выявил два небольших последствия — legacy HTTP conflict и отображение legacy processor в сводке, также исправлены. Дополнительное Spec-замечание о лишней обёртке истории фида исправлено и закреплено GET/PATCH HTTP-сценарием.


## Standards

Ограниченное финальное ревью: 0 оставшихся замечаний. Проверка context/start выполняется атомарно; legacy HTTP конфликт сохраняет 400. Read-only projections не изменяют исходные служебные записи.

## Spec

Ограниченное финальное ревью: 0 оставшихся замечаний. Исправлены порядок fields, исторический processor, startup pending и проекция истории; response-only варианты сохраняют фактические состояния без расширения входных команд.

Итог: Standards 0, Spec 0.

## Финальные ограниченные проверки

- HTTP задачи №2: 15 passed; HTTP + JSON schema предыдущего шага: 31 passed. Последняя добавленная проверка — GET/PATCH фида с сохранённой историей.
- TypeScript приложения, browser tests и tooling — PASS; mypy 62 files — PASS; diff check — PASS.
- Основной production browser после исправлений: 3 passed (desktop lost-response/no replay/new draft; desktop/mobile processor settings).
- Последний canonical SHA-256: `5af08cc8e5c60f2874d728eefd48bbe34787d12664a0ea928fcc2a59e924a384`. Candidate generation, drift и build этого контракта — PASS; compatibility PENDING_REVIEW. /private/tmp/oko-task2-contract-final/20261015T151053Z-1a0ab024/report.json.
- Полный прогон и все первоначальные failures сохранены выше. Все упавшие сценарии после исправлений прошли ограниченные повторы; новый полный PASS не заявляется.

- Штатный pre-commit в изолированном candidate: exact staged API/Python/TypeScript regeneration и browser/tooling types — PASS, 19.09 s; compatibility PENDING_REVIEW. Проверенный Git tree затем переносится на ветку worktree только после проверки полного равенства tree hash; hooks не отключались.


## Интеграция main после завершения задачи

Влит origin/main c8e40b3, включая PR #12 / 5f7b71a: параллельные browser/Python группы, отдельный tools набор с отбором по diff, быстрые synthetic fixtures и удаление дорогих дублей. Scripts/check, CI, runner tests и API tooling сохранены из main. Из файлов ускорения 25 совпадают побайтно, 5 отличаются только переходом HTTP URL на v3; owner.ts совмещает API v3 с удалением неиспользуемого detail из main. Все 408 upstream удалений сохранены.

Конфликты API/installed-package и YouTube tests разрешены в пользу актуальных проверок main; прежний ordinary_checkout заменён общей fixture из main. Legacy diagnostic input снова требует timezone, как в main; чтение исторического журнала остаётся через response projection.

Проверки после merge: pytest test_check_runner.py + test_v3_settings.py — 51 passed (27.15 s); TypeScript приложения/browser/tooling — PASS; candidate build/drift и три browser-сценария пересечения — PASS (3 passed). Отчёт /private/tmp/oko-task2-main-merge/20261015T151811Z-cc3fbff0/report.json. Полный набор повторно не запускался.
