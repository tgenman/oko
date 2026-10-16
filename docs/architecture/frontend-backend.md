# Фронтенд и бэкенд Око

## Действующая архитектура — 2026-10-14

React + strict TypeScript находятся в frontend/src; Vite собирает готовые assets. frontend/src/main.tsx собирает владельцев чтения, настроек и состояния; shell.tsx задаёт структуру, renderer.tsx держит один React root и его именованные portals. Владельцы хранят локальное состояние и управляют своими ресурсами; универсальный application store/router не вводился. reader-session.ts единолично владеет heartbeat/polling/BroadcastChannel/page events; cleanup не отправляет stop/close.

Редактируемый HTTP-контракт — [OpenAPI](../../api/openapi.yaml). Generated Python DTO используются в входных и выходных handlers; доменная валидация и Runtime/Store сохраняют прежние обязанности. Generated TypeScript SDK задаёт операции. APIreader настраивает отдельный Fetch client и учитывает исходы команд; общие errors/transport сохраняют diagnostic headers, unknown network outcomes и bearer только своего origin. Binary operations выводятся из canonical metadata и читаются как Blob, а media links используют авторизованный raw fetch.

[API-first policy](../../api/README.md) задаёт валидацию схемы и проверку актуальности generated-библиотек. Browser snapshots получают schema2020-12 из canonical bundle и используют Ajv8; ручных wire-схем нет. Standalone Swagger не создаёт Runtime. Встроенные docs поставляются локально, требуют явного Authorize для Try и не создают reader lease.

Python static delivery проверяет content hashes, ревизию контракта и freshness checkout до Runtime. Assets находятся в immutable generations с atomic pointer; старые chunks остаются доступны открытым вкладкам при watch rebuild. Читатель запускается [из checkout](../development/package.md); сборка установочного пакета временно удалена.

## Владение

Фронтенд владеет DOM, фокусом, навигацией, черновиками, локальными предпочтениями и допуском позднего ответа к текущему показу. Runtime владеет исполнением, отменой и внешними ожиданиями; Store — фиксацией и восстановлением. Сервер формирует состав чтения и эффективные настройки. Браузер не вычисляет membership и не повторяет правила актуальности обработки.

Действуют решения [UI-состояния](ui-settings.md), [открытого материала](opened-material.md), [управления работой](reader-work.md), [состава чтения](reading-composition.md) и [согласования обработки](processing-coordination.md).

## HTTP interface

Seam между фронтендом и бэкендом — HTTP interface читателя. Существующие URL, локальная сессия, проверка host/origin и защитные заголовки сохраняются. Успешная серверная запись остаётся состоявшейся после закрытия формы; отмена браузерного ожидания не откатывает её. Несколько читающих запросов не обещают один транзакционный снимок.

Запущенный из checkout читатель выдаёт интерфейс и API на одном origin; Node нужен для подготовки frontend, но не для работы сервера. Неизвестные маршруты данных не превращаются в HTML; обход пути, скрытые файлы и symlink за пределы каталога assets отклоняются. Правила единственного API v3: [api/README.md](../../api/README.md).

2026-10-16: consumers читателя и настроек полностью используют v3 SDK; старые handlers и v2 библиотеки сняты. Middleware возвращает прежним запросам типизированный 410 после security checks. Доменный Store выдаёт внутренние снимки с историческими фактами; отдельные проекции строят generated v3 DTO. Контролируемые browser fixtures могут описывать исторические данные сценария, но доставленные wire responses проверяются только по canonical v3; реальная HTTP-приёмка идёт без такого преобразования.
