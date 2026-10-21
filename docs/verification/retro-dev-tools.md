# Инструменты разработки после ретроспективы

Проверено 2026-10-21. Реализованы пункты 1, 3 и 4 ретроспективы: ожидание освобождения порта при обновлении, ранний browser-types в CI, общие ссылки на установленные зависимости проверочных checkout.

База: `37e4d9c8d950a62bc7c16ddd62671bc3e9db3b75`. Подготовленное дерево: `e7b99239a8827b0d2bd988be04729f541ea1c53d`, проверочная копия `/private/tmp/oko-retro-134-check`. Прежние пользовательские изменения исключены через `task_changes.py snapshot/prepare/checkout`.

## Фактические проверки

- Целевые `test_docker_release.py`, `test_task_changes.py`, `test_check_snapshot.py`: **42 passed**. Первый запуск в sandbox: 37 passed, 5 failed из-за запретов `bind` и `ps`; повтор с нужными правами прошёл.
- `./scripts/check tools browser-types syntax lint diff entrypoint --compact`: contracts, tooling-types, syntax, lint, browser-types, diff, entrypoint — **PASS**. Tools: **132 passed, 4 failed** из-за отсутствующей frontend-сборки в новой копии.
- `./scripts/check build --compact`: tooling-types и build — **PASS**.
- `./scripts/check tools --files tests/development/test_browser_host.py --compact` после сборки: **4 passed**. Полный tools повторно не запускался.
- Независимое ревью ограниченного diff: **PASS**, замечаний нет.

Отчёты runner и сжатые логи: [артефакты](../../.scratch/retro-dev-tools/verification/).

Проверены кратковременная занятость порта и отказ при постоянном listener без его остановки, порядок stop → configure → start с ожиданием, ссылки на зависимости в обоих видах копии, отсутствие переноса данных и сохранение существующего сломанного symlink.

Рабочий экземпляр не перезапускался. Настоящий Docker update и GitHub Actions в рамках этой задачи не запускались; проверены синтетические release-сценарии и локальные наборы. Причина прежней кратковременной занятости порта не установлена.
