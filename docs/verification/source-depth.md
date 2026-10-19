# Углубление Source — фактическая проверка

Дата: 2026-10-19. Задача: https://github.com/tgenman/oko/issues/41.
PR: https://github.com/tgenman/oko/pull/42.
Спецификация: [.scratch/source-depth/spec.md](../../.scratch/source-depth/spec.md).

## Реализация и владение

Исходная база ревью: `bdd289c94f5d0411324eed873e33c712cbb64d16`.
Реализация: `a38992f4020c7dab26c408a7ad369d55869cf15f`.
Интеграция main `95f74aad22fc17382c8ee3aa9b631752e717f37f`:
`2958ca92d16637d69cb1bba62e752c3650e1419b`.

RSS, Telegram web/client, YouTube, Demo и синтетические Source используют полные
результаты discover/fetch. Вложенные publication/material/error данные отделены
от последующих вызовов; identity фиксирует usernames и присутствие avatar.
`collection/sources.py` настраивает adapters, сохраняет кеш при неизменной
конфигурации и не меняет настройки выполняющегося экземпляра.

Runtime сохраняет подготовку ресурсов под action lock, внешнее ожидание вне lock
и повторный допуск перед фиксацией. CaptionAttempt, ReadSync, аккаунтные ожидания
и workers остаются у прежних владельцев. Store, форматы курсоров, SQLite schema,
HTTP-контракт и UI не менялись. Строгая проверка привязки применяется также к
внедрённому TelegramSource; setup синтетического Source без URL сохраняется.

## Выполненные проверки

1. Первичный прямой baseline в новом worktree: 26 failed / 8 passed за 4.53s.
   Причина — отсутствующая сборка frontend; выполнение HTTP-сценариев не началось.
   После `npm run build` тот же выбранный набор: **34 passed, 13.08s**.
   Логи: `/private/tmp/oko41-build.log`, `/private/tmp/oko41-baseline.log`.
2. TDD на Source seam: `test_discovery_returns_detached_metadata_and_continuation`
   сначала упал на старом tuple без publications; после перехода прошёл.
   Проверка partial history сначала выявила изменение label прежней ошибки
   следующим вызовом; после отделения вложенных данных прошла.
   Логи: `/private/tmp/oko41-red.log`, `/private/tmp/oko41-red-partial.log`,
   `/private/tmp/oko41-green-partial.log`.
3. Первый целевой повтор: **32 passed / 3 failed, 29.34s**.
   Новый setup ошибочно требовал original_urls[0] у явно внедрённого Source.
   Восстановлен прежний обход настройки такого Source.
   Повтор: **35 passed, 15.22s**, включая runtime actions, Telegram lanes,
   source revisions, YouTube и Source outcome.
   Логи: `/private/tmp/oko41-target.log`, `/private/tmp/oko41-target-2.log`.
4. `./scripts/check python types`: **PASS**, contracts/types и 461 Python tests;
   всего 46.58s, Python 44.92s, типизация 69 файлов.
   Отчёт: `.scratch/check-runs/20261019T085504Z-8a51eeb2/report.json`.
   После этого ресурсы fetch перенесены под lock, добавлена проверка кеша и
   сохранена строгая проверка access; их итоговая приёмка — следующие прогоны.
5. Source interface: **3 passed, 0.45s**. Проверены отдельные identity snapshots
   для discover/fetch, missing/null avatar, partial history + continuation .1
   + FloodWait, отделённые настройки истории и кеш названия YouTube.
   Лог: `/private/tmp/oko41-results.log`.
