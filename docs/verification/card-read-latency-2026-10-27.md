# Card read cost, 27 October 2026

## Scope and implementation

The compact card projection reads borrowed saved publication facts instead of
constructing an opened material through `Store.original`. It copies only the
attachment list and media URL map that linked publications extend. Description
previews do not expand full text or load subtitle bytes when those bytes cannot
affect the title. Filtering, processing eligibility, revision identity and
loading semantics remain unchanged. PostgreSQL input batches bind arrays with
`ANY` instead of generating hundreds of placeholders.

The HTTP contract, database schema and frontend behavior are unchanged. Full
content and linked publication rows are still loaded for revision and processing
validity. This change does **not** establish the target of less than one second.

## Measurements, not a latency guarantee

The checked working release was `d010c7b98970024cb288a01fa98e52361cf67816`.
The reader container has a one-CPU quota. Background collection was active.

A fresh Playwright run measured login/first cards at 13.113 s, first folder at
19.495 s, and subsequent folders at 2.339 / 1.337 / 1.309 s. This is one sequence,
not p95. Artifact: `/tmp/oko-architecture-baseline-latency.json`.

A separate process inside the working container used a read-only PostgreSQL
connection, no Store recovery and no workers. cProfile on 1,177 publications
measured 0.950 s preparing inputs and 5.742 s projecting cards; SQL execute calls
accounted for 0.537 s. `Store.original` accounted for approximately 3.2 s,
including approximately 2.6 s in deep copies. Profiling adds overhead; these
numbers must not be compared directly with browser request durations.

A subsequent old/new/new/old comparison used one repeatable-read snapshot,
separate request-local caches for each run, and no profiler. All 1,161 cards
matched field for field:

| Path | Input loading | Projection | Total |
| --- | ---: | ---: | ---: |
| Old | 0.590 s | 1.991 s | 2.581 s |
| New | 0.598 s | 1.230 s | 1.828 s |
| New | 0.528 s | 1.444 s | 1.971 s |
| Old | 0.628 s | 2.415 s | 3.043 s |

Artifact: `/tmp/oko-live-card-compare-final.jsonl`. Candidate code ran only in
that separate process. This comparison excludes HTTP serialization, admission
wait, browser rendering and network. It is not a deployed browser result.

## Verification

- Regression first failed because the card HTTP request called `Store.original`.
- Clean task snapshot: 61 targeted Python HTTP/storage tests passed, plus types
  and contract checks. Covered card cost, linked publications, material reads,
  PostgreSQL batch cost and resource versions.
- Clean task snapshot: 12 Playwright scenarios passed, including real synthetic
  HTTP reading, folder/feed navigation, previews, YouTube and late responses.
  The first browser attempt failed before behavior checks because the clean
  snapshot lacked built assets; after `npm run build`, all selected checks passed.
- Staged checks passed.
- Independent review caught a proposed streaming HTML optimization changing
  malformed-comment behavior and making sparse text quadratic. That optimization
  was removed; the existing snippet parser is unchanged.

Reports: `/tmp/oko-card-task-check/.scratch/check-runs/20261027T132533Z-3696a48c/report.json`
and `/tmp/oko-card-task-check/.scratch/check-runs/20261027T132627Z-19a53406/report.json`.

## Additional pre-release checks

Three completed browser sequences on the unchanged working release measured
login at 6.962–7.937 s, first folder at 4.423–5.442 s, later folder transitions
at 0.237–0.832 s, and two material openings per sequence at 0.247–0.802 s.
Artifacts: `/tmp/oko-card-expanded-before-final.json` (first sequence completed;
its cleanup wait incorrectly expected a URL fragment) and
`/tmp/oko-card-expanded-before-series.json` (two complete sequences).

A separate loopback HTTP run measured all resources at 3.720 s / 2,052,820 bytes,
a folder reusing known cards at 0.249 s, and opening a material at 0.216 s.
Artifact: `/tmp/oko-card-loopback-before.jsonl`.

PR CI run 40512343376 passed all functional groups but failed one latency case:
400 materials, idle, first opening p95 287.1 ms against the existing 200 ms
threshold. The same four-case latency suite on the integrated change passed
locally: for 400 materials, first/repeat p95 65.1/53.0 ms idle and 61.0/146.4 ms
with processing active. This does not erase the CI failure or prove its cause.
No thresholds changed. Local report:
`/tmp/oko-card-integration/.scratch/check-runs/20261027T133350Z-98a8cd2b/report.json`.

The measurement harness also reached the existing 16-session login limit.
One session created by this task was revoked; other sessions were preserved.
The harness now waits for completed logout before closing the browser.
