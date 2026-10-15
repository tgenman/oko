# Проект semantic review canonical v3

Status: draft — PENDING_REVIEW
Date: 2026-10-15

Это evidence подготовки контракта задачи 01, а не разрешение выпуска и не утверждение соответствия HTTP всех операций. Родительские [спецификация](spec.md), [дизайн](contract-design.md), [задачи](tasks.md) сохраняют согласованный объём. Telegram реализуется отдельно по решению пользователя; его публичный interface описан здесь для общей v3, предметная реализация и HTTP-приёмка пока являются внешней зависимостью.

## Зафиксированные байты

- База: `a0a12bfe9cec548ca9f38999ffad9a8069590c63`, OpenAPI 2.0.0, 49 операций / 41 путей / 72 схем.
- Исходный YAML SHA-256: `f344a3c71d5c8dafac1154bfe81289ecaf051278614f813f192bb137ae2a9fe5`.
- Исходный canonical bundle SHA-256: `fa05c366af7cdafe8f7b2f3c37d3987465143480a9f8abec5510dfb7aac49e35`.
- Кандидат: OpenAPI 3.0.0, 50 операций / 40 путей / 328 достижимых схем.
- YAML кандидата SHA-256: `f1674348deb1923cf5b5d140c5266ce72dd495f244560cc5db74a0d20c206917`.
- Canonical bundle кандидата SHA-256: `5f6ea46cbceb6e68443088218f2e90b2df148af8c4cd129cf41d5a6d21026051`.

Baseline не изменён этой подготовкой. Перед финальным ревью необходимо обновить evidence точных байтов при любом последующем изменении контракта. Счётчик схем отражает закрытые варианты состояния, отдельные request/response, typed error details и переиспользуемые operation-specific error unions; одинаковые наборы ошибок переиспользуются, недостижимые helpers удалены.

## Несовместимые изменения

- Единый major 3 и `/api/v3`; unversioned `/api` снимается окончательным переходом задачи 07. Runtime и потребители переносятся по expand–contract; наличие временного старого пути не делает два wire interface совместимыми.
- Предметные reading-composition и client-sessions заменяют navigation и tabs. API-only использует explicit lifetime; обычный запуск — client_presence с lease 6 / grace 15 / renew 2 секунды. Отсутствующий/закрытый/просроченный session ID имеет отдельную семантику; expiry не оживляется renew.
- Карточки и материал — одна исходная публикация. Content availability, полнота и processing attempts отделены от принятых результатов; ready требует результата. Source reference содержит конкретный kind; caption state существует только у YouTube. Telegram IDs передаются точными строками. Нет событий, обзоров и рекурсивных источников.
- Timestamp — RFC3339 с обязательным offset, ответ UTC; опубликованная неизвестная дата required-nullable. Версии положительные, счётчики ограничены безопасным integer. SourceUrl учитывает существующий `tg://openmessage`; подключение RSS/YouTube принимает HTTP(S).
- HistorySelection — closed count/date union. Count всегда точная положительная decimal string до 4096 знаков. Пропущенный целиком initial_history нового AddFeed/Telegram connect означает count="20"; null/пустой/неполный объект отклоняется. Старые фиды получают absent с legacy_since, без selection и счётчиков; миграция не запускает историю. Явная догрузка YouTube требует count selection.
- Общие GET/PATCH settings сохраняются. Только originals, theses, prompt_theses, prompt_filters, processor; omission сохраняет, null снимает override. Значения и origin типизированы. Пять non-null FeedPatch полей теперь дают 422 при null; resource-dependent admission остаётся предметным правилом.
- YouTube PUT заменяется PATCH, credentials change — keep/clear/replace. Неверная форма 422; невозможное допустимое JSON состояние остаётся бизнес-ошибкой.
- Telegram web/client connect с immutable access; отсутствие access означает web. Response дополнительно поддерживает legacy_client с alias/source_locator/nullable peer_id, без role/account_user_id. Профили personal/collection получают один GET и один PUT проверки/привязки. Topics/OPML удалены. DELETE фида отражает deleting/deleted без нового polling endpoint, ack или leave. Дубль перечисляет всех прежних владельцев через тот же access union.
- Единый envelope ошибок code/message/details/diagnostic_id/command_id; details отдельного типа либо required-null при отсутствии данных. Validation violations не возвращают raw input/ctx. Наборы ошибок заданы отдельно для операций. Неверный переданный X-Oko-Action теперь 422; отсутствие создаёт ID. Корреляция не является idempotency key.
- CommandContext не содержит method/path/UI. Receipt unknown не доказывает отсутствие запроса и не разрешает автоматический retry. Recovery имеет typed цели; reconnect_feed отсутствует. Historical legacy problems сохраняют raw_code, finished outcome сохраняет resolved/irrelevant.
- Закрытый response decoder может сломаться от нового поля/enum/nullability — такие изменения требуют major. Digest сверяет только артефакты одной поставки; независимый клиент сверяет semantic major.

## Реестр целевых операций

Ниже все 50 операций; переход исходных 77 схем из первоначального ревью остаётся в разделе 6 дизайна. Текущие 72 схемы проверены отдельно против зафиксированной базы, а снятые схемы не возвращены ради прежнего счётчика.

