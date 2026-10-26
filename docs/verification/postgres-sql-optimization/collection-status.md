# Collection status: implementation evidence

Base: `fe0dd9aadca7de4881fe3e976b2408260c4cb820`.

The existing collection HTTP boundary now obtains only the selected feed for details.
CollectionStatus joins attempts in one query per bounded group of feeds, projects
only scalar attempt/result metadata for summary, and reads diagnostic bodies only
for details. Three disjoint job-kind branches preserve the existing discover,
fetch and download-media classification. The shared classifier and wire shape stay
unchanged; unused job payloads are excluded.

## Red → green

The new SQLite HTTP test first failed on growth from 38 to 308 SQL statements for
10/100 feeds, while source state, operation, diagnostics and summary/detail equality
already passed. A first fixture assertion expected a connected source; the synthetic
feed is intentionally unavailable, so it was corrected to assert operation state.

Native PostgreSQL baseline was measured by loading the two original collection
methods from the pinned Git base into an isolated Python process, preserving the
same new HTTP test fixture. No checkout files were reverted. Its performance
assertion failed as expected. The complete new implementation passes the same case.

| Adapter / size | Summary SQL before → after | Detail SQL before → after | Summary JSON input bytes before → after | Detail JSON input bytes before → after |
|---|---:|---:|---:|---:|
| SQLite / 10 | 38 → 9 | 38 → 9 | 215,712 → 1,190 | 215,712 → 21,570 |
| SQLite / 100 | 308 → 9 | 308 → 9 | 2,157,384 → 11,990 | 2,157,384 → 21,570 |
| SQLite / 1000 | not measured → 23 | not measured → 9 | not measured → 120,890 | not measured → 21,570 |
| PostgreSQL / 10 | 38 → 9 | 38 → 9 | 215,682 → 1,140 | 215,682 → 21,568 |
| PostgreSQL / 100 | 308 → 9 | 308 → 9 | 2,157,074 → 11,490 | 2,157,074 → 21,568 |
| PostgreSQL / 1000 | not measured → 23 | not measured → 9 | not measured → 115,890 | not measured → 21,568 |

Sizes independently grow feeds and failed attempts while the selected feed retains
one operation. SQLite uses failed fetch jobs; PostgreSQL uses failed discovery jobs.
Each diagnostic includes 1000 synthetic traceback lines. At 1000 feeds the summary
crosses the 400-feed batch boundary; details remain fixed. Summary/detail sources
must compare equal, exact details remain available, and counts are asserted.

`SQL` is request-context execute calls, not trigger statements. JSON input bytes are
bytes parsed by Python `json.loads`, not total PostgreSQL wire traffic. There are no
HTTP-triggered writes or background collection in these fixtures. Measurements are
not a latency SLA; the full summary response necessarily grows with feed count.

Checks:

- `OKO_TEST_POSTGRES_URL=<isolated synthetic server> .venv/bin/python -m pytest tests/reader/test_collection_status.py -q -s`: 7 passed, including native PostgreSQL; warning about Starlette/httpx deprecation.
- `.venv/bin/python -m mypy oko/storage/collection_status.py --follow-imports=silent`: passed.
- Task-scoped `git diff --check`: passed.

Existing tests cover running attempt identity, refresh, pause, restart, wait/error
resolution, cancellation and terminal unavailability, plus redaction of session
secrets. PostgreSQL schema is disposable and dropped by the existing fixture.

Integration note: after bounded feed metadata batching landed, the 1000-feed
summary uses 23 statements rather than 13. Both cost tests initially rejected this
with a budget allowing only the collection batches. The budget now includes the
two extra metadata batches; detailed reads remain 9. This failure affected cost
assertions only; source equality and diagnostic assertions passed.
