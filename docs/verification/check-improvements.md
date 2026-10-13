# Улучшения проверок после списка материалов

2026-10-13. Ветка `codex/check-guardrails`. База: `7ddd6c54801115dc45343b8bb0b36edde9432300`.

## Выполнено

1. Ранее подготовленные runner, hooks, CI и инструкции сохранены отдельным инфраструктурным коммитом `8af39fe`. Продуктовые изменения списка в него не входят. Hooks активны локально (`core.hooksPath=.githooks`); новый checkout включает их через `scripts/install-hooks`.
2. Добавлены закреплённые ESLint 10.12.0 и globals 17.13.0. `check lint`, `check ui` и `check all` проверяют `no-undef`. CI и pre-push используют `check all`. Pre-commit проверяет `no-undef` на точных staged bytes через stdin, включая частичный staging. Браузерные и Node-глобальные имена задаются отдельно; имена тестового транспорта разрешены только в браузерных тестах. Правило: [официальная документация](https://eslint.org/docs/latest/rules/no-undef).
3. `test_reader_http.cjs` запускает production-приложение uvicorn с временным хранилищем и настоящим браузером. `/api/cards`, `/api/navigation`, картинки и действия не подменяются в браузере. На сервере заменены только внешние RSS и модель. Проверяются папка, два участвующих фида и пустой фид, краткий текст, авторизованные локальные картинки, группа со всеми источниками, различие счётчиков карточек/публикаций и удаление просмотренного. Процесс, браузер и данные очищаются при завершении/отмене.
4. `check --files` выбирает существующие файлы соответствующих наборов внутри checkout. Поддерживает несколько файлов, устранение повторов и Python node ID. Ошибка пути или неподходящий набор отклоняется до запуска. Неограниченные выбранные наборы сохраняют полный объём. Hooks/CI остаются полными и не используют `--files`.

## Примеры

```sh
./scripts/check lint
./scripts/check browser --files tests/browser/test_material_list.cjs tests/browser/test_reader_http.cjs
./scripts/check python --files tests/development/test_check_runner.py tests/development/test_hooks.py
./scripts/check python --files tests/reader/test_web_assets.py::test_reader_serves_entire_browser_module_graph_without_exposing_reader_data
```

Зависимости устанавливаются через `npm ci`, поддерживаемые версии Node определяет `package.json`. Подробности: [инструкция](../../tests/browser/README.md).

## Фактическая проверка

- Перед сохранением прежней инфраструктуры: 14 development-тестов passed.
- `check all`: все наборы passed, exit 0, 212.86 s. Синтаксис 0.60 s, линтер 0.38 s, HTTP 1 passed, браузер 59/59 (82.62 s), Python 291 passed (127.57 s), mypy 36 файлов, diff и CLI passed.
- После исправления передачи Python для CI: `check python --files` с обоими development-файлами — 20/20 passed, exit 0, 10.93 s. Включает новую проверку checkout без `.venv`, отсутствие Node для Python-выбора, ошибочные пути и частичный staging с неопределённым `source`.
- После исправления Python и очистки процесса: `check lint http browser --files tests/browser/test_reader_http.cjs` — все наборы passed, exit 0, 4.51 s; HTTP 1 passed, настоящий браузерный сценарий 1/1.
- Выбор браузерного файла и отсутствие передачи Python сначала дали ожидаемые красные проверки, затем прошли после реализации. Две промежуточные ошибки самих тестов (канал stderr Git hook и повторная подготовка одного runtime) исправлены и включены в успешный целевой прогон.
- Изменений production-кода Око нет. После полного прогона повторены затронутые runner/hooks и настоящий HTTP-браузерный сценарий. Последующее уточнение разделения браузерных/Node глобальных имён проверено полным линтером.

## Ревью

Независимое ревью выявило P1: CI использует Python из setup-python без `.venv`, а fixture искал `.venv/bin/python`. Runner теперь передаёт выбранный Python через `OKO_PYTHON`; прямой запуск поддерживает `python3`. Регрессия воспроизводит checkout без `.venv`. Повторное ревью: 0 открытых замечаний Standards/Spec.

## Ограничения

GitHub Actions подготовлен и сохранён в Git, но удалённый запуск CI не выполнялся: remote не настроен. Настройка серверного required check зависит от подключения remote. Личные источники, аккаунты Telegram и настоящие модели не использовались. Пользовательский экземпляр Око не перезапускался; инфраструктурные изменения этого не требуют.
