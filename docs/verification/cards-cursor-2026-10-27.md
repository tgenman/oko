# Cards cursor verification — 2026-10-27

- `npm run build`: passed; generated OpenAPI libraries are current.
- `./scripts/check python` for `test_cards_cursor_http.py` and `test_completion_query_cost.py` against disposable PostgreSQL: 13 passed.
- Earlier full UI run: 235 Playwright passed, 3 skipped; 116 Node and 9 HTTP checks passed. This run predates the last review fixes.
- Synthetic 104/400-item libraries: individual page GET observations 26–224 ms, about 12.8 KB for 40 cards; ETag revalidation 14–464 ms, empty body. These are individual observations, not percentiles or production measurements.
- An attempted full run without `OKO_TEST_POSTGRES_URL` failed database-dependent tests and was interrupted; it is not a successful full run. A later concurrent local browser run timed out preparing its fixture. Separate browser checks and GitHub CI are tracked in the PR.
- A newly ingested backdated item outside the checked head and visible windows can be missed if another item disappears at the same time and the total stays unchanged.
- Production has not been updated or measured for this change.
