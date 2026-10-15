# Переход на Poetry: фактическая проверка

2026-10-15. Спецификация: [план](../../.scratch/poetry-migration/spec.md). База реализации и первого ревью — `2755194342be1ed87788db0534156314897608c4`, реализация — `f5e37d6`. Интегрирован завершённый перенос браузерных тестов `8617c28`; проверяемое дерево — `8a72490`. Diff Poetry после интеграции: `git diff 8617c28...8a72490`.

## Зависимости и установка

- Poetry 2.5.1 установлен в отдельное временное окружение; приложение установлено в новую проектную `.venv`, исходная `.venv` пользователя сохранена.
- Пользователь отдельно согласовал Python `>=3.11,<3.15`: опубликованный `youtube-transcript-api==1.2.4` требует Python `<3.15`.
- `poetry check --lock --strict` — PASS. Чистый `poetry sync --no-interaction` на macOS установил 67 пакетов; повтор сообщил отсутствие установок и обновлений.
- Сохранены все 67 общих package/version пар. Из прямого build-инструментария добавлен poetry-core 2.5.0, удалены setuptools 80.9.0 и wheel 0.45.1. Сравнение: [машинный отчёт](../../.scratch/poetry-migration/dependency-comparison.json), исходные данные — рядом в `dependency-baseline.json`.
- Прямые runtime-pins сохранены. Четыре изначально предложенных resolver обновления (ast-serialize, more-itertools, pydantic-settings, starlette) отклонены; окончательный lock сохраняет прежние версии. SHA-256: `47e514d718c3318f691ff2f1a0b65d6b32f973138e2f87538132625a63c9f85b`.
- Команда `poetry check --lock --strict` в отдельных временных каталогах корректно отклонила отсутствующий lock и lock с изменённым direct pin в pyproject. Проверки не пересоздавали lock.

## TDD и целевые проверки

- Тест настоящего wheel сначала упал на прежнем `Provides-Extra: dev`. После перехода на dev-группу metadata не содержит extra/dev-инструменты, а runtime requirements сохраняются (9 зависимостей).
- Staged API governance сначала упал на прежнем чтении `optional-dependencies.dev`. После адаптации к группе и `poetry.lock` сценарий exact index прошёл, включая противоположные unstaged-байты.
- `poetry run python -m pytest -q tests/api/test_installed_package.py tests/api/test_governance.py`: **6 passed**, 95.52 s.
- Штатный pre-commit `check staged`: PASS до и после интеграции TypeScript runner, включая точную типизацию индекса и обе API-библиотеки.
- Архив wheel содержит только пакет `oko` и dist-info; внутри одна текущая frontend generation, Swagger и canonical OpenAPI. Установленный пакет проверен вне checkout с пустым PATH через HTTP и Playwright.

## macOS

macOS 27.0.1 arm64, Python 3.11.15, Node 22.16.0. `poetry run ./scripts/check build browser --files tests/browser/test_installed_docs.ts`: **PASS**, browser-types/build/browser, 38.55 s; 1 браузерный сценарий, 0 failures/skips. [Отчёт](poetry-migration/macos-installed.json).

До интеграции `.ts` тот же installed browser-сценарий прошёл в CJS-версии (14.38 s). После интеграции выполнена новая сборка. Скриншоты [читателя](poetry-migration/installed-reader.png) и [Swagger](poetry-migration/installed-swagger-authorized.png) просмотрены: интерфейс и локальная документация отображаются, Execute возвращает авторизованный ответ.

`osacompile -o /tmp/oko-poetry-launcher-check.app scripts/launch.applescript` — PASS. Launcher сохраняет путь `.venv/bin/python`. Сам launcher не запускался: в нём закреплены личное хранилище и Telegram-env. Настоящий запуск установленного приложения проверен синтетической HTTP/browser-фабрикой с временным хранилищем.

## Ubuntu

Ubuntu 24.04 arm64, Python 3.11.15, Node 22.21.1. Созданы отдельные чистые checkout и `.venv`; установка выполнялась по lock, npm — через `npm ci`, Chromium — через Playwright. Изменений прямых зависимостей ради Linux не потребовалось.

Первый прогон `poetry run ./scripts/check all` на снимке до TS-интеграции: HTTP **11 passed**; браузер **123 passed, 2 failed**, затем Python-набор прерван при диагностике окружения (entrypoint не запускался). Минимальный Ubuntu-образ не содержал системной базы часовых поясов: подтверждён `ZoneInfoNotFoundError` для Europe/Moscow, что объясняет отказ истории; отдельно наблюдался таймаут загрузки каталога Codex. Эти результаты не считаются успешным полным прогоном. [Промежуточный отчёт](poetry-migration/ubuntu-incomplete.json).

После установки системного tzdata полный `poetry run ./scripts/check all` на `8a72490` прошёл: **438 Python-тестов, 125 браузерных сценариев, 11 HTTP-проверок**, все остальные наборы PASS, 526.73 s. [Полный отчёт](poetry-migration/ubuntu-all.json). Таймаут каталога Codex не воспроизвёлся; его отдельная первопричина не установлена.

После интеграции завершённого typed-tooling `e8affc8` получено дерево `f219d26`. На Ubuntu повторены затронутые tooling-types/browser-types/build/installed-browser: **PASS**, 33.51 s. [Отчёт](poetry-migration/ubuntu-final.json).

На macOS финальный `poetry run ./scripts/check build http browser python --files tests/api/test_installed_package.py tests/api/test_governance.py tests/development/test_check_runner.py tests/development/test_hooks.py tests/browser/test_installed_docs.ts`: **46 Python-тестов, 11 HTTP-проверок, 1 browser**, все наборы PASS, 230.62 s. [Отчёт](poetry-migration/macos-final.json). Незатронутые продуктовые сценарии полного Ubuntu-набора не повторялись.

Предшествующий целевой macOS-запуск ошибочно поставил Python-набор перед новой сборкой: первые два сценария упали при неактуальных assets, запуск прерван; итог Python-набора не получен. Исправлен порядок вызова runner, затем весь выбранный набор выполнен успешно. [Промежуточный отчёт](poetry-migration/macos-interrupted.json).

## Standards

Независимое ревью: 0 замечаний к документированным стандартам, 0 существенных baseline smells. После интеграции повторно проверено разрешение конфликта инструкции поставки — замечаний нет.

## Spec

Независимое ревью: 0 замечаний к реализации спецификации. Reviewer отдельно подтвердил сохранение прежних package/version пар. После интеграции повторно проверено сочетание Poetry-команд с TypeScript runner — замечаний нет.

## Границы доказательства

Данные и источники синтетические; личные подписки, Telegram и настоящий AI-процессор не использовались. GitHub Actions на удалённом сервере не запускались; Linux-проверка выполняется локальным контейнером Ubuntu. Обычная пользовательская установка wheel не получает гарантию разрешения транзитивных зависимостей по Poetry lock. Другие версии Python и Windows не проверялись.

Повторное независимое Standards/Spec ревью интеграции `e8affc8...f219d26`: 0 замечаний по обоим направлениям.
