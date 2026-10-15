# Telegram #17: контракт и проверочная основа

База: e0461daebbb731891b1ae0f574f35cb8c38059bf, актуальный origin/main
на начало работы. Issue: https://github.com/tgenman/oko/issues/17.

## Согласование с v3

Canonical `api/openapi.yaml` уже содержит candidate 3.0.0. Reviewed
baseline остаётся v2; менять major ещё раз или утверждать baseline в этом
шаге не требуется. Неизменность canonical/baseline/generated — часть diff.
Проверки идут через существующий candidate runner, semantic diff остаётся
PENDING_REVIEW до задачи #7.

| Требование Telegram | Текущий canonical / generated |
| --- | --- |
| Web default, без account/auto_read | TelegramWebConnectRequest, optional access |
| Client с одной из двух ролей, auto_read=false | TelegramClientConnectRequest / TelegramRole |
| Профили и подтверждённая identity | TelegramProfiles, role-specific states; user/peer IDs — decimal strings |
| Старые aliases и несопоставленные источники | TelegramLegacyClientAccess, TelegramUnconfiguredAccess |
| Duplicate и identity conflict | DuplicateSourceError, SourceIdentityConflictError |
| Не настроен / недоступен / сменился аккаунт / занят | ProfileNotConfiguredError, ProfileUnavailableError, AccountIdentityMismatchError, ProfileInUseError, ProfileAccountConflictError |
| Удаление и гонки работы | DELETE /api/v3/feeds/{identity}, AcceptedCommand и lifecycle model |
| Immutable access/peer/account | FeedPatchRequest не содержит этих полей |
| Отсутствие OPML и выбора темы | Нет product-facing OPML/topics paths/DTO в canonical; legacy topics пока сохраняются до #19/#21 |

`tests/api/test_telegram_contract.py` проверяет на одном входе JSON Schema и
настоящий generated Python DTO: web default, обе роли, запрет client-полей
для web, отсутствие выбора темы и обязательность роли клиента. Полная
генерация проверяет обе библиотеки. Реальный HTTP Telegram ещё legacy;
новые handlers/consumers подключаются в #5/#21. Auth и lifetime не менялись.

## Typed seam

`oko/collection/telegram/contracts.py` закрепляет binding (Source, cursor key,
lane, optional client ReadSync), source metadata, нормализованную публикацию
с ожидаемыми медиа и внутренними topic scopes, bounded page и историю.
Web не обязан предоставлять account/read/topic операции; Source сохраняет
`discover/fetch`. Пагинация в `position` непрозрачна для Store. Selection
содержит enrollment текущей порции, scan_complete/oldest/paused/warnings,
error/retry_at; конфигурация count/date по-прежнему принадлежит History.

Строгий decoder отклоняет неизвестную версию и другой adapter как SourceError.
Legacy client cursor переносится без сброса after/upper/before/since/lane и
с прежним приоритетом history_ids над history_enroll. Decoder не выполняет
enrollment и не меняет просмотренные записи. Подключение этого decoder к
Store/Source и атомарная фиксация envelope относятся к #20; здесь существующее
поведение не переключается. Для RSS/YouTube ничего не меняется.

## Доказательства и ограничения

Четыре настоящих публичных HTML-ответа, URL, SHA-256 и oracle находятся в
`tests/fixtures/telegram/`. В album wrapper durov/373 подтверждены девять
отдельных anchors 373–381; у grouped videos есть video src и fallback message_media_not_supported.
У public есть video src и CSS photo URL. Fallback не доказывает отсутствие
файла, thumbnail/anchor не считать готовым файлом.
Empty history отличается от landing page недоступного адреса. CDN-файлы не
скачивались, срок жизни URL и доступность скачивания не подтверждены.

SDK subprocess fixture использует установленный Telethon 1.45.0 и реальные
request types, заменяя только сетевой клиент. В общем потоке сохранены
Общее, существующие, новые и уже отсутствующие в каталоге темы с interleaved
IDs. Production worker по-прежнему отвергает whole-forum операции без темы:
он не отправляет broad/global ack. Явный General использует ReadDiscussion.
Существующие scoped scenarios сохранены до #19, helpers не удалены.

Официальные основания: https://core.telegram.org/api/forum,
https://core.telegram.org/method/channels.readHistory,
https://core.telegram.org/method/messages.readDiscussion (проверены 2026-10-15).
Документация и synthetic SDK не доказывают серверные эффекты General,
удалённых тем и global frontier. #19 должен реализовать безопасную global
границу и последующие topic acknowledgements; при недоказанной семантике
нельзя расширять global ack. Join/invite/toggle forum не выполнялись.
Личные подписки, аккаунты и обработчики не запускались.

## Фактические проверки

- Red/green общего decoder: сначала 6 ModuleNotFoundError, затем 6 pass;
  отдельный regression legacy history_ids выявил потерю enrollment, исправлен.
- Первый расширенный HTTP-набор: 9 pass, 4 fail из-за отсутствующей сборки
  frontend в новом worktree. Это ошибка подготовки, не подтверждение HTTP.
- Итоговые runner-результаты и ревью записываются ниже после завершения.

Для недоступного файла подготовлен явно синтетический transport fixture
`unavailable-media.json`: HTTP 403 для media URL из настоящего album member.
Он фиксирует oracle будущего #20 (сохранить пост/остальные медиа, partial,
retry недостающего, без fallback); production web adapter ещё не реализован,
его выполнение этим файлом не доказывается.

### Итог 2026-10-15

Единственный полный запуск `./scripts/check all --candidate api/openapi.yaml`:
140.34 s, exit 1. API generate/drift, validation, Python/TS types, lint,
build, HTTP (9 pass), entrypoint, diff — PASS. Browser: 117 pass, 0 fail,
2 прежних skip. Tools: 115 pass. Python: 362 pass, 3 fail:

1. `test_telegram_html_fixtures` — запуск использовал снимок до исправления
   oracle: grouped video содержит `src`. Исправлено по фактическому HTML;
   целевой повтор PASS.
2. `test_factory_default_empty_history_completes_without_a_real_history_worker`
   — старый setup через single-feed create уже создаёт initial history;
   повторный POST истории закономерно даёт 409. Setup переведён на существующий
   `saved_sources` fixture фида без истории; весь файл теперь PASS. Runtime
   не изменялся, исходные assertions сохранены.
3. `test_stop_exits_even_if_source_ignores_cancellation[stop]` — connection
   refused после запуска тестового процесса. Без изменения сценария оба
   параметра stop/last-close прошли в двух ограниченных повторах. Причина
   нестабильности полного параллельного прогона не установлена.

После исправлений:
```
.venv/bin/python -m pytest -q tests/development/test_test_environment.py \
  tests/collection/test_telegram_contracts.py \
  tests/collection/test_telegram_html_fixtures.py \
  tests/collection/test_telegram_topics.py tests/api/test_telegram_contract.py \
  tests/lifecycle/test_process.py::test_stop_exits_even_if_source_ignores_cancellation
36 passed in 7.55s
```

Новые decoder/API/HTML tests отдельно: 18 pass. Mypy нового module: PASS.
Независимое spec и standards ревью e0461da..d15f5c9: 0 замечаний в каждой оси;
каждый reviewer независимо выполнил 18 новых tests. Итоговый повтор не
объявляется зелёным полным прогоном. Новых skip нет, старые ускорения и
удаление hook сохранены; baseline по-прежнему PENDING_REVIEW. Полный JSON:
`.scratch/check-runs/candidate-1zejuhtx/tree/.scratch/check-runs/20261015T173908Z-de66718d/report.json`.
