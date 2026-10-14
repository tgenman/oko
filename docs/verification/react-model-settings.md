# React model settings — 2026-10-14

Implementation: typed React pages for llama.cpp and Codex use the generated SDK directly. The independent model owner preserves drafts, configured choices, explicit saves/checks, saved A with newer draft B, and admission guards across navigation. Catalog retry remains separate from successful save reconciliation.

Checks used temporary storage, synthetic model/catalog adapters, and the existing production HTTP/Playwright seam. No personal model connections were used.

- TDD red: removing the three legacy model page containers made `mobile model screens save connection and independent Codex choices through production HTTP` fail because the llama.cpp page link was missing.
- `npm run typecheck`: passed.
- `npm run build`: passed, including OpenAPI validation and generated Python/TypeScript library freshness.
- Browser run `test_codex_settings.cjs`, `test_codex_settings_http.cjs`, and `test_ui_lifecycle.cjs`: all 9 model scenarios and 4 lifecycle scenarios passed. One unrelated editor scenario failed because the existing originals link lacked an accessible description; handed to the settings owner.
- Follow-up targeted run: production HTTP mobile model screen and interrupted Codex catalog retry both passed (2/2).
- Actual Playwright screenshot `react-models-mobile.png` inspected: four choices are rendered, save/check actions and messages remain readable, no horizontal overflow at 390px.

Evidence is in the worktree check runs `20261014T134423Z-5f75de78` (red), `20261014T134613Z-379b76cc` (13/14), and `20261014T134648Z-26bc37b7` (2/2 and screenshot).

Integration limitation: these checks used a temporary menus wiring that removed the legacy model containers and mounted `Models` persistently. That wiring is owned by the settings integration ticket and is intentionally not included in this component commit. Ticket 13 can close after the settings branch mounts this component and removes the old model renderers.

Settings integration completed on 2026-10-14: Models mounted persistently, legacy model renderers removed, all 9 model scenarios passed again in the 32/32 integration batch. See [settings verification](react-settings.md).
