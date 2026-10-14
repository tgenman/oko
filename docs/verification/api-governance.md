# API-first: baseline, semantic review и точный Git index

2026-10-14. Ветка codex/oko-api-governance, база 6146286. Snapshot до правок: /tmp/oko-api-governance-start. Объём ограничен development/build/API-governance tools; canonical schema и runtime/frontend/backend behavior не менялись. Тикет 20 остаётся открытым для полной интеграции и независимого ревью.

Реализованы conservative wire-change comparison, первоначальный полный baseline текущего API, explicit version/review update с сохранением evidence и exact-index regeneration обеих библиотек в temporary tree. Незаметное изменение staged baseline отвергается; documentation-only change разрешено. Проверка references отвергает внешние и отсутствующие локальные targets до OpenAPI validator.

Фактические проверки:

- TDD: первые три governance CLI tests дали 3 FAIL (отсутствующая команда, неотклонённый dynamic reference). После реализации — 3/3 PASS. Затем CLI/index governance suite — 4/4 PASS, включая invalid schema, wire changes, read-only comparison, version/review, generated index drift и неприоритетность unstaged bytes.
- После добавления retained-review gate и проверки pinned index tools affected staged test PASS. После добавления успешного reviewed staged baseline transition этот же affected test PASS (1/1, 51.66s); остальные три неизменных CLI scenarios не повторялись.
- Initial combined governance/hooks run: 6 PASS, 5 hook FAIL из-за sandbox `Operation not permitted: ps`, до проверки staged bytes. Existing hook suite с разрешённым process access: 7/7 PASS; assertions не изменены, TS/JS/Python partial staging сохранён.
- Existing contract/reader HTTP suite tests/api/test_contract.py: 4/4 PASS (8.33s), synthetic temporary state.
- npm run api:compat, api:check, api:lint и build: PASS. api:check проверяет baseline, Python DTO и TypeScript SDK; frontend/package build используют тот же check. Baseline и generated source при этих check commands не переписываются.
- git diff --check: PASS. Реальные аккаунты, личные источники и работающий пользовательский экземпляр не использовались.
- Prepared exact index проверен через scripts/check staged: PASS, 16.44s, report 20261014T140915Z-e1da2205. Включены canonical/baseline gate и regeneration обеих библиотек из index inputs.

Ограничения: автоматический diff консервативен и не доказывает semantic compatibility. Семантическая оценка и качество review text остаются ответственностью independent reviewer; потенциально совместимые additions также требуют explicit baseline transition. Реальная package/full UI проверка и общий code-review выполняются основной задачей миграции.

Интеграция: scoped governance commit ebddd8e; primary f70ee6e incorporated без конфликтов в merge 2831721. После изменения build/config повторён npm run build: PASS (baseline gate, обе generated-библиотеки, strict TypeScript, production reader и local Swagger initializer). Неизменные CLI/index/HTTP suites не повторялись.
