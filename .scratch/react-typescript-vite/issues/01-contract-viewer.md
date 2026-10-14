# 01: Полный контракт API и самостоятельный Swagger

**What to build:** Разработчик читает полный действующий контракт Око в локальном Swagger, даже когда читатель остановлен.

Blocked by: None (can start immediately).

Status: done
Assignee: contract

Parent: [Спецификация](../spec.md). Подготовка тикета не разрешает реализацию.

- [x] Описаны все существующие операции читателя, их параметры, запросы, ответы, ошибки, security и media; inventory реального HTTP сопоставлен со стабильными operation IDs.
- [x] OpenAPI становится редактируемым источником контракта; сохранены optional/null, PATCH, фактические content types и статусы, примеры синтетические.
- [x] Локальный Swagger показывает bundled-спецификацию без Runtime, источников и моделей; внешних assets/validator и автоматических API-вызовов нет.
- [x] Валидация спеки, полнота маршрутов и выбранные настоящие HTTP-сценарии подтверждают начальный baseline; браузерный тест проверяет видимые операции.
- [x] Контракт полон до переноса React-потребителей; текущий читатель продолжает работать с прежним поведением.

- [x] Для затронутого сценария сохранены существующие API/security, оформление и поведение; реальные HTTP/browser-проверки выполнены на временных данных и синтетических adapters. Фактический результат записан отдельно.

## Comments

2026-10-14: реализован редактируемый OpenAPI 3.1 baseline всех 50 операций, CLI validate/bundle и самостоятельный Swagger с локальными assets. Контракт не обновляется из FastAPI при сборке. Начальная extraction использовалась однократно, вспомогательные extraction scripts в репозиторий не добавлены.

Проверено отдельно от плана: `pytest tests/api/test_contract.py -q` — 4 passed (реальный HTTP: snapshots, ошибки 403/404/422, PATCH reset, OPML, диагностика, точный count и история, запрет внешних references). `scripts/api.py validate` — валиден, 42 paths / 50 operations. `node --test tests/browser/test_api_docs.cjs` — 1 passed (50 Swagger operations, локальные assets, CSP, отсутствие Try/Authorize/API calls); screenshot просмотрен.

Для изолированного checkout использованы Python/node dependencies главного checkout и Swagger assets sibling checkout через явные переменные. Запуск loopback-сервера в sandbox сначала запрещён bind, повтор с предоставленным sandbox escalation прошёл. Результат относится к standalone Swagger; встроенный Swagger и генерация библиотек — следующие задачи.