| OperationId | Method/path | Success |
| --- | --- | --- |
| `diagnostic_entries` | `GET /api/v3/diagnostics` | 200 |
| `report_client_diagnostic` | `POST /api/v3/diagnostics` | 200 |
| `clear_diagnostics` | `DELETE /api/v3/diagnostics` | 200 |
| `status` | `GET /api/v3/status` | 200 |
| `observe_command` | `POST /api/v3/command-observations` | 200 |
| `abandon_problem` | `POST /api/v3/problems/{identity}/abandon` | 200 |
| `create_client_session` | `POST /api/v3/client-sessions` | 201 |
| `renew_client_session` | `PUT /api/v3/client-sessions/{identity}` | 200 |
| `close_client_session` | `DELETE /api/v3/client-sessions/{identity}` | 200 |
| `stop` | `POST /api/v3/stop` | 200 |
| `codex_state` | `GET /api/v3/processors/codex` | 200 |
| `codex_save` | `PUT /api/v3/processors/codex` | 200 |
| `codex_models` | `GET /api/v3/processors/codex/models` | 200 |
| `codex_check` | `POST /api/v3/processors/codex/check` | 200 |
| `processor_config` | `GET /api/v3/processors/llamacpp` | 200 |
| `processor_save` | `PUT /api/v3/processors/llamacpp` | 200 |
| `processor_check` | `POST /api/v3/processors/llamacpp/check` | 200 |
| `regenerate` | `POST /api/v3/materials/{item}/regenerate` | 200 |
| `retry_theses` | `POST /api/v3/materials/{item}/theses/retry` | 200 |
| `telegram_profiles` | `GET /api/v3/telegram/profiles` | 200 |
| `telegram_connect` | `POST /api/v3/telegram/connect` | 200 |
| `subscription_list` | `GET /api/v3/subscriptions` | 200 |
| `add_feed` | `POST /api/v3/feeds` | 200 |
| `verify_feed` | `POST /api/v3/feeds/verify` | 200 |
| `youtube_settings` | `GET /api/v3/youtube` | 200 |
| `youtube_configure` | `PATCH /api/v3/youtube` | 200 |
| `effective_settings` | `GET /api/v3/settings/{scope}/{owner}` | 200 |
| `settings_patch` | `PATCH /api/v3/settings/{scope}/{owner}` | 200 |
| `history_pause` | `PATCH /api/v3/feeds/{identity}/history` | 200 |
| `history_start` | `POST /api/v3/feeds/{identity}/history` | 200 |
| `history_cancel` | `DELETE /api/v3/feeds/{identity}/history` | 200 |
| `feed_patch` | `PATCH /api/v3/feeds/{identity}` | 200 |
| `remove_feed` | `DELETE /api/v3/feeds/{identity}` | 200 |
| `folders` | `GET /api/v3/folders` | 200 |
| `reading_composition` | `GET /api/v3/reading-composition` | 200 |
| `cards` | `GET /api/v3/cards` | 200 |
| `preview_completion` | `POST /api/v3/folders/{folder}/completion/preview` | 200 |
| `complete_folder` | `POST /api/v3/folders/{folder}/completion` | 200 |
| `collect` | `POST /api/v3/collect` | 200 |
| `retry_material` | `POST /api/v3/materials/{item}/retry` | 200 |
| `image` | `GET /api/v3/materials/{item}/images/{name}` | 200 |
| `attachment` | `GET /api/v3/materials/{item}/media/{name}` | 200 |
| `previous_version` | `GET /api/v3/materials/{item}/versions/{number}` | 200 |
| `previous_media` | `GET /api/v3/materials/{item}/versions/{number}/{kind}/{name}` | 200 |
| `material` | `GET /api/v3/materials/{item}` | 200 |
| `restore_filter` | `POST /api/v3/materials/{item}/filter/restore` | 200 |
| `retry_filter` | `POST /api/v3/materials/{item}/filter/retry` | 200 |
| `viewed` | `POST /api/v3/materials/{item}/viewed` | 200 |
| `command_receipt` | `GET /api/v3/commands/{identity}` | 200 |
| `telegram_profile_save` | `PUT /api/v3/telegram/profiles/{role}` | 200 |

## Фактическая проверка этой подготовки

- RED: `.venv/bin/pytest tests/api/test_v3_schema.py -x -q` на исходном v2 — ожидаемая ошибка отсутствующего HistorySelection.
- GREEN: `OKO_V3_SCHEMA=/tmp/oko-v3-openapi.yaml .venv/bin/pytest tests/api/test_v3_schema.py -q` — 7 passed. Проверены точный count, absent, omission/reset/non-null feed fields, legacy Telegram response-only, web без auto_read, lifetime варианты, ready с обязательным result, response/request Codex choice, реестр 50 операций, валидность всех schema examples и всех альтернатив ошибок каждой операции с format checker.
- `.venv/bin/python scripts/api.py validate --spec /tmp/oko-v3-openapi.yaml` — PASS, Valid OpenAPI 3.1.0: 40 routes.
- Ограничения: эти проверки доказывают форму контракта и примеров. Они не доказывают работу всех producer adapters, HTTP миграцию, generated narrowing, веб-клиент, Playwright, package/install, совместимость независимого клиента или Telegram-поведение. Соответствующие результаты дописываются отдельно исполнителем соответствующего среза; задача 07 принимает всю интеграцию.
