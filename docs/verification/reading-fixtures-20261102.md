# Fixtures чтения — проверки 2 ноября 2026

База: `c31612a8639f9001eeea04eec8d524accceefedc`. Проверялись изменения
синтетических браузерных snapshots публикаций, карточек и навигации; серверные
источники и модели в проверках были синтетическими, хранилище — отдельная
PostgreSQL 18 с временными схемами.

- `npm run typecheck:browser` и `git diff --check`: успешно.
- `./scripts/check ui`: syntax, lint, типы, сборка, 5 HTTP и 38 Node-проверок
  прошли; Playwright: 152 прошли, один упал и один прерван по timeout.
  Не прошли `rss root stays readable while a separate article fails and updates its theses`
  (ожидание кнопки обновления) и `reading shows the first page and loads the next on scroll`
  (timeout 90 с). Отчёт: `.scratch/check-runs/20261102T075919Z-62223030/report.json`.
- Изолированный повтор первого сценария: 1/1 успешно
  (`.scratch/check-runs/20261102T080247Z-4760d181/report.json`).
- Изолированный повтор второго сценария: 1/1 успешно
  (`.scratch/check-runs/20261102T080306Z-ef53732b/report.json`). Эти повторы
  не делают полный UI-прогон успешным.
- Новый сценарий передачи HTTP 404 и сохранения открытой публикации: 1/1 успешно
  (`.scratch/check-runs/20261102T074916Z-44bf05ee/report.json`).
- Независимое ревью обнаружило три проблемы: зависимость карточки от результата
  публикации, fallback неизвестного scope/cursor и превращение HTTP-ошибки в
  исключение. Исправления повторно рассмотрены; блокирующих замечаний не осталось.
- Снимок `opened-feed-identity-b.png` из прогона 35 сценариев просмотрен.

Полный UI-прогон не подтверждён успешным; необходим результат CI точного коммита.
Личная база, источники и настоящий обработчик не использовались.

## Интеграция с новой публикацией

Коммит подготовлен поверх `3a847351d7f000e3ddf19317547f463650146f55`
(PR #194): из fixtures исключены удалённые контрактом `source_position`,
`originals` и `media.id`. В изолированной копии выполнен
`./scripts/check ui --files tests/browser/test_opened_material.ts tests/browser/test_material_markdown.ts tests/browser/test_material_list.ts tests/browser/test_reading_tree.ts tests/browser/test_completion.ts tests/browser/test_cards_cursor_http.ts`:
сборка, lint, типы, 5 HTTP, 38 Node и 42 Playwright-сценария прошли; diff прошёл.
Снимок `opened-feed-identity-b.png` из этого прогона просмотрен.
Отчёт: `/private/tmp/oko-reading-fixtures-integration/.scratch/check-runs/20261102T080537Z-c3a8009d/report.json`.

## Полный UI-прогон после интеграции

Для интегрированного кода на базе `3a847351` выполнен полный `./scripts/check ui`:
сборка, lint, типы, 5 HTTP и 38 Node-проверок прошли; Playwright: 153 прошли,
один отменён по timeout 90 с — `reading shows the first page and loads the next
on scroll`. Итог набора `browser`: FAIL. Отчёт:
`.scratch/check-runs/20261102T080819Z-73daa40d/report.json`.
Этот же сценарий прошёл в ограниченном интеграционном прогоне 42 сценариев до
полного запуска; успех ограниченного набора не делает полный набор успешным.

Изолированный повтор после этого полного прогона: 1/1 успешно, 29,4 с;
`.scratch/check-runs/20261102T081147Z-740cf1ec/report.json`.
