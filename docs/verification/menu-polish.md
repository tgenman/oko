# English reader interface and compact context menus

2026-10-18. Base: `5639f3b8f86848688e8735235c01cdd54a9a397f`.

## Scope

Remove the visible news/feed title headers from context menus. Increase menu and folder-submenu text from 13 px to 16 px, preserving 38 px single-line rows and existing widths. Keep the accessible target name and the target identity in confirmations.

The user clarified that the whole interface should be English: navigation, settings, source connections, reading actions, confirmations, state panel, diagnostic controls, accessibility labels and date groups. Existing publication content, folder/feed names, prompts, model output and raw diagnostic details retain their original text. Known built-in service explanations are translated at display boundaries; saved API responses/diagnostics are preserved. Unrecognized service error details retain their original language.

## Checks performed

- First targeted browser attempt failed because this new worktree had no frontend build. After building, both context-menu files passed: 9 tests plus their helper-file discovery, no failures, 8.48 s browser time.
- The first broad UI attempt was interrupted to update remaining partial-text test expectations. Syntax, lint, build, HTTP and type checks had passed; its browser result was not complete.
- Broad UI run `.scratch/check-runs/20261018T195137Z-ab52d1ca/report.json` failed on old API-version text, unscoped A selectors matching Add Feed/All Items, and the descriptive All Read badge expectation.
- Broad UI repeat `.scratch/check-runs/20261018T195313Z-5f31776f/report.json`: 133 browser passes, 2 failures, 2 existing skips; HTTP 9 passes, syntax/lint/build/types/diff pass. Remaining failures were an unscoped C selector matching Collapse and a Web Access capitalization mismatch. Both corrected.
- Final `./scripts/check ui --files tests/browser/test_opened_material.ts tests/browser/test_telegram_v3_http.ts tests/browser/test_initial_history.ts tests/browser/test_history_outcomes.ts tests/browser/test_news_context_menu.ts tests/browser/test_feed_context_menu.ts` passed. Report: `.scratch/check-runs/20261018T195445Z-0f41ecae/report.json`; total 23.02 s, browser 13.91 s. Full UI was not repeated locally after this correction; complete CI is the final broad check.
- Additional Playwright visual check passed in 3.35 s: no h3 in either menu; all five news actions and feed/folder actions measured 16 px font and 38 px row height; no page errors. Screenshots viewed: `/private/tmp/oko-news-menu-english.png`, `/private/tmp/oko-feed-menu-english.png`, `/private/tmp/oko-settings-english.png`. Russian synthetic folder and publication names remain unchanged.
- Service presentation smoke check: built-in explanation and dynamic subtitle count translate; unknown original details are unchanged.

All runtime checks used synthetic sources, model fixtures and temporary storage. No personal subscriptions/accounts or real processing were used. No tests disabled and no timeouts raised.
