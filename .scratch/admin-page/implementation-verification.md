# Admin implementation verification

Issue: https://github.com/tgenman/oko/issues/203
Prototype: codex/admin-page-prototype, commit 7ed4d248 (not merged into main).

## Original base c31612a8

- TypeScript and staged checks passed.
- Focused browser regressions: 8/8 passed, report 20261103T024435Z-2709ec72.
- Real HTTP/browser Admin, no-browser source commands passed in targeted runs.
- Lifecycle run: 18 passed; discovery crash recovery needed its original explicit collect command restored.
- A subsequent single process test hit the stale frontend build guard, not the crash scenario.
- Earlier browser runs failed due to missing test PostgreSQL, hidden navigation controls (fixed),
  obsolete reader-only navigation in tests (fixed), and timeouts. They are not counted as passes.
- Independent review found four issues (profile compatibility, local auth links, panel navigation,
  truthful runtime status); all corrected, follow-up review found no blockers.
- Full all run 20261103T052437Z-1a870621 was interrupted when fresh origin/main was found to
  contain subsequent publication/Telegram changes. It was not a passing run; it had already
  reported a publication-processing timeout. Final verification must use the integrated base.

All data are synthetic; disposable PostgreSQL is isolated from the working installation.
No production deployment has been performed.

## Integration on fc538f3c (2026-11-03)

- Full `check all --compact --in-place --base origin/main --browser-workers 2`:
  Python 635 passed / 4 failed / 2 skipped; Playwright 148 passed / 4 failed;
  Node 38 passed; tools 155 passed. Types, build, HTTP, lint, syntax and diff passed.
  Report: 20261103T060018Z-29879833. This full run was not green.
- All eight failures subsequently passed targeted checks. Navigation tests now return to
  reading before operating on its hidden controls or checking scroll position. The new
  test uses URL resolution for an absolute authenticated link. No production workaround
  was added for these test adaptations.
- No-browser tests now expect automatic collection; the history request budget excludes
  the preceding startup round, without increasing its four-request limit. The recovery
  timeout passed an unchanged targeted retry. Reports: 20261103T060752Z-8fb9e625 and
  20261103T061023Z-d4e17c07.
- English collection labels: 3 HTTP checks passed (20261103T060945Z-e9822559).
  Source titles and raw diagnostic messages remain original data.
- Reviewer requested stronger pending-retry cancellation coverage. With a long existing
  refresh interval to isolate rescan from retry, two blocked caption attempts are followed
  by viewing, Start and a clock advance: publication stays deleted and no third request.
  PASS with Admin direct URL/new-tab/Back/no-browser test: 20261103T061523Z-a4802677.
- Reader latency: 4/4 scenarios (104/400 publications, idle/processing), p95 <=143.9 ms
  against the existing 200 ms budget. Admin also passed in this run:
  20261103T061157Z-13689a15.
- Desktop/mobile production screenshots inspected and saved in previews/.
- Independent review of implementation and integration conflict resolutions found no
  blocking issues after corrections. CI and release verification follow in the PR.
