# Persistent reader and remembered login at http://localhost:8765

GitHub: https://github.com/tgenman/oko/issues/50
Status: claimed
Blocked by: Нет

Specification: [Docker runtime](../spec.md).

## Acceptance

- [ ] Explicit web lifetime continues without tabs; disabled state persists across restart.
- [ ] Separate bind address and public URL; strictly validate the configured local Host and Origin.
- [ ] Bootstrap remembered revocable browser session using existing bearer; clean browser remains unauthorized.
- [ ] Verify reopen without fragment and restart through HTTP and Playwright.
