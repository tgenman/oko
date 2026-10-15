# Проверка задачи 03: чтение и действия над материалами

Date: 2026-10-15
GitHub: https://github.com/tgenman/oko/issues/3
Branch: codex/independent-api-task3
Base: 7c435f3f28e0ae8a269e31793a8754dc96a137b9 (main с PR #13 и ускорениями проверок)
Status: поведение задачи проверено; общий v3 остаётся candidate / PENDING_REVIEW

## Результат

Чтение карточек, состава, папок, текущего материала, версий и всех видов медиа переведено на canonical v3. DTO строятся HTTP adapter; Runtime и Store сохраняют владение выполнением, составом, фиксацией и удалением. Pending/available различаются исчерпывающим dispatch в UI. Порядок, принадлежность, счётчики, snippets, полнота, удаление источником, описание видео, фильтрация, тезисы и происхождение результата сохраняются.

Команды collect, regenerate, retry_material, retry_theses, retry_filter, restore_filter, viewed и complete_folder используют typed context, acknowledgements и persisted receipts. Конфликт состава возвращает composition_changed с новым preview. GET не выполняет viewed. Media URLs принадлежат v3, ответы no-store; выдача bytes выполняется до yield. UI сохраняет поколения открытия, AbortSignal, очистку media/object URLs, scroll/focus и межвкладочные события. Команды разных материалов имеют отдельные ключи неизвестного исхода; автоматического replay нет.

Исправлены замечания ревью и найденные тестами дефекты:

- Очистка материала больше не превращает typed receipt подтверждённой команды в abandoned. Удаляются ссылки панели на материал, сохраняются служебные факты команды.
- Причина ожидания обработки берётся из сохранённого предметного кода; retry_at переводится из монотонного срока в UTC через clocks Runtime. Running имеет приоритет над ошибкой предыдущей попытки. Ранний запрет Codex получает ai-permission.
- Актуальные принятые тезисы доступны при повторе и его ошибке. Таблица retained_theses содержит только path/digest, записанные атомарно с явным повтором ранее принятого результата. Чтение требует актуальной signature и точных bytes; файл после сбоя до commit не публикуется. Viewed удаляет marker. Добавление таблицы не переписывает содержимое, настройки или старые результаты; старый код может игнорировать её при откате.
- HTTP schema validation обнаружила прежнее несоответствие PUT processors canonical: ответы теперь CodexProcessorState/LocalProcessorState, как уже описано в YAML и generated SDK. Контракт не изменялся.
- Общий setup_filters перенесён из сценарного файла в tests/support/filters.py.

Canonical OpenAPI, baseline, generated-библиотеки, scripts/check, CI и test_check_runner.py не изменены. SHA-256 canonical: 5af08cc8e5c60f2874d728eefd48bbe34787d12664a0ea928fcc2a59e924a384. Перенос оставшихся семейств и снятие legacy consumers относятся к следующим задачам.

## Границы проверок

Использованы существующие HTTP interface (временные SQLite/файлы) и настоящий Playwright UI с синтетическими source/model adapters. Новые HTTP-сценарии валидируют фактические JSON-ответы и ошибки по canonical OpenAPI с format checker. Проверены версии и image/video/audio/circle/file, очистка current/previous media, restart receipts, скрытое и pending, explicit completion, typed errors, причины ожидания, задержанный/неуспешный повтор тезисов, инвалидация при смене входа.

Браузерные старые сценарные данные преобразуются только в synthetic transport, затем проверяются по v3 schema. productionReader использует настоящие серверные ответы без преобразования; новые сценарии проверяют потерю ответа viewed, отсутствие replay и независимость двух неизвестных команд.

## Фактические прогоны

1. Начальный HTTP red: сначала отсутствовала frontend-сборка; API-only устранил эту зависимость и подтвердил 404 отсутствующего v3 route. После реализации первый сценарий выявил abandoned вместо succeeded у viewed; исправлен.
2. Ограниченный browser без build: 23 failures на отсутствующей сборке, поведение не проверено. Отчёт `/private/tmp/oko-task3-first/20261015T153250Z-b1be5390/report.json`.
3. `check build browser --files test_opened_material.ts test_folder_actions.ts test_material_list.ts --candidate api/openapi.yaml`: 16 passed / 4 failed, 66.02 s. Причина — старые fixture media URLs и пустая source_url, запрещённые v3. Фикстуры приведены к реальным формам адресов. Отчёт `/private/tmp/oko-task3-browser/20261015T153336Z-3f34d0f7/report.json`.
4. Единственный полный `./scripts/check all --candidate api/openapi.yaml`: Python 348 passed; HTTP 8 passed / 1 failed; browser 115 passed / 4 failed / 2 ранее согласованных skipped. Types, syntax/lint, generation/drift, build, entrypoint, candidate wheel installation — PASS. 259.35 s. Отчёт `/private/tmp/oko-task3-full/20261015T153635Z-877a8842/report.json`.
   - HTTP failure: ожидаемый набор экспортируемых схем не включал новые v3 schemas.
   - Browser failures: оставшийся старый preview URL, неполная Telegram fixture, export старого SDK в transport facade, старая collect route в interception.
   - Ускоренный runner не запускал tooling suite: его inputs не изменены; Python/browser выполнялись параллельно.
5. Ограниченный повтор после исправлений: HTTP 9 passed, browser 15 passed / 1 failed, Python 54 passed / 1 failed; 58.21 s. Отчёт `/private/tmp/oko-task3-recheck/20261015T154105Z-893a3594/report.json`.
   - Fixture appearance одновременно задавала pending и changed; заменена на available/loading, соответствующий существующему сценарию обновлённой сохранённой копии.
   - Crash recovery обнаружил неподтверждённый moved theses file; исправлено через retained marker, а не ослабление теста.
6. Финальный ограниченный `check build types browser python --files test_reader_appearance.ts test_reading_v3_http.ts test_v3_reading.py test_theses_recovery.py test_theses.py test_youtube_theses_freshness.py --candidate api/openapi.yaml`: Python 33 passed, browser 4 passed, types/build/generation/drift PASS; 47.95 s. Отчёт `/private/tmp/oko-task3-final/20261015T154433Z-469e6507/report.json`.
7. Прямые HTTP-проверки в worktree: settings + reading — 21 passed; после последних уточнений reading — 7 passed. TypeScript приложения/browser/tooling и Python mypy проходили при реализации. Финальный runner повторил соответствующие types.

Полный прогон не повторялся; его результат остаётся частично неуспешным историческим evidence. Ограниченные повторы закрывают перечисленные падения и затронутые исправления, не означают нового полного PASS. Новые skips не добавлены.

## Ревью и изображения

Независимый code-review: Standards — 0 оставшихся замечаний; Spec — 0 оставшихся замечаний после проверки исправлений. База обеих осей — указанный main commit плюс рабочий diff.

Просмотрены финальные Playwright screenshots: [desktop](evidence/task3/desktop.png), [материал при mobile viewport](evidence/task3/mobile-material.png). Чтение, текст, ссылки, media и кнопки сохранены. Общая существующая компоновка при узком viewport не перерабатывалась; mobile screenshot фиксирует затронутую область материала.

Личные источники, Telegram-аккаунты и настоящие модели не использовались. Проверки candidate не разрешают выпуск v3: semantic review/baseline остаются задачей 07. Пользовательский экземпляр не перезапускался.
