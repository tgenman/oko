# Все накопленные изменения в main и рабочий выпуск — 2026-10-19

Запрос пользователя: обновить/перезапустить рабочее Око для просмотра новых настроек
фида и влить всё в main. Начальная ветка main и origin/main совпадали:
`ddaf565b873dc2de6e6091efc99da9f3648a7dbb`.
Снимок до staging: `/tmp/oko-main-live-start-20261019`.
Включены настройки фида B, полные заголовки Telegram/представление Markdown-ссылок,
команда обновления origin/main, тесты и документы. Личные backups исключены.

## Проверки общего снимка

`./scripts/check all --compact --browser-workers 2` создал изолированный checkout:
`/private/var/folders/_g/b8j10z1d3db8wtgxyks8jk1m0000gp/T/oko-check-w7tk5v9w/checkout`.
Первое дерево: `120a981f772949d8a882cbbd786598c7c8bbe2b6`.
Отчёт: `.scratch/check-runs/20261019T151008Z-a9717000/report.json`.

- PASS contracts, tooling-types, syntax, lint, types, browser-types, build,
  HTTP, diff, entrypoint; 135 инструментальных сценариев passed.
- Python: 521 passed, 2 failed.
- Browser: 171 passed, 1 failed, 1 cancelled по тайм-ауту, 2 заранее отключённых skipped.
  Полный прогон не считается успешным.

Одна серверная ошибка — заголовок legacy Telegram-фида с transport=rss подменялся
первой строкой статьи. `_reading_heading` теперь исключает RSS, в обоих путях
списка и открытого материала. Сохранённые версии и содержимое не переписываются.
Остальные падения — ожидание owned caption worker при истекшей client presence,
45-секундный deadline YouTube history и page.goto timeout в YouTube settings.
Причина тайм-аутов не объявляется доказанной по успешному повтору.

После исправления staging и проверочная копия побайтово совпали по Git tree:
`935b2e4fa3242d28be7fe3061e03cdc633e80154`.
Повтор Python: test_telegram, test_youtube, test_news_titles — **39 passed**,
контракты PASS. Отчёт в проверочной копии:
`.scratch/check-runs/20261019T200351Z-439cdcba/report.json`.
Повтор browser: test_youtube_history_http, test_youtube_settings, test_material_list,
test_news_titles — **15 passed**, tooling/browser types PASS. Отчёт:
`.scratch/check-runs/20261019T200354Z-6581393c/report.json`.
Успех относится к выбранным сценариям; полный all после исправления не повторялся.

Независимое ревью дополнительных изменений updater/заголовков и затем исправления
legacy RSS завершилось без actionable findings. Ранее отдельное ревью и проверки
настроек фида сохранены в `.scratch/feed-settings/verification.md`.
Этот документ добавлен после проверки; он не меняет код или состав тестов.

## Поставка

Перед обновлением рабочий выпуск `080d5555491d16310251106e37ce539cb9eb1d19`
имел healthy контейнер и загруженный helper; runtime_state=running, work_allowed=true.
Поставка выполняется штатным `scripts/update-working-oko` после публикации main:
fetch/фиксированный SHA/чистый clone/prepare/update с резервной копией/stop/start.
Фактический выбранный SHA, предыдущий выпуск и backup записывает штатный controller
в приватные manifests установки. Данный раздел описывает процедуру, а не выдаёт
её за завершённый запуск. Результат перезапуска проверяется после команды через
controller status и настоящий браузер настроек без сохранения личных полей.
