# Дополнительная проверка canonical-кандидата v3

Date: 2026-10-15
Status: candidate evidence — PENDING_REVIEW

Проверка сопоставляет кандидат с разделами 2–4 дизайна и фактическими producer полями исходной v2. Первоначальная проверка валидности примеров не обнаружила перечисленные потери: корректный пример сам по себе не доказывает полноту контракта. Новые отрицательные проверки сначала показали ошибки старого кандидата, затем прошли на исправленном.

Исходный замороженный YAML: SHA-256 `f1674348deb1923cf5b5d140c5266ce72dd495f244560cc5db74a0d20c206917`.

Исправленный `/tmp/candidate-next.yaml`: SHA-256 `89b423b47c15b724f266399244a3def5548c3cc46676b13746c9f1ac8afd1df3`. 50 операций, 40 путей, 350 схем. Canonical checkout и baseline этой проверкой не изменялись; перенос точных байтов выполняет основной исполнитель.

| Обнаруженная потеря или противоречие | Исправление и основание |
| --- | --- |
| not_started не содержал общие счётчики | Required progress того же типа, что у остальных настроенных состояний; absent остаётся без selection/счётчиков. Дизайн 3.2. |
| paused не сообщал приостановленную фазу; замороженный состав смешивался с окончанием поиска | Required paused_phase; отдельные progress.manifest_complete и scan_complete. Discovery problem сохраняет code/message/retry_at. Дизайн 3.2, History.freeze/enroll/discovery_state/status. |
| cancelled не выражал завершённый исход | Required outcome=cancelled, без активных reason/retry_at. Счётчик retrying сохраняет незавершённые исходы элементов, а не обещает работающие задания: History.cancel останавливает jobs, но сохраняет history_members. Обнуление такого счётчика выдумывало бы данные. |
| Проблема теряла отдельное последствие и correlation ID | Required title/consequence/message и nullable command_id. Current/retrying и finished остаются разными ветвями; current snapshot отвергает finished. Поля per-problem count/updated_at удалены: у фактического producer нет таких фактов. Snapshot сохраняет собственные count/updated_at. Дизайн D18/D20, Problems.snapshot/reconcile. |
| Материал терял описание и текст после фильтрации | AvailablePublicationContent сохраняет description и server-produced display_text отдельно от raw text. Store.material; фильтрация остаётся на сервере. |
| Карточка теряла media previews и признаки обновления | PendingReadingCard/AvailableReadingCard по availability; available требует images/attachments/revision/version/previous_versions/completeness/loading/source_deleted/changed. Полный текст в карточку не добавлен. ReadingComposition.cards и frontend list preview. |
| Состав чтения терял общий count; позиция demo=-1 отклонялась как отрицательный счётчик | ReadingComposition.count required; position — отдельный signed ordering integer, а не счётчик. Subscriptions.ensure_demo/folders, ReadingComposition.navigation. |
| Сохранённые username/marked ID считались URL; исчезали transport и отличимые состояния подключения | original_urls содержит SourceLocator; новые AddFeed/verify остаются HTTP(S). Non-Telegram variants сохраняют nullable transport и применимые needs_connection/ambiguous, saved connected нормализуется в ready. Legacy Telegram допускает deleting без выдуманной роли. Subscriptions.feeds/update_feed/identify. |
| Diagnostic processing error исчезал при отделении результата от попытки | FilterState/ThesesState сохраняют required nullable diagnostic_message. Ready по-прежнему требует result; прежний результат допускается при новой попытке. Store.material и processing state producers. |
| Часть tracked mutations не имела предметного CommandContext; пустой PATCH невозможно было описать | Добавлены codex_check, processor_check, verify_feed, preview_completion; keys/fields допускают пустой список для разрешённого пустого PATCH. Recovery.change_setting по-прежнему требует непустые keys. Прежний actions.action_context и SettingsPatch. |
| affected settings не имели собственной цели | SettingsResource содержит SettingsTarget; нет page/DOM/HTTP route. |
| Профиль под ключом personal мог сообщать role=collection | TelegramProfiles использует варианты с const соответствующей роли. PUT response сохраняет общий TelegramProfileState. Дизайн 3.2.1. |
| Unchecked Codex требовал выдуманную locality | Unavailable.local nullable; auth — closed unchecked/unknown/missing/chatgpt/api_key. Available допускает подтверждённый способ авторизации. Codex.status/diagnose. |
| Статический processor enum терял существующую model-bound llama.cpp настройку и неизвестные исторические строки | ProcessorSelection допускает codex/llamacpp и ограниченный формат llamacpp:model, уже поддерживаемый Runtime. Response-only LegacyProcessorSelection явно сохраняет нераспознанный прежний выбор; новый PATCH его не принимает. Runtime processing admission и D28. |

Фактические проверки:

- `OKO_V3_SCHEMA=/tmp/candidate-next.yaml .venv/bin/pytest tests/api/test_v3_schema.py -q` — **16 passed**. Новые случаи используют конкретные значения producer: demo position -1, unresolved/needs_connection, @username/marked ID, описание и отличающийся filtered text, lost-request correlation, model-bound processor; они проверяют required поля и отвергаемые противоречия.
- `.venv/bin/python scripts/api.py validate --spec /tmp/candidate-next.yaml` — **PASS**, 40 routes.
- `.venv/bin/python scripts/generate_api.py generate --schema /tmp/candidate-next.yaml --output /tmp/oko-v3-reviewed-generated` — **PASS**; temporary output, canonical/generated checkout не изменены этим вызовом.

- Загрузка temporary generated Python module и `TypeAdapter(...).validate_python(...)` для каждого из 350 named schema examples — **350 checked, 0 failed**. Это дополнительно проверяет выполнимость generated моделей; не заменяет отрицательные статические fixtures и HTTP.

Это evidence формы и генерации кандидата. HTTP/браузерная приёмка семейств, Telegram-модули, миграция сохранённых служебных записей и полный candidate → review → baseline → обычные gates остаются отдельной работой согласно графу задач. Проверка canonical-кандидата не означает завершения задач 02–07.
