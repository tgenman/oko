# Login sessions, 28 October 2026

A synthetic HTTP regression reproduced a 429 when 16 unexpired sessions for one user existed. Removing the two session-count checks allows a valid login while preserving existing sessions; expired sessions are still removed on login. The 30-day lifetime, logout, and attempt rate limit are unchanged. Session records can grow with successful logins within those 30 days.

- `./scripts/check python --files tests/reader/test_password_login.py --compact`: 11 passed on disposable PostgreSQL, including both old count thresholds.
- `./scripts/check python browser --files tests/reader/test_password_login.py tests/browser/test_password_login.ts --browser-workers 2 --compact`: 11 Python and 4 Playwright passed; the runner reported a changed checkout because the Python tests were consolidated during the run. The changed Python suite passed again separately above.
- Production before the code fix: the user requested removal of 16 accumulated `reader` sessions. Two browser observations after removal: login to first cards 5.20 s and 4.60 s; four folder switches 0.80–0.82 s in each run; material opening 0.80 s and 1.30 s. One first card response took 4.90 s; a 40-card response was 21,758 bytes. These are observations, not percentiles. The second run confirmed logout. No production code update was performed for the session fix.
