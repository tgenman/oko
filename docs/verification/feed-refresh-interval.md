# Проверка интервала обновления фидов

Дата: 2026-10-19. Задача: https://github.com/tgenman/oko/issues/46.
Ветка: `codex/feed-refresh-interval`. База ревью: `9e9f206e4439d6c04091b41a90e33a132d055f3e`.
Спецификация: [интервал обновления](../architecture/feed-refresh-interval.md).

## Объём

Общий интервал 1–1440 минут в Настройках Око, default/reset 15; сохранение между запусками. Пересчёт от последней автоматической постановки в очередь, без отмены сбора/ИИ и без сдвига таймера ручным обновлением. Существующие закрытые ответы настроек и enum ключей исходов команд сохранены; новый GET читает интервал отдельно.

## Фактические проверки

Проверки используют временные SQLite/файлы, синтетические источники/модели и Playwright с настоящим HTTP. Личные подписки, Telegram и настоящие модели не использовались. Запущенный пользовательский экземпляр не перезапускался.

- TDD: новый HTTP-тест сначала падал на отсутствии интервала; проверка таймера выявляла прежние 300 секунд; браузерный сценарий сначала не находил новый пункт. Затем соответствующие сценарии прошли.
- Чувствительность проверок отмены: временное возвращение прежней отмены после записи настроек вызвало оба ожидаемых падения — сбор и ИИ отменялись. Рабочие байты восстановлены. Лог: `/tmp/oko-feed-interval-tdd-cancellation.log`.
- Первоначальные целевые проверки: 22 Python HTTP-теста и 2 браузерных сценария прошли. Desktop/mobile screenshots просмотрены; переполнения диалога нет.
- Публикация одной задачи проверена экспортёром: `verified: true`. План и receipt сохранены в `.scratch/feed-refresh-interval/`; полные API-снимки и действия — `/tmp/oko-feed-interval-*.json`.

### Первый полный прогон

В изолированной копии подготовленного дерева:

```sh
OKO_PYTHON=/Users/dabondarev/repo_personal/oko/.venv/bin/python \
NODE_PATH=/Users/dabondarev/repo_personal/oko/node_modules \
./scripts/check all --in-place --compact
```

Отчёт: `/private/tmp/oko-feed-interval-check/.scratch/check-runs/20261019T123658Z-158e145e/report.json`.

Результат: contracts, TypeScript/Python types, syntax, lint, build, HTTP, browser, diff, entrypoint и tool-tests прошли. HTTP — 9 тестов; browser — 158 passed, 2 skipped; tool-tests — 135 passed. Python — 465 passed, 1 failed: `test_upgrade_preserves_individual_results_and_removes_group_work`, проверявший точный состав прежнего ответа настроек. Полный прогон не был зелёным.

Ошибка совпала с замечанием Standards review о закрытом ответе API. Вместо изменения ожиданий старого теста сохранена прежняя форма ответа и добавлено отдельное чтение интервала. Добавлена регрессия по закреплённой схеме из базового коммита, включая контекст команд и failed/unknown исходы.

### Промежуточные ограничения и исправления

- Первые попытки runner в sandbox не запускались из-за запрета `ps`; проверка повторялась с разрешениями на локальный тестовый процесс.
- Автоматическая повторная копия `all` не нашла Node-пакеты по одному `NODE_PATH`. Изолированной копии подключён установленный `node_modules`; последующие прогоны выполнялись непосредственно в ней.
- Прямая браузерная проверка до пересборки останавливалась на stale frontend build. Это не результат проверки UI; нужная сборка выполнена runner.
- Один повтор проверки изолированной копии остановился на неполной синхронизации тестового type mapping и generated `operations.py`. В копию заново перенесён полный явный состав задачи.
- Проверка типов выявила отсутствие explicit discriminator mapping у нового origin union. Mapping добавлен в OpenAPI, библиотеки пересозданы.
- Новая проверка исходов команд первоначально ожидала две одновременные текущие проблемы для одной цели. Журнал сохраняет актуальную; исправлен только тест: каждый исход проверяется сразу после наблюдения.

## Независимое ревью

### Standards

Первый проход нашёл несовместимость закрытого ответа настроек; повтор — выход нового значения enum через receipts/status. Оба замечания исправлены. Итоговый повтор после `6536906`: actionable замечаний нет. Проверены сохранение прежних схем, согласованный target-only контекст `keys=[]` и сброс неудачного черновика.

### Spec

Первый проход и повтор после исправления HTTP-границы: замечаний нет. Подтверждены согласованные область, диапазон, default, сохранение, таймер, отсутствие отмены и владение черновиками.

## Финальный повтор

В той же изолированной копии выполнено:

```sh
OKO_PYTHON=/Users/dabondarev/repo_personal/oko/.venv/bin/python \
NODE_PATH=/Users/dabondarev/repo_personal/oko/node_modules \
./scripts/check build browser python --compact --files \
  tests/reader/test_v3_settings.py tests/reader/test_feed_refresh_interval.py \
  tests/reader/test_remove_aggregates.py tests/api/test_contract.py
```

Отчёт: `/private/tmp/oko-feed-interval-check/.scratch/check-runs/20261019T124539Z-2b8628cb/report.json`.

PASS: contracts, tooling-types, browser-types, build, весь browser (159 passed, 2 skipped) и выбранные Python-наборы (28 passed). Исправленный тест миграции проходит. В браузерном наборе прошли все 3 новых сценария интервала, включая сброс неудачного черновика. Финальные [desktop](feed-refresh-interval-desktop.png) и [mobile](feed-refresh-interval-mobile.png) снимки просмотрены.

После исправлений повторены затронутые наборы; полный `all` повторно не запускался. Результаты неизменных syntax/lint/mypy/HTTP/tool-tests/entrypoint сохранены из предыдущих прогонов. Финальный discriminator mapping не меняет JSON-значения или прежние DTO.

В рабочем checkout отдельно прошли `npm run build` и `./scripts/check staged` (точный API-контракт, generated-библиотеки и staged TypeScript). Последний отчёт: `.scratch/check-runs/20261019T124727Z-e065597f/report.json`.

База проверки не содержит посторонних изменений глоссария, прототипов и backups. Изменения закоммичены в рабочей ветке; push/merge и перезапуск пользовательского экземпляра не выполнялись.
