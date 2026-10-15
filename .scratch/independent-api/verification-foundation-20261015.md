# Задача 01: возобновление реализации 15 октября 2026

Status: done для задачи 01; compatibility PENDING_REVIEW до финального допуска v3.
GitHub: https://github.com/tgenman/oko/issues/1

## Выполненный срез

- Canonical `api/openapi.yaml`: v3.0.0, 50 операций. Перенесён исправленный полный проект из предыдущей подготовки. Для 45 discriminated unions добавлены явные mappings wire literals: без них TypeScript-генератор получал противоречивые intersections и `never`.
- Сгенерированы Python DTO и TypeScript SDK. Переходные consumers используют отдельно генерируемые legacy-библиотеки из неизменённого reviewed v2 baseline. Это временный expand–contract путь до задачи 07.
- Через v3 работают status, POST/PUT/DELETE нейтральной клиентской сессии и stop. Создание отвечает 201; генератор и installed smoke теперь учитывают объявленные успешные статусы, включая 201.
- Host владеет lease/grace и сессиями; Runtime получает допуск/завершение. Explicit API-only работает без presence, Node и frontend assets. Повторный запуск проверяет профиль и major работающего API.
- Веб-владелец получает lifetime, создаёт/продлевает/закрывает нейтральную сессию, использует серверный renew interval. Cleanup подписки не вызывает business-stop. SDK проверяет major перед мутацией и сохраняет typed error code/details/correlation IDs.
- Сохранена проверка обычного compatibility gate: breaking v3 не принят как release. Reviewed baseline не менялся.

## Проверки и отказы

1. Начальный runner остановлен sandbox-запретом `ps`; повтор с разрешением прошёл.
2. Первый целевой Python-прогон: 1 PASS / 2 FAIL — процессу не хватало loopback-разрешения, обычному приложению — актуальной frontend-сборки.
3. Candidate build прошёл; следующий прогон обнаружил `never` в lifetime union. Исправлены discriminator mappings. Следующий кандидат: сборка, mypy, schema/codegen и 29 Python-сценариев PASS.
4. После добавления typed internal error envelope mypy обнаружил два неверных аргумента DTO; исправлены. В том прогоне browser/Python были NOT RUN, не PASS.
5. Следующий изолированный кандидат: generation/drift/types/build PASS, **33 Python PASS**, browser **6 PASS / 2 FAIL**. Оба browser-отказа были assertion failures (fixture 60/60 вместо defaults и прежний close URL), не timeout. В числе 33 — три проверки installed wheel через настоящий HTTP/process: API-only без assets/Node, работа с сессией и чистая остановка.
6. Текущий быстрый прогон: **21 schema/generator test PASS**, включая настоящие TypeScript compile-negative/narrowing fixtures; mypy **59 файлов PASS**. Отдельная сборка текущей изолированной копии PASS, browser **6 PASS / 2 SKIP**, lifecycle **5 PASS**. Последний lifecycle-сценарий проверяет устойчивое к отмене внешнее ожидание и запрет поздней фиксации после закрытия последней сессии.

Полные прежние runner-отчёты: `/private/tmp/oko-v3-resume-checks/`. Быстрый прогон выбранного дерева: `/private/var/folders/_g/b8j10z1d3db8wtgxyks8jk1m0000gp/T/oko-api-fast-nd7obgvq/fast-verification.json`. Source digest: `365577d5cf1f5eceb66ef68cdc9913ee8783e038f9bbcdbc02f80d1cbc2aa305`. YAML SHA-256: `76ed25ad8f9b98d41cf8511c2cdee27574d8ff5d2813428100d452b5f741fbb6`.

## Решение пользователя о проверках

Пользователь попросил временно выключить падающие долгие проверки и отложить их разбор. Временно помечены `skip`, без удаления тел:

- `neutral web and independent HTTP sessions coexist and close independently` — новый совместный HTTP/web + BFCache сценарий;
- `reader closing sends an authorized keepalive and releases displayed media` — обновлённый close/keepalive сценарий.

Существующий отдельный BFCache restoration и остальные шесть browser-сценариев продолжают проходить. Два SKIP не считаются PASS. Screenshots нового отключённого сценария не получены и не просмотрены. Полный `check all` не запускался; повторные полные candidate-сборки прекращены по указанию пользователя.

## Остаток на момент промежуточного отчёта

