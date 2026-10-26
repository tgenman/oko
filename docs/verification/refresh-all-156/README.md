# Manual refresh SQL amplification — #156

Review base: `fe0dd9aadca7de4881fe3e976b2408260c4cb820`.
Issue: https://github.com/tgenman/oko/issues/156.

## Reproduced cause

The actual HTTP `POST /api/v3/collect` on isolated PostgreSQL schemas reprojected every saved material through Store.material(), repeatedly loading original rows, linked publications, settings, processing output and initial-history status. Media retry also visited publications whose files were already ready. Synchronous source configuration and the complete target loop ran before HTTP could regain control.

The regression is reproducible without a competing SQL transaction: many short synchronous queries accumulate and block the event loop. This establishes an application-side database workload problem; it does not establish that PostgreSQL locks were absent during the historical production incident. No index/schema change was needed for this fix.

## Same-fixture before/after

Four synthetic feeds; fully saved text and actual PNG files, ready media and saved done fetch jobs. Identical fixture factory and PostgreSQL on localhost. The baseline subprocess loaded the four original product files from the pinned base; a finally block restored the exact fixed bytes. No production data was used.

| Materials | Before request execute calls | After request execute calls | Before seconds | After seconds |
| --- | ---: | ---: | ---: | ---: |
| 16 | 966 | 101 | 0.462 | 0.118 |
| 128 | 6342 | 101 | 3.188 | 0.120 |

`before.json` and `after.json` separately record background execute calls and cursor.executemany batches/rows. These are driver-boundary counts, not a claim that one batch is one PostgreSQL statement. During the measured POST window background execute calls were 47 before / 133 after; batch calls/rows were 2/4 before and 8/18 after. Total observed work still decreases substantially, and the request no longer grows per saved material. Largest observed individual execute was under 8 ms; timings depend on machine load.

## Change

- Queue admission uses saved collection completeness, membership, deletion and source-manifest facts. It does not construct filters/theses/reader loading state. Linked publication and YouTube completeness keep their existing original-header calculation.
- Media retry selects eligible non-ready resources and preserves source waits, backoff and running attempts. Ready files are not revalidated by ordinary refresh.
- Initial histories resume in one committed target batch, preserving cancelled histories and done-state pause semantics. This avoids a full source-configuration rebuild after each yielded feed.
- Adapter configuration stays with the existing collection worker. The HTTP command yields after target preparation and between feeds; each step rechecks work, Store ownership and deletion. No SQL transaction crosses an await. Existing stop errors and command receipts represent partial admission honestly.
- No public HTTP contract, schema, model selection, startup/request timeout or personal source configuration changed.

## Checks

- RED: ready-library HTTP cost failed at 966 execute calls for 16 materials. A baseline refresh/stop test failed because reading waited for the entire manual refresh. The controlled 2-ms PostgreSQL round-trip test initially measured a 692-ms event-loop pause.
- GREEN: 62 targeted cases passed, covering the real PostgreSQL SQL budget (16/128 materials), controlled-latency event-loop budget under 150 ms, actual HTTP reading/settings/power during refresh, cancellation receipts, existing refresh outcomes, history pause/RSS, linked articles, independent media and native PostgreSQL Playwright.
- The real browser used the Refresh Sources button, retained the opened article and rendered its image. Synthetic screenshots are stored with the test artifacts and inspected separately.
- Source mypy passed for 153 files. Frontend build passed. Initial staged TypeScript check found an unguarded naturalWidth access; it was changed to an HTMLImageElement guard and the affected checks are repeated below.

Earlier attempts are not counted as passing: missing frontend assets caused one broad run to stop after 15 passes; building the assets resolved that environment failure. Additional meaningful RED runs isolated source sync before the first yield and packet preparation combined with the first feed. A PostgreSQL text/integer COALESCE error in an intermediate batch implementation was corrected before verification. An overlong concurrency test issued power only after an independent settings write and refresh had finished; the final test sends power while refresh is still pending.

## Limits and release status

This is a local synthetic result. Per-feed admission remains synchronous; the test does not promise a universal latency bound for an arbitrarily large single feed or an unlimited number of failures. Full capacity/reboot/outage acceptance remains #133. The historical production 30-second timeout is not rerun merely for diagnosis. Working cloud collection remains enabled on the previous release until a separately checked release is installed. Migration data is not rolled back.
