# Persistent reader and remembered login at http://localhost:8765

GitHub: https://github.com/tgenman/oko/issues/50
Status: done
Blocked by: Нет

Specification: [Docker runtime](../spec.md).

## Acceptance

- [x] Explicit web lifetime continues without tabs; disabled state persists across restart.
- [x] Separate bind address and public URL; strictly validate the configured local Host and Origin.
- [x] Bootstrap remembered revocable browser session using existing bearer; clean browser remains unauthorized.
- [x] Verify reopen without fragment and restart through HTTP and Playwright.

## Result

Implementation: `2aea94c`. Full isolated check all PASS (184.14s); prepared Docker image smoke PASS on localhost:8765. Independent Standards and Spec re-reviews: no actionable findings. See [verification](../../../docs/verification/docker-runtime.md). Native CLI0.163 approval, LaunchAgent startup and personal-data migration remain separate deployment steps.
