# Последовательное завершение шести задач

Worktree: `telegram-remaining/oko`, ветка `codex/telegram-remaining`.
База: main `3cf1ca7`. Пользователь поручил выполнить все шесть задач последовательно,
отдельным коммитом на задачу, ПР создать только в конце. Личные аккаунты не использовать.

1. #19 workers/lanes/read sync — реализовано и проверено; отчёт
   [workers-verification-20261015.md](workers-verification-20261015.md).
2. #20 web/history/Telegraph/media — следующая задача; читать актуальное issue и comments.
3. #5 Telegram API/UI — после предметных модулей.
4. #21 итоговая Telegram-приёмка — после #5.
5. #6 состояние работы/восстановление — после Telegram.
6. #7 финальная интеграция/переход — последняя, затем общий PR.

Каждый следующий шаг начинает с текущего git status и этого checkpoint.
Полный `./scripts/check all tools` — на итоговой реализации. Не возвращать hooks,
Poetry, approval baseline или старые flake-тесты. Ускорения main/uv сохранены.

#19: последний расширенный Python-прогон391pass4fail; четыре миграционных падения
исправлены восстановлением null-поля только в хэше; повтор5affectedtestsPASS.
TypesPASS, browser6PASS; подробности и ограничения в отчёте. Новых skips нет.
