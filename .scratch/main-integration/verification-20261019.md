# Интеграция накопленных изменений в main — 2026-10-19

Запрос пользователя: «влей все в main». Checkout уже находился на main,
база 4a6d47d1ebee35e52f19e42b32ca63be58190c68, совпадающая с origin/main.
Включены все накопленные исходники, тесты и документы, включая записи публикации
Telegram-задач. Локальные backups пользовательских данных исключены.
Исходный снимок: /tmp/oko-merge-all-main-start-20261019.

## Полный прогон

`./scripts/check all --compact`, изолированный снимок:
/private/var/folders/_g/b8j10z1d3db8wtgxyks8jk1m0000gp/T/oko-check-1hxtaffq/checkout
Tree: 404a3a74b0fc4255daada6372e5f035b0a9b7d4f.
Отчёт: .scratch/check-runs/20261019T112402Z-65169a4d/report.json.

- PASS contracts, tooling-types, syntax, lint, types, browser-types, build,
  HTTP/delivery (9), diff, entrypoint; checkout не изменён.
- Python: 460 passed (243 + 217).
- Инструменты: 135 passed (68 + 67).
- Browser: 152 passed, 4 failed, 2 skipped. Полный прогон не считается успешным.

Четыре браузерных падения относились к накопленной компактной карточке:
ожидания 76px/16px/112px вместо 64px/6px/76px, источник внутри выбранного фида,
дата на каждой карточке вместо группы и правый клик по верхней карточке,
перекрытой раскрытым List Actions.

## Исправление и целевой повтор

Production-код не менялся: ожидания приведены к текущим правилам list.tsx/style.css.
Проверка даты дополнена сценарием importance (даты в карточках без групп), источник
выбранного фида проверяется в aria-label. Перед Mark Above закрывается раскрытый
List Actions; force-click не используется. Независимый reviewer проверил четыре
изменённых test-файла: потери проверок и actionable замечаний нет.

`./scripts/check browser --files tests/browser/test_reader_appearance.ts tests/browser/test_reader_http.ts tests/browser/test_youtube_history_http.ts tests/browser/test_news_context_menu.ts`
— 10 passed, tooling-types/browser-types PASS.
Отчёт: .scratch/check-runs/20261019T112733Z-eb6c3ed5/report.json.
Повторный скриншот reader-cards.png просмотрен.
Полный all после исправления только тестовых ожиданий не повторялся.

Предыдущие независимые ревью и проверки folder management / create & move /
early API validation сохранены в соответствующих .scratch/*/verification-документах.
Личные подписки и работающий пользовательский экземпляр не использовались.
