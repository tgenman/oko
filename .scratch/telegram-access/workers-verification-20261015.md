# Telegram #19: workers, lanes и прочитанное групп

Дата: 2026-10-15. База: `3cf1ca7df82dc445cf45a36cb01bdc388aa53015`.
Задача: https://github.com/tgenman/oko/issues/19.

## Результат

- Один ленивый worker на проверенный user ID, отдельные pipe/lock/generation/spool.
  Проверка ещё не привязанной сессии использует временный worker; конфигурация
  по-прежнему принадлежит ClientRegistry. Ошибка/отмена уничтожает только
  соответствующий worker. Общая остановка закрывает все процессы.
- Runtime выполняет common/personal/collection lanes и отдельное AI-задание.
  Store выдаёт задания последовательно и атомарно; фиксации используют action lock.
  Просмотр/удаление отменяют затронутые фиды. FloodWait хранится по user ID как
  wall-clock deadline; после перезапуска восстанавливается оставшийся интервал
  и время готовности затронутых заданий с новым monotonic origin.
- Группа собирается целиком, без reply_to-фильтра. Выбор тем удалён из формы,
  legacy route/DTO/SDK и collection operations; сведения о теме остаются внутри
  протокола прочитанного. Существующий topic preflight сохраняет старую базу.
- ReadSync сначала проверяет весь поток, учитывает серверные read_inbox_max_id,
  затем подтверждает допустимые границы тем. Частичный успех хранится отдельно
  и не повторяется после перезапуска. Удалённая тема даёт видимую ошибку;
  расширяющий global ack форума запрещён. Web/members не дают права на ack.
- Удаление публичного null-поля темы сохраняет историческую форму хэшей
  принятых фильтров и миграции тезисов; результаты обработки не теряются.

## Проверка

HTTP использует временные SQLite/файлы и синтетические adapters. Subprocess
использует установленные Telethon types/RPC и подменяет только сетевой клиент.

- RED: HTTP-второй аккаунт блокировался скачиванием первого (2.79 s).
  После lanes — PASS. Worker-сценарий доказывает независимый download второго,
  отмену первого и отсутствие оставшихся media.bin.
- Целевой `check python types --files ...`: 64 passed, contracts/types PASS,
  11.21 s. Проверены bindings, read sync, workers и runtime actions.
- После последних дополнений расширенный `./scripts/check python types`:
  **391 passed, 4 failed**, contracts/types PASS, 46.12 s.
  Четыре падения воспроизвели потерю исторических signatures после удаления
  публичного поля. Исправлены; `pytest -q tests/reader/test_remove_aggregates.py
  tests/reader/test_aggregate_upgrade_youtube.py`: **5 passed, 4.20 s**.
  Последующий `./scripts/check types`: PASS, 1.05 s. Широкий набор после этих
  двух локальных исправлений повторно не запускался.
- До обновления frontend-сборки после codegen один серверный прогон отказал
  из-за stale build. После `npm run build` эти отказы исчезли.
- `./scripts/check browser --files tests/browser/test_telegram_connection.ts`:
  **6 passed**, 8.56 s вместе с tooling/browser types. Первый запуск с записью
  screenshots прошёл сценарии, но runner отклонил изменение checkout; повтор
  без записи артефактов PASS. Desktop/mobile screenshots сняты и просмотрены:
  [desktop](evidence/task19/desktop.png), [mobile](evidence/task19/desktop-mobile.png).
- `npm run api:generate`, последующая штатная сборка и drift/type checks: PASS.
  Canonical v3 не менялся; legacy libraries сгенерированы из фильтра схемы.
- `git diff --check`: PASS. Новых skipped-тестов нет; полный `all tools`
  запланирован после оставшихся задач, а не после каждого коммита.

## Независимое ревью

Spec: 0 незакрытых замечаний. Исправлены серверное прочитанное и остатки выбора тем.
Standards: 0 незакрытых замечаний. Исправлена персистентность FloodWait.
Обе оси повторно проверили совместимость хэшей старых результатов.
Независимые запуски reviewer: 9 HTTP/worker tests PASS; FloodWait restart PASS.

## Пределы доказательств

Личные аккаунты, подписки и модели не использовались. Эффект ack на настоящем
сервере Telegram не проверялся. Протокол сопоставлен с
[Telegram forum](https://core.telegram.org/api/forum),
[readDiscussion](https://core.telegram.org/method/messages.readDiscussion) и
[официальным Telegram Desktop RepliesList](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/data/data_replies_list.cpp)
(`sendReadTillRequest` передаёт root ID, включая General).
Автоматическая проверка доказывает запросы и локальные гарантии на синтетической сети.
Сбор веба/Telegraph/manifest принадлежит #20; новая форма профилей — #5/#21.
