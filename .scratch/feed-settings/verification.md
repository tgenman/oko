# Проверка реализации настроек фида B

Дата: 2026-10-19
Статус: реализация и заключительная проверка завершены.

## Объём

Основные поля Title/Folder сохраняются атомарно. Link/Additional links неизменяемы,
с маленькими кнопками Copy link. Activity хранит неизвестность старых дат и считает
полный Unread. Описание берётся из RSS/Atom или веб-метаданных Telegram, без запросов
при открытии настроек. Остальные параметры остались в существующем редакторе.

Прототип сохранён в локальной ветке `codex/feed-settings-prototype-20261019`,
коммит `1b1516cf09621304bdc9e9cde06908f85539bb6f`; throwaway-исходники и команда
запуска удалены из рабочего frontend. Указатель — `docs/agents/prototype-archive.md`.

## Выполненные проверки

- `npm run typecheck`: успешно на финальной реализации.
- `npm run api:generate`, последующие проверки API библиотек при сборке: успешно.
- `npm run build`: успешно на финальной реализации.
- `mypy oko`: успешно, 75 файлов, после исправлений хранения и профилей.
- Целевой Python: 29 passed (feed details, v3 settings, feed completion).
- Дополнительный Python: 19 passed (contract, RSS, feed details).
- После ревью: 22 passed (feed details, Telegram bindings).
- Независимое ревью: замечания Reset/customValidity, холодного GET, фокуса после
  начального GET и Modified при legacy profile mapping исправлены; повторное
  ревью завершено без новых actionable findings.

## Браузер и прежние неуспешные запуски

Проверки выполняются Playwright через настоящий HTTP, с отдельными временными
хранилищами и синтетическими источниками. Личные источники и обработчик не запускались.

В раннем Python-запуске 20 сценариев прошли, 4 completion-сценария не стартовали из-за
устаревшей сборки после изменения API. После сборки и исправлений целевые 29 прошли.
При разработке новые тесты также нашли некорректный ответ 500 для пустого Title;
исправлено на 422. В тестах уточнены ожидание фильтрации и соблюдение source backoff.

Первый браузерный запуск начался до готовности новой сборки: 24 failed на startup.
Следующий: 17 passed, 4 failed (cleanup отменённого route в новом тесте, гонка возврата
на страницу без начального snapshot, отсутствующий выбор папки в двух существующих
RSS fixtures). Cleanup, загрузка отсутствующего snapshot при возврате и preconditions
тестов исправлены. Повтор: 20 passed, 1 failed (тот же сценарий model navigation).
После исправления загрузки и ревью новый повтор в рабочем checkout не стартовал:
27 failed на startup, поскольку параллельные сторонние изменения frontend сделали
сборку устаревшей. Эти результаты не считаются успешной проверкой финального кода.

Ранние скриншоты 1440×1050 и 1024×900 просмотрены. Финальные снимки 1440×1050
и 820×900 из снимка 08 просмотрены и сохранены в docs/verification/feed-settings-B-*.png. Проверяются также узкий редактор, длинные значения,
несколько/отсутствие URL, clipboard отказ, неизвестные даты, cold failure/retry,
сохранение фокуса/черновиков и поздние ответы после переключения/повторного открытия.

## Изолированное дерево

База: `ddaf565b873dc2de6e6091efc99da9f3648a7dbb`.
Начальный снимок: `/tmp/oko-feed-settings-implementation-start-20261019`.
Подготовленный индекс: `/tmp/oko-feed-settings-ready-05`.
Проверочная копия: `/tmp/oko-feed-settings-check-05`.
Дерево задачи: `8b7891a075dfab8938ef41996fff25e60f20d18f`.

`task_changes.py` исключил прежние пользовательские изменения. Локальная спецификация
существовала как untracked до начала реализации и дала конфликт трёхстороннего
наложения; для проверки кода она оставлена отдельно в основном checkout. Кодовая
копия содержит только явно выбранные исходники, generated, тесты и документацию задачи.

Полная команда:

```sh
OKO_PYTHON=/Users/dabondarev/repo_personal/oko/.venv/bin/python \
NODE_PATH=/Users/dabondarev/repo_personal/oko/node_modules \
./scripts/check all --in-place --compact --browser-workers 2
```

Отчёт: `/tmp/oko-feed-settings-check-05/.scratch/check-runs/20261019T145920Z-5558febc/report.json`.
Полный прогон 05: серверные проверки успешны (517 Python и отдельно 9 HTTP),
инструменты — 135 passed; контракты, lint, syntax, types, сборка, entrypoint и diff
успешны. Browser: 165 passed, 6 failed, 2 skipped. Два skip существовали до задачи
(coexistence/BFCache и closing/keepalive, отключены по просьбе пользователя).

Шесть ошибок браузера: пустая папка в RSS/Telegram connection fixtures, старый
Telegram DTO каталога, отсутствующий список адресов в старом ответе и два новых
cold-load теста, которые делали same-document goto вместо перезагрузки.
Исправлены предусловия фикстур, новый блок принимает отсутствие адресов как
Not available, cold-load тесты используют reload. Никакие реальные подключения
и продуктовые defaults ради прохождения тестов не подменялись.

Снимок 06: tooling/browser types прошли, browser не стартовал из-за отсутствия
сборки в новой копии. Этот запуск не считается проверкой поведения.
Снимок 07: после сборки 37 passed, 1 failed (оставшийся legacy Telegram fixture).
Снимок 08: после исправления фикстуры и сборки все 38 выбранных browser-сценариев
успешны; tooling/browser types также прошли. Выбраны восемь файлов: test_feed_details,
test_connections_v3_http, test_feed_debug, test_settings_navigation,
test_existing_history, test_separate_menus, test_telegram_connection,
test_telegram_v3_http. Они включают все шесть ошибок полного прогона и соседние
сценарии. Успех относится к этому повтору; полный all после него не повторялся.

Финальный подготовленный индекс: /tmp/oko-feed-settings-ready-08.
Проверочная копия: /tmp/oko-feed-settings-check-08.
Дерево: 267a2c2e6d7ca55299bfa6cf772eeb8fccace818.
Заключительный отчёт: /tmp/oko-feed-settings-check-08/.scratch/check-runs/20261019T150616Z-69e2ffeb/report.json.
Копии обоих отчётов сохранены рядом как check-all-report.json и check-browser-report.json.
После проверки также успешно выполнен npm run build рабочего checkout с параллельными
изменениями. Пользовательский экземпляр приложения не перезапускался.
