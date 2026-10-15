# Миграция Python-окружения на uv — 2026-10-15

## Изменение

Отдельный worktree `uv-migration`, ветка `codex/uv-migration`, база свежего `origin/main` — `c401abc`. Код миграции: `7e60828`.

- Poetry CLI, `poetry.lock`, `poetry.toml`, `poetry-core` и build backend исключены из проекта. Исторический отчёт `wheel-removal.md` сохранён без переписывания прежних результатов.
- Зависимости фиксирует `uv.lock`; uv 0.11.28 закреплён в конфигурации, CI и инструкции. Око — non-package project (`package = false`), запуск из checkout через `.venv` сохранён.
- CI устанавливает uv через закреплённый SHA официального setup-uv, кэширует пакеты и выполняет один `uv sync --locked` на job. Группы runner используют готовую `.venv` напрямую.
- `uv.lock` включён в API fingerprint, материализацию точного Git index, staged cache selection и выбор tools. Существующие регрессии дополнены проверкой изменения lock, включая staged-only изменение; сценарии не удалены.

## Сохранение зависимостей

Для первого разрешения временно применены constraints всех версий старого lock, кроме poetry-core. Затем constraints удалены и `uv lock` повторён с предпочтением уже выбранных версий. Постоянных транзитивных constraints в pyproject нет.

Сравнение lock по нормализованным именам и версиям: **66 библиотек без изменений**, удалён только `poetry-core==2.5.0`. `oko==0.1.0` записан в uv.lock как virtual project. Независимый reviewer подтвердил это сравнение.

В реально установленном macOS/Python 3.11 окружении версии общих библиотек также совпали. В окружении uv отсутствуют служебный pip и установленный пакет самого Око; импорт и CLI работают из checkout. Это соответствует отказу от упаковки приложения.

## Установка и синхронизация

macOS arm64, Python 3.11.15 из Homebrew, Poetry 2.5.1, uv 0.11.28. Оба CLI уже установлены вне проектной `.venv`; время установки самих CLI не включено. Для каждого менеджера использован отдельный изначально пустой cache directory. Подготовка uv.lock выполнена с третьим кэшем, не участвующим в установочных замерах.

Для Poetry измерялся `poetry env use /opt/homebrew/bin/python3.11` плюс `poetry sync --no-interaction`; для uv — `uv sync --locked --python /opt/homebrew/bin/python3.11`. Отдельная команда `poetry check --lock --strict` в установочные замеры не включена.

| Условия | Poetry | uv | Изменение |
| --- | ---: | ---: | ---: |
| Новая `.venv`, пустой кэш пакетов | 19,748 с | 15,516 с | −21,4% |
| Новая `.venv`, прогретый кэш пакетов | 2,632 с | 0,104 с | −96,0%, в 25,3 раза быстрее |
| Повторная синхронизация готовой `.venv` | 1,025 с | 0,066 с | −93,6%, в 15,5 раза быстрее |

Каждая ячейка — один замер wall time. Холодная установка зависит от сети; эти цифры не являются прогнозом времени GitHub-hosted Linux CI. uv по-прежнему может собирать внешние зависимости без подходящего бинарного артефакта (в этом запуске — pyaes); сборка самого Око отключена.

## Полный прогон

Одинаковая команда до и после: `./scripts/check all tools --report-dir <отдельный каталог>`. Tools выбран явно в обоих запусках, чтобы состав не зависел от Git diff относительно main. Оба запуска — в чистом checkout: база `c401abc` и коммит миграции `7e60828`.

Одинаковые Node v22.16.0, npm-зависимости, Python 3.11.15 и worker limits (Python2/tools2/browser4). Перед каждым полным замером отсутствовали `.scratch/check-cache` и `oko/frontend_build`: типы проверены заново, API регенерирован и сравнен, frontend собран заново. Низкоуровневые кэши ОС и исходных Python-модулей между запусками не сбрасывались.

| Набор | До | После |
| --- | --- | --- |
| `contracts` | PASS (0.31s) | PASS (0.30s) |
| `tooling-types` | PASS (1.29s) | PASS (1.20s) |
| `syntax` | PASS (0.59s) | PASS (0.55s) |
| `lint` | PASS (0.68s) | PASS (0.53s) |
| `types` | PASS (3.41s) | PASS (3.21s) |
| `browser-types` | PASS (1.72s) | PASS (1.70s) |
| `build` | PASS (13.88s) | PASS (9.94s) |
| `http` | PASS (3.08s) | PASS (2.25s) |
| `browser` | PASS (65.96s) | PASS (60.45s) |
| `diff` | PASS (0.06s) | PASS (0.06s) |
| `python-tests` | PASS (89.23s) | PASS (84.75s) |
| `entrypoint` | PASS (0.47s) | PASS (0.49s) |
| `tool-tests` | PASS (85.31s) | PASS (82.50s) |
| **Весь прогон** | **PASS, 115.16 с** | **PASS, 105.48 с** |

Оба запуска: Python 365 passed, HTTP 9 passed, tools 111 passed, browser 117 passed / 2 ранее отключённых skipped. Ошибок нет.

Разница полного прогона — **−9,68 с (−8,4%)**. Это два одиночных наблюдения: uv не меняет выполнение pytest или браузера, поэтому ускорение отдельных групп нельзя приписать менеджеру окружения. Основной непосредственно измеренный выигрыш миграции — установка с прогретым кэшем и повторная синхронизация.

## Дополнительные проверки

- 17 целевых тестов кэша, выбора tools, API governance и staged hooks: PASS, 33,85 с.
- Точный staged-индекс, включая генерацию обеих API-библиотек в новом каталоге: PASS, 11,49 с. Исправления рабочей копии не подменяют индекс.
- `uv lock --check --offline`: PASS.
- В отдельной временной копии добавлен уже залоченный packaging в прямые dev-зависимости: `uv sync --locked --offline` завершился ошибкой `The lockfile at uv.lock needs to be updated, but --locked was provided`. Lock сохранился побайтно.
- `uv run --locked python -m oko --help`: PASS.
- YAML CI разбирается; в setup ровно одна locked-синхронизация. Независимое ревью: блокирующих замечаний нет.
- `git diff --check`: PASS. Пользовательское окружение основного checkout, личные источники и аккаунты не затронуты.

После итогового полного прогона добавлен только этот отчёт. GitHub Actions на Linux на момент локальной проверки ещё не выполнялся.

## Локальные артефакты

Каталог: `/tmp/oko-uv-migration-evidence/`.

- `poetry-times.json`, `uv-times.json` и соответствующие `*-cold.log`, `*-warm.log`, `*-noop.log`.
- `lock-comparison.json`, `poetry-locked.json`, `poetry-installed.json`, `uv-installed.json`.
- До: `/tmp/oko-uv-migration-evidence/before/20261015T175025Z-81a8456f/report.json`.
- После: `/tmp/oko-uv-migration-evidence/after/20261015T175523Z-85b02bf0/report.json`.
- `targeted.log`, `staged.log`, `lock-refusal.log`, `uv-launch.log`.

Официальные инструкции: [CI](https://docs.astral.sh/uv/guides/integration/github/), [locked-синхронизация](https://docs.astral.sh/uv/concepts/projects/sync/), [non-package projects](https://docs.astral.sh/uv/concepts/projects/config/).
