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

## 2026-10-14 — входные wire DTO

Все JSON body handlers используют generated входные DTO; прежние доменные нормализаторы и ограничения выполняются до создания DTO, Runtime получает доменные объекты через явное преобразование. Ручные транспортные HistoryPause/CompletionRequest/ReportAction удалены. YouTube сохраняет прежний400, остальные body-ошибки422. Отсутствующие поля и null остаются отдельными исходами.

- mypy всех52sourcefiles — PASS.
-50затронутых HTTP-тестов настроек/подписок/начальной истории/diagnostics/YouTube/contracts — PASS.
- Дополнительный HTTP red на наивном ISO timestamp выявил изменение генератором прежней нормализации. Выбран datetime вместо AwareDatetime;16debug+generator проверок PASS. Публичные timestamps сохраняют доменную семантику.
- Полный tests/reader +canonicalcontract —156/156PASS за77.57s после всех inputDTO изменений. Реальные сетевые adapters не использовались.
- uv.lock обновлён под зависимости packagebuilder/installer из04;70закреплённых пакетов.

## 2026-10-14 — guards и renderer

TS/TSX syntax включён в единый runner и проверку точных staged bytes. Частичный staging проверяет индекс, а не рабочий файл; strict project typecheck остаётся отдельной обязательной частью build.35hook/runner тестов PASS. Пробный запуск hook-тестов без разрешения ps не смог выполнить staged-набор; успешный запуск выполнен с разрешённым процессным доступом.

Единственный React renderer использует стабильный ключ для каждого portal host, включая безымянные контейнеры форм. Ошибки render отправляются в диагностику через microtask, чтобы не вызывать вложенный flushSync из componentDidCatch. Typecheck/build и syntax renderer.tsx PASS.

## 2026-10-14 — общий TS transport и docs initializer

Общие HTTP error/diagnostic правила перенесены в strictTS и используются SDK и transport без дублирования декодера. JStransport временно только реэкспортирует TSфабрику до окончательного entrycutover. Swagger initializer также typecheckedTS; сборка выдаёт его как обычный локальныйJSasset. Transport facade выбирается по фактическому isEntry Viteoutput, а не по совпадению имени sharedchunk. Pydantic2.13.5 явно закреплён какruntime dependency для используемого MISSING API.

Typecheck/build PASS;5transport+installedreader/Swagger browser сценариев PASS (report20261014T140459Z-60ec6611). Проверяется wheeloutsidecheckout/PATHwithoutNode, явнаяSwaggerавторизация и token/session правила.

## 2026-10-14 — общий entry/root cutover перед приёмкой

Entry перенесён в strictTSX, структура страницы — ReaderShell. Все owners и shell используют один React root с portals; production JS renderers/reexports и allowJs удалены. Общий JSON-декодер сохраняет ошибки, media операции классифицируются из canonical generated metadata, SDK binary ответы читаются как Blob (включая text/plain файлы), headers/rawResponse сохранены. Preview/media URL ресурсы остаются у их владельцев.

SDKbinary test сначала4/5 из-за неполного временногоentry реэкспорта; переключён прямойTSentry. Повтор5/5transport PASS. Typecheck/build PASS. ОбщаяUI-проверка13/13PASS, включая34проверенных геометрических состояния; desktop/plain и mobile/RSS снимки просмотрены. Просмотр выявил потерю toolbarSVG при переносе commandcomponent, icons восстановлены по исходной разметке; итоговые снимки ещё проверяются в полном прогоне.

Окончательная приёмка и независимое ревью пока не завершены,20claimed.

## Полная приёмка

Подготовка проверочного дерева на базе8ac07e5; full runner и независимые Standards/Spec review начаты. Результаты будут дописаны после завершения.

## 2026-10-14 — первая полная приёмка и ревью

Подготовленное дерево8ac07e5: contracts/syntax/types/build/http/diff/CLI PASS. Python433passed/1failed за308.81s: старый test_telegram_form_contains_both_initial_history_choices искал поля в статическомHTML. Эти три assertions перенесены в существующий browser initial-history сценарий;3/3browser PASS. Lint не стартовал из-за отсутствующего legacyJS glob, browser NOT RUN. Defaultglob теперь добавляется только при наличии authoredJS; явно переданные ошибочные paths по-прежнему отклоняются. Остальные Python-наборы не изменялись.

Независимые Standards и Spec review от5b7a7e8 до8ac07e5: Standards1finding; Spec2findings. Оба указали BFCache lifecycle; Spec дополнительно нашёл stale status-target navigation. Исправлены:

- cachedpagehide освобождает ресурсы, но оставляет одноразовый pageshow hook для reload; closing UI инвалидирует admissionepoch. Новый браузерный тест RED(timeout reload)→GREEN.
- owner.open возвращает актуальность всего navigationintent; закрытие/reopen/select/Back/новыйpage лишают старый intent права перехода. Два browser regression сценария подтверждают reopen и переход внутри того же меню; первичныйreopen RED(home→codex)→GREEN.
- После исправлений20status/transport/lifecycle browser PASS; отдельно2status-target PASS. Основной backend/storage/sources не изменён.

Полный результат: /tmp/oko-final-acceptance-check/.scratch/check-runs/20261014T181333Z-ff9090ad/report.json. Повторные проверки и re-review affectedfindings ещё выполняются.
