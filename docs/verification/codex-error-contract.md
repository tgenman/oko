# Ошибки Codex — соответствие HTTP-контракту

Дата: 2026-10-20. Git-база: `ade81be0c83ce2156480f4395160bd1dad1733c8`.
Объём согласован пользователем после разбора расхождения:
[описание операций Codex](../../api/README.md#ошибки-операций-codex).

## Изменение

- PUT `/api/v3/processors/codex` и POST `/api/v3/processors/codex/check`:
  ответ 503 использует существующий `CodexModels503Errors`, допускающий
  `processor_unavailable` и `storage_unavailable`.
- Все четыре операции Codex описывают `409 runtime_stopped`; три операции внешней
  работы также сохраняют `runtime_disabled`. Чтение состояния при выключении
  по-прежнему доступно.
- Добавлены примеры ошибок, перегенерированы TypeScript error types, Python bundle
  и revision digests. Остальные Python DTO/SDK не изменились после генерации.
- Семантическое сравнение YAML подтвердило изменения только шести ответов четырёх
  операций. Запросы, успешные ответы, другие операции и именованные схемы сохранены.
  Серверный код в этой правке не изменён. Успешная диагностика недоступного Codex
  остаётся `200 state: unavailable` с успешным receipt.

## Фактические проверки

1. Новые строгие HTTP-сценарии до правки схемы: **6 failed, 5 passed** (2.08 s).
   Воспроизведены две ошибки валидации 503 и четыре ошибки stopped-ответов.
2. После генерации: **38 passed, 4 failed** — четыре прежних сценария настроек
   не запустились из-за устаревшей сборки после смены contract digest.
   `npm run build` — PASS. Целевой повтор: **42 passed** (4.75 s).
3. `npm run api:generate`, `npm run api:check`, `npm run typecheck` — PASS.
   Typecheck включает frontend, browser tests и tooling.
4. Изолированный runner — **PASS**, 33.31 s:
   contracts, tooling types, Python types, build, entrypoint, diff, staged;
   HTTP — **9 passed**; выбранные Python-наборы — **56 passed**
   (17 + 20 + 6 + 13). Проверка неизменности checkout — PASS.
   Staged проверяет точные байты временного индекса и generated-библиотеки.
5. Независимое ревью текущего diff относительно снимка перед исправлением:
   замечаний нет. Reviewer проверил объём, схемы и тесты; сам тесты не запускал.

Строгий `tests.support.v3.TestClient` проверяет ответы по JSON Schema 2020-12.
Новые `tests/reader/test_codex_errors.py` покрывают сбой каталога, прерванную проверку,
профиль ошибок после остановки/выключения, сохранение конфигурации и receipts.
Существующие десять сценариев поздних ответов и ошибок Telegram также переведены
на строгий клиент; прежние статусы и предметные коды сохранены.

Изолированная копия включает ранее согласованный Runtime-рефакторинг, а не
посторонние изменения. Проверенное дерево: `927ff43d00bd1d294be2f6b96fd2567f917dd8c1`.
Этот отчёт добавлен после прогона; код, схема и тесты после него не менялись.
Серверный Runtime побайтно совпал с предыдущей проверенной реализацией.

Команда из `/private/tmp/oko-codex-errors-check`:

```sh
OKO_PYTHON=/Users/dabondarev/repo_personal/oko/.venv/bin/python \
NODE_PATH=/Users/dabondarev/repo_personal/oko/node_modules \
./scripts/check contracts build types http python entrypoint diff staged --compact \
  --report-dir /tmp/oko-codex-errors-reports --files \
  tests/reader/test_codex_errors.py tests/reader/test_external_checks.py \
  tests/reader/test_codex_settings.py tests/api/test_contract.py \
  tests/api/test_v3_schema.py tests/api/test_v3_lexical.py tests/api/test_telegram_contract.py
```

JSON: `/private/tmp/oko-codex-errors-reports/20261020T065606Z-88bb2561/report.json`.
Целевой лог: `/tmp/oko-codex-errors-targeted-final.log`.

Это выбранные серверные/контрактные проверки, а не полный `check all`.
Браузерные сценарии не запускались: поведение UI не менялось, TypeScript проверен.
Все источники/обработчики синтетические, SQLite/файлы временные. Пользовательский
экземпляр не перезапускался. Исторический отчёт предыдущего рефакторинга сохранён.
