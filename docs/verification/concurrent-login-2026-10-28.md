# Concurrent login, 28 October 2026

A synthetic HTTP test held the first password check and started a second login. Before the fix, the first returned 303 and the second 429. After the fix both returned 303. Waiting is bounded; password checks execute one at a time. Cancelling a waiting request removes it; cancelling a running request keeps the slot until its thread finishes and revokes any undelivered session.

Regression tests for simultaneous logins and both cancellation cases passed. `./scripts/check python browser --files tests/reader/test_password_login.py tests/browser/test_password_login.ts --browser-workers 2 --compact` passed: 17 Python and 4 Playwright tests using a disposable PostgreSQL database. Independent review found no remaining critical issues.

The server did not record which branch produced the user's 429, so concurrency is a confirmed reproducible cause, not proven attribution of that particular response. A production login test was stopped when it may have overlapped the user's login.