6. `./scripts/check all` на реализации до интеграции main: **FAIL, 148.11s**.
   Contracts, syntax/lint, все types, build, HTTP **9 passed**, browser
   **135 passed / 2 исторически skipped**, tools **111 passed**, diff и entrypoint
   прошли. Python: **452 passed / 1 failed**. Единственный сбой:
   `test_subprocess_downloads_have_separate_account_pipes_and_spools` не увидел
   `download-start` за 100 ожиданий по .01s. Worker-код этот рефакторинг не меняет;
   причина опоздания в этом прогоне не установлена. Таймауты и тест не менялись.
   Отдельный повтор **1 passed, 1.62s**. Повтор доказывает только этот сценарий.
   Отчёт: `.scratch/check-runs/20261019T085711Z-77e16693/report.json`.
   Логи: `/private/tmp/oko41-all.log`, `/private/tmp/oko41-worker-repeat.log`.
7. После интеграции main: `./scripts/check all --compact`, **FAIL, 154.1s**.
   Проверялся отдельный snapshot tree `4097567a7f622e42cf64f63dc96343543aad49cd`.
   Contracts/types/syntax/lint/build/HTTP/entrypoint/diff прошли; browser
   **135 passed / 2 исторически skipped**, tools **125 passed**;
   checkout неизменён. Python: **452 passed / 1 failed** —
   `test_late_captions_cannot_publish_after_reader_action[viewed]` не увидел
   начало попытки. Тест ожидал событие при lease .1s без возобновления сессии;
   истечение реальных часов могло прервать работу до выбранного действия.
   Причина конкретного опоздания не доказана; этот посторонний фактор исключён
   контролируемыми monotonic и wall clocks. Действия viewed/stop/close и проверки
   отсутствия позднего текста/медиа сохранены, таймауты не повышены.
   Отчёт: `.scratch/check-runs/20261019T090007Z-bfcfede2/report.json`.
8. Первый CI `39226601546` на `a38992f`: static/browser/tools прошли, Python
   упал в `test_youtube_description_survives_restart_without_requesting_captions`.
   Старое описание проверялось после разрешения фонового сбора нового; CI получил
   новое описание. HTTP fixture теперь удерживает ответ источника до проверки
   сохранённого содержимого, затем разрешает обновление. Продуктовый код не менялся.
9. После двух тестовых исправлений: **8 passed, 4.21s**, включая прежний
   worker-сценарий. Независимый reviewer обнаружил, что нужно контролировать и
   wall clock; добавлен второй clock. Финальный повтор затронутых двух файлов:
   **7 passed, 3.22s**. Логи `/private/tmp/oko41-races.log` и
   `/private/tmp/oko41-races-final.log`. Коммиты `2ef344c` и `eb85adf`.
   Неизменившиеся прошедшие наборы локально не повторялись после test-only правок.
   Новый полный CI на опубликованной итоговой ветке проверяется отдельно.

Проверки использовали временные SQLite/файлы и синтетические внешние adapters.
Браузерные действия выполнены Playwright; просмотрен screenshot
`.scratch/check-runs/20261019T085711Z-77e16693/artifacts/browser/74934/feed-refresh.png`.
Это проверка синтетического RSS, не настоящих подписок.

## Независимое ревью

Две отдельные оси `code-review`, diff от исходной базы до `a38992f`:

- Standards: 0 замечаний к документированным правилам, 0 actionable smell findings.
- Spec: 0 отсутствующих требований, ошибок реализации или расширений объёма.

Reviewers проверяли код и документы, но не запускали собственные наборы.
Интеграция main добавила только новую инфраструктуру проверок; task implementation
не менялась. Повторное ревью сосредоточено на двух тестовых исправлениях:
Spec — без замечаний; Standards нашёл оставшуюся зависимость от wall clock,
исправленную в `eb85adf`. Полный прогон интегрированной ветки записан выше;
финальный CI не подменяется ограниченным локальным повтором.

## Ограничения

Личные аккаунты, подписки и настоящий обработчик не запускались. Приложение
пользователя не перезапускалось. Проверки не являются новой приёмкой Telethon,
Telegram web или YouTube на настоящей внешней сети. Два исторически пропущенных
браузерных сценария не включались этой задачей.
