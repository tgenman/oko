# Ускорение тестов runner — 2026-10-15

## Объём

База: свежий `origin/main` `e74e1e744bf12eae608e70265eca38756aaffb23` после слияния PR25. Новый отдельный worktree `/Users/dabondarev/.codex/worktrees/runner-tests-speed/oko`, ветка `codex/faster-runner-tests`.

Production runner, его аргументы, выбор групп, worker limits, API и поведение читателя не изменены. Изменены тесты runner и порядок подготовки одного YouTube-сценария, который упал во время полного замера.

## Что убрано

В `tests/development/test_check_runner.py` 23 тестовые функции (25 параметризованных случаев) вызывают настоящий `main()` внутри процесса вместо нового Python CLI. Подставные Node/Python/Git уже были короткими Python-программами; helper выполняет именно эти программы без нового интерпретатора для каждой команды.

Сохранены настоящий argparse, выбор файлов и профилей, порядок/gating проверок, Report и логика parallel_tests. Каждый вызов загружает runner в новый namespace; argv, env и cwd восстанавливаются. В режиме планирования подставлены execute, descendants, tool_changes и checkout_state. Запрет `subprocess.Popen` делает неожиданный возврат CLI-запусков ошибкой теста. Короткие fork workers самой parallel_tests могут создаваться; внешние CLI/подставные интерпретаторы в этих случаях не запускаются.

Эти случаи проверяют orchestration и обработку синтетических результатов. Они не доказывают работу execute с настоящими процессами — для этого сохранены 16 остальных случаев:

- сквозной успешный CLI и неверный аргумент;
- выбор Python через PATH без `.venv`;
- полный stdout/stderr, коды ошибок, отдельные отчёты повторных запусков;
- выход команды при дочернем процессе, удерживающем stdout;
- SIGINT, остановка descendants и shard workers;
- настоящее перекрытие параллельных групп и разделение логов;
- настоящие Git guards, dirty baseline, owned artifacts, новые файлы и отсутствие отката пользовательских данных;
- выбранные файлы в shards, отсутствие пустых buckets, failure propagation и агрегированный отчёт;
- реальные Git branch/index/worktree/untracked/deleted проверки и shell-выбор tools в CI.

Число сценариев runner осталось **41**. Прежние assertions сохранены. Production-код не получил новых test-only параметров или режимов.

Два throwaway mutation probes подтвердили, что перенесённые тесты отвергают реальные ошибки в runner: игнорирование browser `--files` и игнорирование ненулевого кода checker. Оба изменённых плана были отклонены.

## Замеры

macOS arm64, Python 3.11.15, Node v22.16.0; зависимости по актуальному uv.lock. Одинаковая команда полного прогона: `PYTEST_ADDOPTS='--durations=45' ./scripts/check all tools --report-dir <каталог>`. Tools выбраны явно. В проверочных копиях передан абсолютный `OKO_PYTHON` на то же окружение.

| Набор | До | После | Изменение |
| --- | ---: | ---: | ---: |
| Runner отдельно, 41 passed | 27,82 с | 9,51 с | −65,8%, в 2,93 раза быстрее |
| Tools в успешном full, 111 passed | 82,07 с | 72,10 с | −12,1% |
| Python в успешном full, 367 passed | 84,46 с | 82,77 с | −2,0% |
| Полный прогон | 107,78 с | 104,63 с | −2,9% |

Это одиночные wall-time замеры. Не следует суммировать длительности параллельных групп. После ускорения tools полный прогон ждёт Python: 82,77 с против 72,10 с. Поэтому большой локальный выигрыш runner даёт небольшой выигрыш всего прогона.

### Все проверки успешного full

| Набор | До | После |
| --- | --- | --- |
| `contracts` | PASS (0.30s) | PASS (0.32s) |
| `tooling-types` | PASS (1.27s) | PASS (1.31s) |
| `syntax` | PASS (0.59s) | PASS (0.59s) |
| `lint` | PASS (0.67s) | PASS (0.82s) |
| `types` | PASS (2.90s) | PASS (2.06s) |
| `browser-types` | PASS (1.69s) | PASS (2.12s) |
| `build` | PASS (12.20s) | PASS (11.55s) |
| `http` | PASS (2.52s) | PASS (1.97s) |
| `browser` | PASS (62.26s) | PASS (62.59s) |
| `diff` | PASS (0.10s) | PASS (0.16s) |
| `python-tests` | PASS (84.46s) | PASS (82.77s) |
| `entrypoint` | PASS (0.56s) | PASS (0.50s) |
| `tool-tests` | PASS (82.07s) | PASS (72.10s) |
| **Весь прогон** | **PASS, 107,78 с** | **PASS, 104,63 с** |

Оба успешных full: Python **367 passed**, HTTP **9 passed**, tools **111 passed**, browser **117 passed / 2 прежних skipped**. Новые skip не добавлены.

Baseline выполнен в чистом новом worktree с отдельной uv `.venv`. Итог выполнен из подготовленной копии `/tmp/oko-runner-speed-final-check` по явному списку двух файлов; кэши типов/build в неё не переносились. ОС и установленное окружение не пересоздавались между замерами. Проверенное дерево кода: `c5d2ddeebd3e9dbbc414cef6d957a994cb7ba84a`. После успешного full добавлен только этот отчёт; полный набор повторно не запускался.

## Промежуточное падение и исправление fixture

Первый вариант после ускорения runner: **FAIL, 102,85 с**. Tools PASS71,08 с, browser PASS61,19 с; Python FAIL только в `test_normal_feed_waits_for_mode_and_missing_captions_never_reach_ai`.

Тест подключал источник с включёнными тезисами и затем выключал их. Подключение v3 само запускает сбор с heartbeat: иногда субтитры уже запрашивались, и тест не мог получить ожидаемый `captions=not_requested`. Изменение только в подготовке: `theses=False` устанавливается до subscribe. Прежнее per-feed выключение, включение тезисов, запрет AI для отсутствующих субтитров, ошибки и успешное восстановление сохранены.

В двух временных копиях после subscribe добавлены одинаковые heartbeat и задержка 0,25 с. Старый исходник воспроизвёл падение, новый прошёл (1 expected failure /1 pass). Задержка не добавлена в постоянные тесты. Весь YouTube-модуль: **13 passed, 6,51 с**. Итоговый полный прогон после этой правки PASS104,63 с.

Независимый reviewer проверил основной перенос и отдельно исправление YouTube-fixture: замечаний нет. Точный подготовленный индекс: PASS3,39 с; `git diff --check`: PASS. Основной пользовательский checkout и его окружение не затронуты.

## Артефакты

- Baseline PASS: `/tmp/oko-runner-speed/before/20261015T192440Z-e8e662d9/report.json`.
- Первый вариант FAIL: `/tmp/oko-runner-speed/after/20261015T193046Z-edb4d065/report.json`.
- Итог PASS: `/tmp/oko-runner-speed/final/20261015T193522Z-e7cbc262/report.json`.
- `/tmp/oko-runner-module-before.log`, `/tmp/oko-runner-module-after.log` — сравнение 41 сценария.
- `/tmp/oko-runner-mutations.log` — отклонение двух испорченных планов.
- `/tmp/oko-runner-youtube-race-probe.log`, `/tmp/oko-runner-youtube-fix.log` — воспроизведение fixture и проверка всего модуля.
- `/tmp/oko-runner-staged.log` — подготовленный индекс.
