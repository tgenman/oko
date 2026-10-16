# Telegram API/UI #5: фактическая проверка

Дата: 2026-10-16. База: `798cd0b1884a1dbeee554a1e436fc97ec19e7c48`.
Issue: https://github.com/tgenman/oko/issues/5 (актуальное описание и пустые comments прочитаны перед работой).

## Результат

Одна форма web/personal/collection использует generated v3 SDK. Web выбран по умолчанию, без auto_read и требования профиля. Начальная история отправляется как exact count/date, default20. Клиент допускается по проверенной роли, auto_read=false. Настройки Око содержат два профиля на существующих GET/PUT; тот же PUT проверяет восстановленный аккаунт. Typed duplicate показывает всех подтверждённых владельцев и старые aliases, без переключения доступа и upsert. Редактор использует предметный feed из v3 effective settings, показывает закреплённый доступ и старый alias до сопоставления; reconnect-форма удалена.

Удаление требует явного подтверждения локальной очистки, показывает deleting/ошибку и использует существующие каталог/status. Нет отдельного polling endpoint. Command contexts Telegram/profile/remove отправляются через общий tracker. Успех записи согласует данные и отправленный черновик до проверки права навигации. RSS/YouTube/processor forms и их самостоятельные операции сохранены.

## Прослеживаемость критериев

| Критерии | Доказательство |
| --- | --- |
| C05.06, C05.08, C06.02 | `test_v3_telegram.py`: omitted/web/personal/collection, сохранённый count20, null/empty/incomplete422 без фида. `test_initial_history.ts`: exact date/timezone. История/cursors/count manifest/live-пауза — предметные отчёты #19/#20 и HTTP history-сценарии. |
| C06.01, C06.06, C11.04 | В новом UI нет OPML/topics; группы остаются целиком, доступ без fallback. Новая форма и связанный набор истории/меню/YouTube/RSS/Codex проходят. Worker/ReadSync — отдельное доказательство #19. |
| C06.03–05, C06.10–12 | 15 HTTP bindings: identity/profile-generation/race/claims/multiple owners/adoption/delete. Четыре новых варианта v3: точные user IDs, typed duplicate, immutable PATCH и command receipts. Browser realHTTP: две роли, конфликт одинакового аккаунта, web без аккаунта. Отдельный browser legacy: alias без выдуманной роли, переход к профилям без reconnect. |
| C06.07, C11.08 | Настоящий browser HTTP: confirmation, локальное удаление, исчезновение фида, новый ID при re-add. Crash/restart/late result/claims и запрет ack — HTTP bindings из #18, сохранены текущим прогоном. |
| C06.08 | Настоящий browser HTTP дополнительно открывает web-пост: текст Telegraph и локальная картинка видны; неподдержанный iframe оставляет явную причину неполноты. Версии/дополнение/политика media подтверждены предметным отчётом #20. |
| C11.07, C11.09–10 | Browser profiles/default20/typed popup/legacy alias; общий history formatter сохраняет absent и not_started. Проверки задержанных profile/connect/catalog replies и черновиков. |

## Фактические запуски

Все источники/аккаунты/модели синтетические, SQLite и файлы временные. Личные источники не запускались, Computer Use не использовался.

- RED новой формы: 3 browser failures, отсутствовали новые controls/API. После реализации 3 PASS.
- `check python types --files test_v3_telegram.py test_v3_connections.py test_telegram_bindings.py`: **24 PASS**, contracts/types PASS; 4.86 s. Report `20261016T052939Z-27db703d/report.json`.
- Прямой browser по initial_history/feed_debug/ui_lifecycle/separate_menus: **27 PASS**, 14.40 s (`/private/tmp/oko5-linked-ui.log`). Старые reconnect-сценарии заменены проверками новой согласованной формы и профилей; skips не добавлены.
- `check browser --files test_telegram_connection.ts test_telegram_v3_http.ts test_settings_navigation.ts test_settings_v3_http.ts test_connections_v3_http.ts test_youtube_settings.ts test_codex_settings_http.ts`: **14 PASS**, browser/tooling types PASS, 13.78 s. Report `20261016T053242Z-41f9d4cf/report.json`.
- Отдельный новый legacy UI: **1 PASS**, 2.17 s (`/private/tmp/oko5-legacy-ui.log`).
- Настоящий HTTP browser расширен после первого успеха подключением обеих ролей, сохранённой картинкой/Telegraph и неполнотой iframe: **1 PASS**, 4.62 s (`/private/tmp/oko5-media-ui.log`). Это ограниченный повтор, не весь browser.
- `npm run typecheck`: frontend/browser/tooling PASS, включая финальный повтор перед commit. `npm run build`: PASS, generated API current.
- `check staged`: PASS (4.17 s), точный staged TypeScript, report `20261016T053616Z-7bae2c94/report.json`.
- Первый realHTTP UI запуск: FAIL, Telegram link отсутствовал в фактически используемой группе подключений. Добавлен в существующий shell-раздел; повтор PASS. Следующая версия дополнительно ограничила locator текущим диалогом.
- Ревью нашло late-success draft: новый RED воспроизвёл оставшийся `@submitted`; исправление переносит очистку совпавших значений до context gate. Повтор всего нового файла **4 PASS**, 4.30 s (`/private/tmp/oko5-late-green.log`).
- Полный all/tools не запускался на промежуточном шаге; один итоговый прогон остаётся после #7. Исторические отчёты не переписаны, новые skips/хуки/approval gates не вводились.

## Ревью и визуальная проверка

Скилл code-review: две независимые оси. Spec — 0 замечаний. Standards — 1 нарушение правила очистки черновика после успешной записи; исправлено и подтверждено RED/GREEN, focused re-review — 0. Смells — 0.

Desktop/mobile Playwright snapshots сохранены в `evidence/task5/`: connect, profiles, duplicate и delete. Просмотрены через view_image. Controls доступны при прокрутке, текст предупреждения и кнопки помещаются на mobile. Добавления/редизайна вне согласованного сценария нет.

## Ограничения

Публичные HTML/Telegraph и две личности подтверждены синтетическим transport, не реальными аккаунтами/CDN. Ограничение web peer identity остаётся в описании источника и README. Переходный v2 каталог/статус читателя пока существуют; их окончательное удаление — задача #7. #21 отдельно сводит итоговую Telegram-приёмку и не подменяется этим отчётом.
