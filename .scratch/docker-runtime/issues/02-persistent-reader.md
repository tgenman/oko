# Persistent reader and remembered login at http://oko

Status: ready-for-agent
Blocked by: Нет

Specification: [Docker runtime](../spec.md).

## Acceptance

- [ ] Explicit web lifetime continues without tabs; disabled state persists across restart.
- [ ] Separate bind address and public URL; strictly allow oko Host and Origin.
- [ ] Bootstrap remembered revocable browser session using existing bearer; clean browser remains unauthorized.
- [ ] Verify reopen without fragment and restart through HTTP and Playwright.
