# Synthetic direct-Store startup query cost — 2026-10-25

The same immutable synthetic SQLite fixture was imported into separate temporary
PostgreSQL schemas, then opened using the default synchronous `Store` constructor.
The fixture contains one RSS feed, its publication and linked article (two
publications), one image, and completed filtering/theses. Setup/import time is
excluded. No personal library, working installation or production server was used.

| Observed operation | Baseline | Current working tree |
| --- | ---: | ---: |
| Adapter `execute` calls | 245 | 87 |
| SELECT / INSERT / UPDATE / DELETE | 202 / 17 / 13 / 13 | 48 / 17 / 9 / 13 |
| All settings SELECTs | 65 | 1 |
| Repeated scope/owner settings SELECTs | 63 | 0 |
| Item-by-ID SELECTs | 44 | 4 |
| Feed/binding-by-ID SELECTs | 21 | 0 |
| Observed `set_config` SQL calls | 247 | 11 |
| Observed low-level `Connection.execute` calls | 511 | 117 |
| Adapter `executemany` calls / rows | 2 / 4 | 2 / 4 |
| Commit-method calls | 13 | 12 |
| Constructor elapsed time, sample 1 | 0.213706 s | 0.072160 s |
| Constructor elapsed time, sample 2 | 0.233010 s | 0.072078 s |

The baseline is the immutable checkout at `fd740dbf6d5aa2bd85092e5827321a9716f9ece9`.
The current measurement uses the uncommitted working tree based on
`1afd6508d1481e2277c858c2215f56e3f634ef6c`; the JSON records include SHA-256 of the
relevant source files. Those files did not change during either measurement and
their hashes match between the two current samples. Both versions used the exact
same source database hash, and each verified that the source remained unchanged.

The clock count is measured at the real psycopg connection boundary, rather than
inferred by multiplying repository calls. The profiler delegates every statement
to a real PostgreSQL connection. Its wrappers only count SQL templates and calls;
they do not replace responses or record bound values. `Connection.execute` counts
are driver calls, not TCP packets or network round trips. Cursor `executemany`
statements are recorded separately, and commit-method calls are not asserted to
equal durable commits.

These small local samples demonstrate eliminated repeated work. They do not
establish production startup latency or the asynchronous HTTP readiness result;
those are separate acceptance measurements.

## Reproduction

The original 245-call investigation was an inline shell/Python invocation, not a
saved script. [`profile_store_open.py`](profile_store_open.py) is its reproducible
extension with actual low-level clock-call counting.

Set `OKO_TEST_POSTGRES_URL` to the explicitly isolated synthetic PostgreSQL.
Run the baseline first with a new fixture path; then run current code using that
same path. Existing directories without the exact synthetic marker are refused.
Use absolute paths for the output files because the script enters the checkout.

```sh
.venv/bin/python docs/verification/async-start-settings-2026-10-25/profile_store_open.py \
  --checkout /tmp/oko-completion-final-check \
  --source /tmp/oko-startup-cost-fixture-20261025 \
  --output /absolute/output/baseline-store-open.json

.venv/bin/python docs/verification/async-start-settings-2026-10-25/profile_store_open.py \
  --checkout /absolute/current/checkout \
  --source /tmp/oko-startup-cost-fixture-20261025 \
  --output /absolute/output/current-store-open.json
```

Evidence: [baseline](baseline-store-open.json), [current](current-store-open.json),
and the preserved first samples [before](baseline-first-sample.json) /
[after](current-first-sample.json).
