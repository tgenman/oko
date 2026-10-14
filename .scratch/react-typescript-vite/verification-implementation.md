# Реализация React + TypeScript + Vite / API-first

Ветка: codex/react-typescript-api-first. База до реализации: 5b7a7e823480fc6fc9836ab8f994e586cf3dfded. Подготовленные документы были исходными изменениями задачи; сохранён снимок /tmp/oko-implement-start-20261014. Первые два независимых среза реализованы в изолированных рабочих деревьях и интегрированы.

## 2026-10-14 — 01–03

01: 50 операций/77 схем, canonical OpenAPI3.1 и самостоятельный Swagger. Четыре contract/HTTP-теста и один Playwright-тест прошли; снимок просмотрен. Детали в тикете01.

02: Vite production/watch, atomic generations, freshness before Runtime. Первоначальный полный UI дал 108/111; три проверки путей/ресурсов адаптированы к реальной поставке. Полный повтор дал111/111 +11HTTP. TS7 несовместим с compilerAPI HeyAPI; закреплён TS5.9.3. Подробности: verification-vite.md.

03: Python datamodel-code-generator0.83.0, HeyAPI0.97.3, строгие wire DTO и SDK. Все JSON responses FastAPI используют generated operation map; status consumer использует SDK и React. Ручной contracts.py заменён экспортом schema2020-12 из canonicalbundle, браузерные снимки проверяются Ajv8/2020-12. Generated output не правится вручную; api:check пишет только во временные каталоги. Общая ревизия проверяется при запуске; frontend build требует актуальных обеих библиотек и TS typecheck.

Проверки и наблюдения:

- До переноса: 11 status/settings browser-сценариев PASS, desktop/mobile снимки просмотрены.
- Генератор: red на JSON date-time, исправлена конфигурация с Strict primitive types и форматами; red на optional discriminator/MISSING, исправлен шаблон. Union default/required/null/optional/alias/additionalProperties/boolean-vs-integer/большие целые, deterministic regeneration и drift — PASS.
- mypy всех51Python sourcefiles и strictTS — PASS. exactOptionalPropertyTypes не включён: текущий generated Fetch client несовместим с этой дополнительной опцией; strict и noUncheckedIndexedAccess остаются включены.
- Первый интегрированный browser/HTTP прогон не стартовал из-за разного порядка хеширования nestedpaths в Node/Python; сортировка унифицирована. Повтор 13 browser-сценариев PASS (status/transport/contract), report20261014T125648Z-f1ba886e; desktop/mobile снимки просмотрены, оформление сохранено.
- tests/reader +tests/api +generator:155/156PASS; оставшаяся проверка запрашивала старый /opened-material.js. Перенесена на actualentry Vite, повтор этого сценария и генератора2/2PASS. Серверные assertions не ослаблены.
- build/generation/typecheck и targeted7Python проверки fresh/missing/damaged/watch/reader/generator — PASS. Дополнительная проверка stalecanonical на build-команде — PASS,1тест. Неподходящие generated RootModel defaults исправляются шаблоном, без изменения canonicalAPI.

Готовы только01–03. Полный React UI, session owner, остальные typed consumers, встроенные docs и installed-package smoke ещё не завершены. Пользовательский экземпляр не запускался; личные источники не использовались.

## 2026-10-14 — 05

SDK подключён к heartbeat/close/collect/stop; один session owner управляет таймерами, каналом и событиями страницы. Dispose не закрывает lease и не вызывает stop; только pagehide отправляет close с keepalive. Setup отложен в microtask, немедленный setup/dispose/setup не запускает первый цикл. Общий fetch adapter передаёт action identity и фиксирует неизвестные/подтверждённые исходы без повторов команд. Reporter не дублирует уже зарегистрированную SDK-ошибку и не сообщает неуспешную доставку diagnostics рекурсивно.

- Проверка18browser сценариев первоначально17/18: захват headers ожидал plainobject. Изменён на нормативный Headers API; transport-повтор4/4PASS.
- После подключения единственного React renderer:10/18PASS,8ошибок из-за оставшегося начального текста при portal handoff. Контейнер теперь очищается один раз при передаче React; status-повтор8/8PASS. Утверждения о тексте/фокусе/порядке сохранены. Остальные10сценариев не менялись и прошли.
- Процессные lifecycle сначала8passed/8failed (subprocess exited before readiness, без сетевой эскалации); повтор с разрешённым loopback16/16PASS. Подробный исходный stderr дочерних процессов этот helper не сохраняет.
- strictTS/build/api drift — PASS. Desktop/mobile панели сохранены в report20261014T131359Z-63f2549c. Root-композиция через portals подготовлена для дальнейших независимых owners; полный UI ещё переносится.
