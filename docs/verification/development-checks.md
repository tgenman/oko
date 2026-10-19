# Проверка инструментов разработки после ретроспективы

2026-10-19. База `bdd289c94f5d0411324eed873e33c712cbb64d16`.
Спецификация: [обновление и проверки](../development/check-workflow.md),
четыре согласованных пункта ретроспективы.

## Выполнено до полного прогона

- Новые CLI-сценарии сначала воспроизвели отсутствие остановки/изоляции/сводки
  (red), затем прошли с реализацией. Логи `/private/tmp/oko-update-red.log`,
  `/private/tmp/oko-guard-red.log`, `/private/tmp/oko-snapshot-red.log`,
  `/private/tmp/oko-report-red.log`.
- Первое совместное выполнение: 67 passed, 1 failed — существующий сценарий
  состава Python-наборов ещё не включал три новых tool-файла. Явный ожидаемый
  список дополнен; состав проверок не ослаблен.
- После добавления защиты нового исходного кода две проверки снимка выявили
  неполный .gitignore тестового checkout (не исключался Python cache). Fixture
  приведён к настройкам проекта, production исключения не расширялись.
- Итоговый целевой набор: `test_check_runner.py`, `test_task_changes.py`,
  `test_update_checkout.py`, `test_check_snapshot.py`, `test_check_report.py`:
  **70 passed, 25.99 с**, лог `/private/tmp/oko-dev-target-final.log`.
- `git diff --check` — успешно.

Временные Git-репозитории и синтетические executables; сетевые Git-серверы,
личные источники и настоящий обработчик не использовались. Команда update
проверялась только против локального origin, не обновляла личный checkout.
## Полный прогон на настоящем снимке

`./scripts/check all --compact`, commit `9e31ac1`, **135.14 с**.
Отчёт `.scratch/check-runs/20261019T075751Z-8efbaec4/report.json`.
Снимок `/private/var/folders/_g/b8j10z1d3db8wtgxyks8jk1m0000gp/T/oko-check-jgszk6g0/checkout`.

Контракты, tooling/browser/Python types, syntax, lint, build, diff, entrypoint,
9 HTTP, 135 browser (2 исторических skipped), 123 tool tests — PASS.
Python: 448 passed, 2 failed — синтетические сценарии
`test_owned_caption_worker_preserves_original_exception_in_debug` и один параметр
`test_future_live_is_discarded_but_other_unplayable_errors_remain`.
В момент падения статус показывал отсутствие действующей клиентской сессии;
полная причина нестабильности отдельно не устанавливалась. Общий результат — FAIL.
Checkout guard — PASS, проверяемые файлы не изменялись.

Ограниченный повтор этих двух функций в том же собранном снимке, без изменения
кода: **3 passed, 2.32 с**. Лог `/private/tmp/oko-dev-youtube-snapshot-repeat.log`.
Предыдущий прямой повтор в исходном checkout отклонил отсутствие assets: по новой
схеме сборка намеренно существует только в снимке. Этот запуск не считается
проверкой сценариев. Production-код и YouTube-тесты не менялись.

## Ревью и исправления

Независимый reviewer проверил diff от `bdd289c` до `9e31ac1` и отметил:

1. Отдельный subprocess launcher мог мешать очистке runner при отмене.
   Реальный CLI-сценарий воспроизвёл timeout при SIGTERM. Исправлено заменой
   launcher через execve; проверяется report130 и отсутствие живых PID тестов.
2. JSON-режим принимал неверные типы обязательных полей отчёта. Сценарий
   воспроизвёл code0 для null duration/status/log. Общая проверка структуры
   вынесена до вывода и применяется одинаково к текстовому и JSON-режиму.

Также исправлен обнаруженный в полном отчёте разбор pytest-параметров с пробелами:
сводка сохраняет полное имя сценария. Существующий тест сначала воспроизвёл
обрезание, затем прошёл после исправления.

После этих изменений:
- весь затронутый набор — **72 passed, 27.15 с**;
- повторное узкое ревью — новых существенных замечаний нет;
- compact report CLI прочитал настоящий полный отчёт и сохранил exit_code1,
  duration135.14 и отдельный checkout PASS;
- `git diff --check` — успешно.

Полный набор после последних узких исправлений не повторялся. Его FAIL и
ограниченные успешные повторы приведены отдельно; общий полный PASS не заявляется.
Финальный лог `/private/tmp/oko-dev-final-target.log`, результаты review-fixes —
`/private/tmp/oko-review-fixes.log`. Персональный экземпляр Око не перезапускался.
