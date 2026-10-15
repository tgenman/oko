# Ускорение браузерных тестов — 2026-10-15

В отдельную ветку codex/check-cycle-cache влита origin/main 6f0a811 без конфликтов (merge b913b9f). Удаление API approval/candidate процедур сохранено коммитом 6193a14 до слияния. Основной checkout не менялся. Из main пришли новые сценарии API v3 и удаление автоматического pre-commit; hook не восстановлен.

## Замеры

| Запуск | Browser | Общий runner | Результат |
| --- | ---: | ---: | --- |
| После merge, до оптимизации, 2 browser workers (`check build browser`) | 93,44 с | 99,56 с | 120 passed, 2 skipped |
| Lazy schemas и управляемое время, 4 workers (`check browser --browser-workers 4`) | 56,68 с | 59,30 с | 120 passed, 2 skipped |
| Итоговый `check all tools`, включая параллельный startup fixture | 62,81 с | 112,89 с | все группы PASS |

Время standalone browser сократилось на 36,76 с (39,3%). Это общий эффект правок и четырёх workers, не отдельная оценка каждой оптимизации. Итоговый полный запуск включает одновременные Python/tools, поэтому его 62,81 с не являются повторным standalone benchmark. Свежий полный baseline после merge не измерялся: старые 119,92 с относятся к другому составу main. Каждый широкий замер выполнен один раз.

В полном прогоне: Python 346 passed, 91,74 с; tools 109 passed, 83,96 с; HTTP 9 passed; browser 120 passed, 2 ранее отключённых skipped. Build 11,53 с, типы, contracts, syntax/lint, diff и entrypoint PASS. После ускорения browser самое долгое звено — Python.

## Изменения и границы

- Browser validators читают generated OpenAPI JSON напрямую, без запуска Python в каждом тестовом файле и без отдельной копии components для каждого ответа. Ajv 2020-12 компилирует только использованные схемы; локальные references и formats сохранены. Импорт contracts.ts: 419,74 → 63,34 мс; импорт с двумя отрицательными валидациями после изменения — 117,85 мс. Это одиночные микрозамеры.
- В журнале, дереве и previews реальные паузы заменены Playwright clock. Изменение DOM подтверждает применение очередного polling до проверки фокуса или повторных image requests. Например journal polling: 2,89 → 0,82 с, preview reuse: 2,19 → 1,30 с в standalone замерах.
- HTTP fixture и browser context запускаются одновременно. Cleanup ждёт обе попытки запуска и освобождает ресурсы после abort/ошибки; сценарии сохраняют отдельные context, приложение, токен, порт и хранилище.
- По умолчанию 4 browser workers; `--browser-workers 2` сохранён. Полный прогон подтвердил работу четырёх workers вместе с двумя Python и двумя tools workers. Один успешный прогон не является доказательством отсутствия редких flakes.
- Тесты не удалялись, новые skip не добавлялись, production-код не менялся.

## Проверка

Целевой набор contracts/material-list/reading-tree/feed-debug PASS. После изменения startup проверены HTTP isolation (включая abort во время подготовки), browser context, contracts и 40 тестов runner — PASS. Независимый reviewer обнаружил недостаточный маркер завершения polling в previews; он заменён на изменение заголовка карточки и ожидание этого заголовка в DOM. Повторное ревью исправления и startup не выявило замечаний.

Полный запуск выполнен из подготовленного дерева задачи 045897a18f152fb69dfaefcf665e03d324344e4d, база b913b9f, каталог /tmp/oko-browser-check. После него изменялись только инструкция и этот отчёт. `git diff --check` PASS.

Отчёты и полные логи сохранены в `.scratch/check-runs/browser-speed-20261015/` (локальный игнорируемый архив). Исходные отчёты: `/tmp/oko-browser-before/20261015T165534Z-d3951ca1/report.json`, `/tmp/oko-browser-after4/20261015T165940Z-e0eab627/report.json`, `/tmp/oko-browser-full-after/20261015T170156Z-b232b30f/report.json`.
