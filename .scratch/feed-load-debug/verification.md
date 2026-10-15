# Задержка выбора фида — 2026-10-15

Причина: старый refresh после выбора новой области назначал очередной poll, пока новый refresh ещё загружал карточки. Он увеличивал refreshSequence в main.tsx, поэтому ответ выбранного фида отбрасывался. Несколько циклов могли повторять это неограниченно.

Исправление: frontend/src/reader-session.ts разрешает планирование следующего poll только последнему вызову refresh в текущем поколении сессии. Интервал опроса и контракты API не менялись.

## Фактические проверки

- Исходный экземпляр, read-only HTTP: status 0.067 s, navigation 0.291 s, cards 0.250 s (79 карточек). Серия из 30 запросов cards: 0.255–1.074 s.
- Playwright на исходном экземпляре: выбор Isaac Arthur, 0 карточек и timeout после 15 s при успешных отдельных HTTP-ответах. После пересборки: 26 карточек за 1361 ms. Просмотрен screenshot. Сервер не перезапускался; сбор/обработчик вручную не запускались.
- Новый синтетический browser regression до исправления: 0 карточек за 2000 ms при задержке ответа 900 ms. После исправления проходит, только один запрос выбранного фида до показа карточки.
- npm run build: PASS, включая strict TypeScript и проверку API generation.
- ./scripts/check ui --files tests/browser/test_reader_refresh.cjs tests/browser/test_reading_tree.cjs tests/browser/test_ui_lifecycle.cjs: syntax/lint/build/http/diff PASS, 11 HTTP-тестов PASS, 12 browser-тестов PASS. Общий результат FAIL: checkout guard зафиксировал параллельные изменения tests/browser/README.md, tests/development/test_process_logs.py и tests/support/processes.py. Эти файлы не менялись в рамках данной задачи.
- Независимый повтор ./scripts/check browser с теми же тремя файлами: PASS, 12/12, checkout guard PASS. Просмотрен синтетический screenshot.
- Отчёты: ../check-runs/20261015T060623Z-b975dbf2/report.json и ../check-runs/20261015T060732Z-32b37eb8/report.json.
- Независимое ревью diff от b3a8954: actionable findings отсутствуют; проверены poll ownership, dispose/restart и граница регрессии.

Полный Python-набор не запускался: изменение ограничено браузерным планированием. Открытая пользовательская вкладка получает новую сборку после обновления страницы.
