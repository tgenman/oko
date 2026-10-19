# Реализация меню папки — 2026-10-19

## Объём

Реализован выбранный пользователем вариант A: Mark All as Read, Settings…, Rename…,
Delete…. Непустая папка показывает Folder Is Not Empty; удаляется только папка без
всех фидов. Проверка пустоты атомарна с удалением её настроек.
Новые папки получают случайные внутренние ID; переименование не меняет существующие
ID и связи. Подключение RSS/YouTube/Telegram использует единое разрешение названия
папки в сохранённый ID. Старые ID и локальные предпочтения не мигрируют.

Прототип сохранён отдельно: `codex/folder-menu-prototype-20261019`,
коммит `6906d5d767094a88e578226b2c3371152660ae82`. Решение пользователя и исходники
сохранены в этом коммите; throwaway-исходники убраны из рабочего frontend.

## Изоляция

База: `9b0424d2b7fc8c20b1fa678ddf455a660f53e7aa`.
Исходное dirty-состояние: `/tmp/oko-folder-menu-implementation-start-20261019`.
Финальный проверочный снимок: `/tmp/oko-folder-menu-check3-20261019`,
task tree `f590b26b1e581d8adf56da8051b8bc6c9c5c01ee`.

В снимок вошли 34 файла задачи. Прежние и параллельные изменения list.tsx,
общего style.css, других прототипов, Telegram-черновиков и backups исключены.
Общий CSS не содержит добавленных этой задачей стилей: они в folder-menu.css.

## Завершённые проверки

- Frontend и browser TypeScript: успешно.
- HTTP: folder management (6), subscriptions (5), folder completion (7), API
  contract (2) — 20 passed. Проверены старые ID после перезапуска, наследование
  настроек, повторное использование названия и повторное создание папки, запрещённые
  названия, blocked delete, receipts и согласованность status после ошибки.
- Playwright: новые folder menu (4), прежние folder completion (2), feed menu (3) —
  9 passed. Настоящий HTTP reader и синтетические источники, плюс управляемые поздние
  ответы. Скриншоты меню и предупреждения сняты и просмотрены.
- Независимый reviewer проверил точную базу и task tree
  `d071d7897221e33e9adca37c5dd65b8b4760c273`: замечаний не обнаружено. После него
  сделана только явная конвертация SQLite ID в str для строгой Python-типизации.

До сборки целевые HTTP-запуски останавливались на freshness-проверке assets
(mixed contract revisions / stale build); после сборки все 20 сценариев прошли.
Первый полный прогон изолированной копии прошёл lint, frontend/browser/tooling
синтаксис/типизацию, build и HTTP, но остановился на mypy no-any-return в новом
ensure_folder. Исправлено; повторный полный прогон записывается ниже отдельно.

## Ограничения

Пользовательский экземпляр не перезапускался. Личные подписки, Telegram-аккаунты и
настоящие обработчики не использовались. Production-код не коммитился и не публиковался.

## Полный прогон и финальный повтор

Полный `./scripts/check all --in-place --compact` на снимке check2:

- Browser: 139 passed, 2 skipped, 0 failed (82.41 s).
- Python: 455 passed, 4 failed. Два падения — старый счётчик операций 53 вместо 55
  и обязательные examples новых 409 union. Исправлены счётчик, примеры union и
  ответов новых путей; SDK регенерированы.
- Ещё два падения — YouTube: ожидание ошибки caption worker и удаления карточки
  будущей трансляции. Отдельно после сборки не воспроизвелись; причина не установлена.
- Tool tests: 125 passed. Contracts, синтаксис, lint, типизация, build, HTTP,
  entrypoint и diff прошли. Весь полный прогон не считается успешным.

Полный отчёт: `/tmp/oko-folder-menu-check2-20261019/.scratch/check-runs/20261019T103625Z-03ad1cbf/report.json`.

Финальный повтор после свежей сборки снимка check3:

```sh
./scripts/check python types http --files tests/api/test_v3_schema.py tests/api/test_contract.py tests/reader/test_folder_management.py tests/collection/test_youtube.py --compact
```

PASS: 39 Python (18 schema, 2 HTTP contract, 6 folder management, 13 YouTube),
9 HTTP/delivery, contracts и types. Длительность runner 10.36 s.
Повтор относится к этим наборам; полный all после него не повторялся.
Отчёт: `/tmp/oko-folder-menu-check3-20261019/.scratch/check-runs/20261019T103959Z-6b9e8f6b/report.json`.
До свежей сборки запуск check3 останавливался из-за отсутствия frontend assets;
после `npm run build` тот же целевой набор прошёл.

Независимый reviewer дополнительно проверил delta до финального tree
`f590b26b1e581d8adf56da8051b8bc6c9c5c01ee`: замечаний нет.
Рабочий checkout также собран (`npm run build`), diff не содержит whitespace-ошибок.
Временный сервер выбранного прототипа на 5186 остановлен после архивирования.

## Интеграция с параллельным изменением #38

После предыдущих проверок внешний процесс обновил HEAD на
`4a6d47d1ebee35e52f19e42b32ca63be58190c68` (merge #45) и при возврате локальных
правок оставил конфликты в folder-actions/feed-actions/coordination и двух
архитектурных документах. До разрешения сохранены байты файлов и index stages
в `/tmp/oko-folder-menu-conflicts-20261019`.

Сохранены обе доработки: меню и его явная цель/название/фокус; завершение папки
использует новый общий `reading/completion.ts`, включая блокировку повторной
отправки, unknown outcome и допуск ответов. Оба раздела документации сохранены.
Пять конфликтов разрешены и отмечены в индексе; текущая ветка/индекс не откатывались.
Backend меню и внутренних ID не изменялся при этой интеграции.

Финальная интеграционная копия: `/tmp/oko-folder-menu-integration-20261019`.
База: `4a6d47d1ebee35e52f19e42b32ca63be58190c68`.
Task tree: `5c893e773c70e292112719c8fa022b5c231f587b`.

```sh
./scripts/check ui --files tests/browser/test_folder_context_menu.ts tests/browser/test_folder_actions.ts tests/browser/test_feed_context_menu.ts tests/browser/test_completion.ts --compact
```

Все выбранные проверки PASS: 22 браузерных сценария, 9 HTTP/delivery, build,
tooling/browser types, syntax, lint и diff. Время runner 36.90 s.
Отчёт: `/tmp/oko-folder-menu-integration-20261019/.scratch/check-runs/20261019T104221Z-400316d5/report.json`.
Независимый reviewer проверил интеграционный delta относительно новой базы:
замечаний нет. Рабочий checkout повторно собран после разрешения конфликтов.
Финальные git diff и staged diff не содержат conflict markers/whitespace errors.
Полный all на новой базе не повторялся; приведённые выше результаты полного
прогона и целевого Python-повтора относятся к их точным снимкам.
