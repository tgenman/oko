# PostgreSQL reading optimization — verification, 2026-10-26

Implementation: [PR #159](https://github.com/tgenman/oko/pull/159), resolves
[#158](https://github.com/tgenman/oko/issues/158) on merge. Specification:
[local agreed scope](../../../.scratch/postgres-sql-optimization/spec.md).
Review base: `fe0dd9aadca7de4881fe3e976b2408260c4cb820`.
Branch: `codex/postgres-reading-optimization`, created in a clean worktree.
The original dirty checkout and unrelated monitoring/report changes were preserved.

## Results

Same synthetic PostgreSQL fixtures, independently scaling feeds and materials:

| HTTP path, 1000 resources | Execute before | Execute after |
|---|---:|---:|
| Navigation, feeds | 7009 | 30 |
| Subscriptions, feeds | 1007 | 20 |
| Compact cards, materials | 2021 | 27 |
| Scoped legacy cards, materials | 7015 | 28 |
| Completion preview, materials | 7010 | 20 |
| Completion command, materials | 47060 | 24110 |

Completion computes reachability once instead of 1000 times. Remaining completion
calls mainly belong to existing per-item cleanup after commit; cancellation,
file safety and recovery retain their owners. No fixed latency SLA is claimed.

The archived-job plan uses indexed UNION branches: approximately 8 ms /972 blocks
became 0.04 ms /11 blocks on 60,000 jobs and 10,000 media rows. The new reverse-link
index uses migration 004; applied migrations are unchanged.

Collection summary and selected-feed detail at 100 feeds: 308→9 execute calls each.
At 1000 feeds the bounded summary uses 23 calls; detail stays at 9 and does not
transfer foreign diagnostics. Summary reads scalar diagnostics rather than bodies.

List-version research retains the current model. At 1000 publications and six
variants for each all/feed/folder scope (18 lists), flush took 112.816 ms in one
local observation. Re-reading ordered IDs dominates hashing. No-op, A→B→A,
rollback, membership removal and restart were measured.

Detailed fixtures, results, limits and reproduction commands:

- [Reading](reading.md), [before](reading-before.json), [after](reading-after.json), [probe](reading_probe.py).
- [Collection status](collection-status.md).
- [Completion](../postgres-reading-optimization/completion.md), with its probe and raw JSON.
- [SQL plans and versions](sql-and-versions.md), with `plans.py/json` and `versions.py/json`.

Raw measurements count execute, executemany calls/rows, returned rows, approximate
decoded value bytes, response bytes and background work separately. SQL trigger
statements and exact PostgreSQL wire bytes are not counted. Reading reduces round
trips, not every byte: compact decoded bytes grew modestly on the 1000-item fixture.
Startup recovery remains unchanged: 51,108 execute and 1000 batch calls /2000 rows
for 1000 materials. It was measured separately after an exploratory run exposed
recovery overlapping reads. No personal sources or production were exercised.

## Actual checks

Full `OKO_TEST_POSTGRES_URL=<synthetic-dsn> ./scripts/check all --compact` passed
on committed implementation `1e00ac9907cdb371360e00deab54ea491c836fe9`, frozen tree
`31d6a159716ca43ce9b63062eb36aae05c4b9385`, in 332.96 s:

- Python: 1057 passed, 2 skipped (Linux bubblewrap integration requires Linux).
- Node: 113 passed.
- Playwright: 225 passed, 3 skipped. Two are pre-existing user-deferred
  closing/keepalive and coexistence/BFCache cases; the standalone PostgreSQL browser
  case needs a schema credential supplied by its Python wrapper. That wrapper ran
  successfully in Python and produced the native PostgreSQL reader screenshot.
- Contracts, tooling types, syntax, lint, Python types (155 files), browser types,
  build, HTTP, diff and entrypoint: passed.

Report ID: `20261026T124103Z-896ca11a`. Earlier focused reading check: 26 passed;
completion: 24 passed; SQL/migrations/transactions: 25 passed; collection: 7 passed;
focused Playwright: 25 passed. These are overlapping subsets, not additive coverage.

After independent review, `03bc7a9` fixes two remaining reading variants. The final
rerun covers PostgreSQL cost/SQL plans/browser, completion, versions, legacy reading,
linked publications, Telegram/Telegraph and YouTube. Final result: **79 passed**, one existing Starlette/httpx deprecation warning,
125.64 s. See [post-review test output](post-review-tests.log).
Five additional Playwright scenarios for linked RSS/Telegram/Telegraph and YouTube
ready/error captions passed in 18.33 s; tooling/browser types, Python types, lint
and syntax passed. Final code revision: `03bc7a9`.
Unchanged full-run suites were not repeated merely to increase their counts.

Screenshots were inspected: completion conflict dialog, collection details on
mobile and real PostgreSQL reader with text/media/theses. The displayed content
and controls are legible, with no new visual changes intended by this PR.

## Independent review

**Standards:** 0 required violations or substantial complexity findings in
`fe0dd9aa...1e00ac99`; incremental review `1e00ac99...03bc7a9` also found none.

**Spec:** initial review found two P2 N+1 paths: saved-summary source rows and
YouTube subtitle storage keys. Both received HTTP regressions and were fixed.
Re-review of the original reproductions: linked summaries with 1/10 sources use
20/20 SQL with no per-source reads; YouTube with 1/10 subtitle files uses 21/21 SQL
with no per-file SQL. File existence is still checked. **0 open Spec findings.**

The final PostgreSQL regression suite independently scales links to 10/100/1000,
and subtitles to 10/100/1000, including deletion of a saved subtitle file between
requests. Related rows do not imply that their jobs were loaded.

## Failures and limitations

- Expected red tests reproduced navigation217 and compact75 calls for 30 objects,
  archive Seq Scan, repeated reachability and the two review findings (30 calls for
  10 linked sources/subtitles). They passed after their corresponding changes.
- Initial types check needed an explicit `list[Row]` annotation; the focused retry
  passed. A collection test budget was corrected to account for extra 400-feed
  batches; the 1000-feed rerun passed.
- Completion corruption fixture initially crossed SQLite thread ownership; repaired
  before the successful corruption and completion runs. Four corrupted input forms
  now preserve preview/card agreement.
- Two initial assertions in the new saved-summary test used internal names instead
  of the wire DTO (`result.points[].references`); corrected before the SQL red test.
- A redundant `check python types` invocation launched Python tests and was stopped
  with exit130 while benchmarks were writing reports. It also reported changed
  report files. It is not counted as a successful run. The successful full run used
  an isolated frozen tree and was not affected.
- Timings are single local samples, sometimes under concurrent validation load;
  they do not establish p95, production capacity or fixed latency guarantees.
- This PR does not implement #157 refresh, pool/async, persistent snippets or list
  eviction. Merge and deployment were not performed.
