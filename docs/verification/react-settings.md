# React-настройки и подключения — 2026-10-14

Тикеты 08–15 реализованы через strict TypeScript, React и generated OpenAPI SDK. Меню, редактор полей, параметры фида, RSS, Telegram, OPML, модели и YouTube имеют собственное состояние; успешная запись согласует каталог независимо от права показать сообщение или выполнить переход. Старые renderers перенесённых сценариев удалены.

Проверки выполнялись через production HTTP и Playwright с временными хранилищами и синтетическими источниками/моделями. Личные Telegram-аккаунты, подписки и модели не использовались.

## Фактические проверки

- `npm run typecheck` и `npm run build`: passed; сборка также проверяет OpenAPI и актуальность generated Python/TypeScript libraries.
- Первый запуск браузерных проверок был NOT RUN: sandbox запретил `ps`, который runner использует для очистки своих процессов. Повтор с разрешением запуска проверок состоялся.
- Начальный набор настроек: 33/38 passed. Три теста читали запрос сразу после click, до асинхронной отправки generated SDK; теперь они ожидают запрос через существующий HTTP seam. Две регрессии исправлены: краткий статус YouTube и busy истории после закрытия/повторного открытия.
- Ограниченный повтор затронутых файлов: 12/12 passed (`test_existing_history`, `test_initial_history`, `test_youtube_history`, `test_youtube_settings`).
- Lifecycle/appearance/real HTTP history: 10/12 passed. Исправлено потерянное accessible description ссылки поля; real HTTP poll допускает отсутствие истории до завершения POST.
- Дополнительный RSS сценарий save A → draft B → close/reopen → response A действительно провалился до исправления: форма уничтожала B. После исправления green; при согласовании записи остаются новые название и глубина истории. Один промежуточный запуск остановился на защите устаревшей сборки; после пересборки сценарий выполнен.
- OPML: настоящий HTTP проверяет конфликт двух папок и подтверждение выбора; задержанный preview предыдущего файла не заменяет план нового файла. Passed.
- Интеграция Models и API/status: 32/32 passed, включая 9 Codex/llama сценариев и настоящий HTTP загрузки 60 материалов YouTube с сохранением viewed при повторном выборе.
- После интеграции reading/status на базе `6146286`: финальный набор 45/45 passed: Telegram, initial/existing history, pause/outcomes, YouTube history/settings, separate menus, lifecycle, settings navigation. Runner report: `20261014T135758Z-1ec0e168`.
- Отдельно feed-tree/appearance: все 6 сценариев passed; Playwright снимки дерева настроек mobile/desktop просмотрены. Models mobile и RSS mobile/desktop также просмотрены; формы сохраняют прежние controls, подписи и отсутствие горизонтального overflow.
- Git staged hooks passed для коммитов реализации и обоих integration merges.

После финального набора добавлена проверка disabled текущей формы во время pending history перед её закрытием: red обнаружил несовпадение ключа в карте pending-операций. Исправление сохраняет блокировку текущего запроса и снимает право прежней операции управлять повторно открытой формой. `typecheck`/`build` passed; ограниченный повтор history/pause/real HTTP YouTube — 7/7 passed (`20261014T140435Z-da51ac9a`). Остальные прошедшие наборы после этого изменения не повторялись.

Снимки текущих RSS-форм сохранены рядом с отчётом: [desktop](react-settings/rss-desktop.png), [mobile](react-settings/rss-mobile.png). Проверки модели до интеграции и их ограничения сохранены в [отдельном отчёте](react-model-settings.md); интеграция здесь проверена без временного harness.

Полный suite и независимое Standards/Spec review всей миграции выполняются главным integration-исполнителем. Этот отчёт относится к настройкам и подключениям и не заменяет общую приёмку.
