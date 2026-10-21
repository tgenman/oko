# Публикация и обновление рабочего Око

21 октября 2026. Пользователь разрешил публикацию обеих веток, слияние и обновление рабочего экземпляра; затем явно поручил слить и обновить без дальнейшего ожидания CI.

- Реализация опубликована: `codex/telegram-reply`, SHA `789df0af8e96ae060125f26aa2fcfdf22dae4168`.
- Архив прототипа опубликован: `codex/telegram-reply-prototype-20261021`, SHA `0932a002037e87d9daf48655cce212af265f590d`.
- PR: https://github.com/tgenman/oko/pull/71, squash merged.
- Проверенный удалённый `origin/main`: `120fe163f13766aac97a4d4e0ee83ea0262437da`.
- Установленный и запущенный рабочий выпуск: `120fe163f13766aac97a4d4e0ee83ea0262437da`; helper того же SHA, protocol 1.
- Образ: `sha256:c18bb2550e3891ed2c595774cba4cc6c90a118f2ce16bd31e32a36df0a268e0d`.
- Контейнер running/healthy; helper загружен. Штатный start подтвердил HTTP readiness и доступность helper из контейнера с правильными секретом/protocol/release, без inference.
- Прямая HTTP-проверка: `/api/v3/status` 200, runtime_state running; `/` 200 и все четыре frontend assets 200. Токены не выводились.
- Резервная копия: `~/Library/Application Support/Oko/backups/20261021T143636166983Z`, backup.json содержит прежний release `2a61f6e085d7647c59dcb2ee254e3e36fb73a6fd` и digest.

`./scripts/update-working-oko` собрал новый образ, остановил прежний выпуск и сделал backup, но первый запуск не прошёл из-за занятого порта 8765. Первый отдельный start также не прошёл; lsof не показал listener, затем прямые bind-пробы подтвердили свободный порт. Повторный штатный start успешно запустил новый контейнер и выполнил readiness/helper проверки. Причина временного состояния порта не установлена; чужие процессы не завершались.

CI PR #71, run 39617744022: build/static и tools прошли; Python завершился с одним падением `tests/reader/test_news_titles.py::test_full_telegram_heading_in_list_and_opened_material[True-web]` (проверка полного заголовка старой web-копии). Browser ещё выполнялся на момент последней проверки. Это не считается успешным CI; слияние выполнено по прямому указанию пользователя не ждать. Ранее локальный полный прогон реализации прошёл; он не подменяет этот результат CI.

Текущий грязный checkout не переключался и в выпуск не включался. Согласование с main выполнено в отдельном чистом clone: подтверждено равенство исходного дерева squash baseline, затем добавлены существующие изменения #69; файлы самой задачи сохранены без изменений. Временный clone удалён после публикации и сохранения remote refs.

Логи: `.scratch/check-runs/telegram-reply-deploy/` — before/final JSON, update.log, start-retry.log и start-final.log.
