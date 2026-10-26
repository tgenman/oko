# PostgreSQL reading: independent scaling measurements

The same `reading_probe.py` ran against the original checkout and the implementation
worktree with independent sizes 10, 100 and 1000. The original checkout HEAD is
`0a89ae4bb3fa4a6ff7e63e5016c7c50c35f01f8e`; its product/test source tree was verified
equal to the pinned integration base `fe0dd9aadca7de4881fe3e976b2408260c4cb820`.
The after result measures the working implementation, before its final commit.

Each case uses a new isolated PostgreSQL schema, real migrations, synthetic material
and feeds, disabled work, and temporary files. Feed scaling retains one material;
material scaling retains one feed. No personal sources or model are contacted.

Request execute counts (before → after):

| Axis / size | Subscriptions | Navigation | Compact cards | Unchanged | Scoped cards | Opened |
|---|---:|---:|---:|---:|---:|---:|
| feeds / 10 | 17 → 8 | 77 → 14 | 17 → 17 | 2 → 2 | 22 → 20 | 7 → 7 |
| feeds / 100 | 107 → 8 | 707 → 14 | 17 → 17 | 2 → 2 | 22 → 20 | 7 → 7 |
| feeds / 1000 | 1007 → 20 | 7009 → 30 | 17 → 17 | 2 → 2 | 22 → 20 | 7 → 7 |
| materials / 10 | 8 → 8 | 14 → 14 | 35 → 17 | 2 → 2 | 85 → 20 | 7 → 7 |
| materials / 100 | 8 → 8 | 14 → 14 | 215 → 17 | 2 → 2 | 715 → 20 | 7 → 7 |
| materials / 1000 | 8 → 8 | 14 → 14 | 2021 → 27 | 4 → 4 | 7015 → 28 | 7 → 7 |

The count grows with bounded batches, not one round trip per feed or card. All measured
HTTP requests use zero `executemany` calls and zero batch rows; these counters are
recorded independently rather than treating a batch as one statement. SQL trigger
statements are not included. Unchanged and opened paths retain their existing budgets.

The 1000-object samples also record rows, approximate decoded value bytes and full
response bytes:

| Scenario | Milliseconds before → after | Returned rows before → after | Decoded value bytes before → after | Response bytes before → after |
|---|---:|---:|---:|---:|
| feeds / subscriptions | 607.388 → 323.942 | 1001 → 1001 | 120790 → 120790 | 287951 → 287951 |
| feeds / navigation | 2586.153 → 239.801 | 3005 → 3005 | 240778 → 241782 | 456096 → 456096 |
| materials / compact | 4127.281 → 471.885 | 6004 → 6004 | 1022001 → 1056005 | 590274 → 590274 |
| materials / scoped_cards | 11944.341 → 309.388 | 4003 → 4004 | 1583020 → 1651128 | 875005 → 875005 |
| materials / opened | 61.609 → 12.071 | 9 → 9 | 1332 → 1370 | 1622 → 1622 |

These are single local latency observations with instrumentation and other validation
processes running, not percentiles or an SLA. Decoded value bytes sum UTF-8 lengths of
non-null decoded database values; they are not exact PostgreSQL protocol traffic.
Returned rows use cursor row counts; decoded rows additionally track consumed rows.
The raw JSON preserves all dimensions for every request.

## Startup and background work

The first exploratory run exposed recovery overlapping requests despite disabled work.
It was discarded as a steady-state comparison. The saved probe waits for the existing
recovery flag and records startup/recovery separately; it does not suppress that work.
For 10/100/1000 materials startup/recovery performs 618/5208/51108 execute calls in the
before version. The after version's raw results retain this cost separately. This
scope does not claim to improve startup recovery. Occasional housekeeping queries
remain during requests or the explicit 150 ms idle observation; their counters and
batch rows are separate from request work. Reading savings are not moved into a new
background mechanism.

## Reproduction

```sh
OKO_TEST_POSTGRES_URL='<disposable synthetic PostgreSQL DSN>' \
  .venv/bin/python docs/verification/postgres-sql-optimization/reading_probe.py \
  --repo /path/to/checkout --output /tmp/reading.json
```

The command creates and drops random private schemas. Both saved runs completed all
six cases and all HTTP responses were 200. These measurements supplement the HTTP
regression tests; they do not replace final checks of the committed tree.