Задача 01 не помечена `done`: требуется сверка всех её критериев и независимое ревью. Строгий textual query/path parsing остальных операций, полные producer mappings состояния, настройки/receipts, чтение, подключения и recovery переходят по согласованному порядку задач 02–06. Общая приёмка, удаление legacy-пути, semantic review и обновление baseline — задача 07. Telegram domain implementation остаётся входной зависимостью задачи 05.

Рабочая ветка `codex/independent-api-resume`. Снимок до возобновления: `/private/tmp/oko-api-resume-20261015`. Исходные dirty-правки сохранены. Коммит, публикация и перезапуск пользовательского экземпляра не выполнялись.

## Финальная приёмка задачи 01

Пользователь отдельно поручил закончить только №1, сделать commit/push/PR и закрыть Issue. Задача принята в своей области; прежний абзац о незавершённом ревью выше описывает промежуточное состояние. Два согласованных browser skips сохранены, не названы прошедшими. Выполнение задач 02–07 в этот результат не входит.

Проверялось подготовленное дерево относительно `main` / `75f6fe1860cb159ab46db026815c25986c7da5b7`, изолированное через `task_changes.py prepare` + `checkout`, затем единый candidate-контекст. Чужие правки AGENTS, GLOSSARY, tracker, плотности дерева и Telegram-topics в объём не включены. В PR входят только реализация №1 и необходимые исходные спецификации.

- Реестр повторно сверен с immutable исходной версией: 50 операций / 77 схем; актуальный reviewed v2 baseline — 49 / 72; кандидат v3 — 50 / 350. Первичная проверка таблицы ожидала Markdown-backticks и отказала; исправленная проверка ячеек сверила все имена, потерь нет.
- TypeScript всех трёх конфигураций — PASS; mypy 58 файлов — PASS.
- Python: 29 PASS (schema, Python/TypeScript positive/negative generation, HTTP lexical parsing, lifetime, cancellation-resistant source, candidate staged-context integrity).
- Browser transport/session: 6 PASS / 2 SKIP. Отдельный короткий Playwright сценарий снял desktop/mobile из той же сборки; оба изображения просмотрены. Существующая мобильная компоновка не менялась.
- Настоящий wheel собран штатным package builder, установлен вне checkout: API-only без frontend/Node и обычный reader; status/stop, create/renew/close и process exit 0 — PASS. Wheel явно UNREVIEWED, `publication_allowed=false`; это свидетельство задачи, не разрешённый release.
- Независимое ревью code-review: Standards 0 findings, Spec 0 findings. Отдельно проверено исключение staged из предварительной генерации runner: exact-index drift выполняется внутри staged gate и не меняет working libraries.

В точном staged gate candidate-контекст передаётся только при совпадении bytes staged canonical и reviewed baseline с проверенным родителем; изменённые inputs отклоняются. Обычные gates без контекста сохраняют строгий отказ для breaking v3. Полный check all и merge/release не заявляются. Baseline не обновлялся.

Критерии C01 покрыты полным canonical, examples/formats, inventory и semantic diff; C02 — генераторами, compile-negative fixtures, concrete DTO/output validation, error envelope, lexical parsing и candidate/staged/package path; C03 — host Lifetime, нейтральным HTTP, explicit/process/cancellation; C09 — generated transport/session owner/major checks и указанными browser доказательствами с двумя принятыми исключениями; C12 — CLI/lock/static/docs и установленной поставкой. Операции остальных семейств остаются только проектом контракта, как прямо разрешает граница задачи 01.

Сводные результаты и digests: [task1-checks.json](evidence/task1-checks.json). Снимки: [desktop](evidence/task1-desktop.png), [mobile](evidence/task1-mobile.png). Исходный полный лог и exact candidate source остаются в `/private/tmp/oko-task1-final-evidence/`. Публикация выполняется отдельной task-веткой; пользовательский экземпляр не перезапускался.

### Исправление exact-index генерации перед коммитом

Штатный pre-commit сначала отказал: Python SDK отличался только порядком импортов. В минимальном index tree отсутствует пакетный `oko/__init__.py`, поэтому isort выводил `oko.reader.wire` как third-party. Генератор закрепляет `--project oko`; полные generated bytes остаются прежними. Новый регрессионный тест сравнивает результат полного checkout с минимальным scripts/templates tree. Затронутый набор строгой генерации: 5 PASS. Повторное независимое Standards-ревью исправления: 0 findings. Staged gate выполняется штатно с validated candidate-контекстом, без отключения hooks.
