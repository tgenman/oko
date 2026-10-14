# 02. Генерация библиотек и подключение бэкенда

Status: ready-for-agent
Blocked by: 01

Спецификация: [spec.md](../spec.md), детали: [API-first](../api-contract.md). Реализация пока не разрешена.

## Результат

Из одного OpenAPI генерируются TypeScript SDK и Python DTO. Backend использует generated-модели на HTTP-границе, frontend SDK проверен и готов к подключению в этапе 04.

## Объём

- Закрепить `@hey-api/openapi-ts` (types/SDK/Fetch) и `datamodel-code-generator` (Pydantic v2) с конфигурацией. Сначала проверить трудные семантические случаи из api-contract.md; неподходящие defaults генератора не меняют API.
- Создать локальные generated-модули, provenance/digest и явный `api:generate`; сохранить output в Git без ручных правок.
- Добавить генерацию во временное место и проверку drift, детерминизма и совместимости контракта. Python-ветка запускается отдельно от Node; общая CI проверяет обе.
- Подключить generated request/response models к handlers и output validation; удалить заменённые ручные wire descriptions. Адаптеры в доменные модели не меняют Runtime/Store и прежние статусы ошибок.
- Проверять соответствие operation IDs/methods/paths, параметров, security и объявленных responses реальной HTTP-реализации. Не выдавать canonical YAML за доказательство соответствия кода.
- Перевести схемы browser snapshots на источник OpenAPI и совместимый validator; прежний экспорт Python contracts больше не источник истины.

## Приёмка

- [ ] Оба generated-пакета воспроизводимы, типизируются и происходят из одного digest; намеренно устаревший output ломает проверку.
- [ ] Реальный backend импортирует DTO, проверяет запросы/ответы и сохраняет unset/null, error statuses и диагностические headers.
- [ ] TS SDK содержит функции операций и позволяет передать fetch/client/signal/keepalive, читать raw Response/headers и media без JSON-декодирования.
- [ ] Нарушение схемы или необъявленный route обнаруживаются проверками; metadata `operationId` не дублирует ручной контракт.
- [ ] Проверки не переписывают source checkout, не запускают пользовательские источники и не скачивают схемы из сети.

## Проверка

Генерация дважды, compile/typecheck обеих библиотек, негативные проверки схем/маршрутов и существующий HTTP-набор. Полный frontend SDK production-use проверяется в этапе 04, а упаковка и Swagger — в этапе 03; эти части не объявлять выполненными заранее.
