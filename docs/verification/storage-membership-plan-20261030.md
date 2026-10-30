# Transitional membership flag: synthetic query measurements

30 October 2026. This is an experiment, not a migration or proof that the flag
can be removed safely. The reproducible script is
[storage-membership-plan-20261030.py](storage-membership-plan-20261030.py).
It creates an isolated synthetic schema: the selected feed has 10,000 viewed
publications and one unviewed publication with a pending fetch; a different
feed has 10,000 unviewed publications. PostgreSQL statistics are collected.
The task is captured and rolled back; no real library is involved.

For the actual `next_job` call, read-only EXPLAIN instrumentation counted
40,011 scanned relation rows and three SELECTs both with the existing predicate
and when the two `owner.is_unviewed` predicates were removed from the generated
query. This includes other reads made by the call; the compatibility views and
physical column still existed. It is not a measurement of a migrated schema.

For a separate simplified membership query on the same dataset, the sum of
actual rows across all plan nodes was 10,004 with the predicate, 30,004 without
it, and 40,005 with an explicitly materialized unviewed-first CTE. Corresponding
EXPLAIN execution times were 2.681, 3.523 and 4.101 ms. These node totals differ
from the relation-scan metric above and must not be compared directly. One run
under concurrent test load is not a latency benchmark.

Further work must inspect the complete dispatch and reading plans with realistic
feed distributions, test empty and heavily active feeds, verify pagination and
concurrent viewing, and validate a migration including view/index changes before
removing the flag. The experiment did not change application code or schema.

## Candidate: start from pending fetch Jobs

A later experiment in the same script compares a **fetch-only candidate** using
correlated membership checks and no `is_unviewed`. With one pending fetch, the
candidate scanned three relation rows (0.062 ms); with `--foreign-jobs`, which
adds 10,000 earlier pending fetches owned by another feed, it scanned 20,002
rows (36.406 ms). The corresponding current full dispatch calls scanned 40,011
and 60,011 relation rows across three SELECTs. These totals are useful evidence
of different work shapes, not an equivalent latency comparison: the candidate
is one SELECT and does not include all dispatch kinds, priority, locking and
claim behavior of `next_job`. Both runs used synthetic data and EXPLAIN ANALYZE;
they do not prove a production improvement or justify migration yet.

## Attempted change to full dispatch

A subsequent implementation of the pending-first idea changed the complete
`next_job` query and was tested with 10,000 viewed memberships in the selected
feed, 10,000 earlier pending fetch Jobs in a different feed, and one eligible
fetch. It returned the correct Job in this scenario. The old query's EXPLAIN
instrumentation reported 50,011 scanned relation rows and 643 shared buffer
accesses; a correlated form reported 30,010 rows but 20,282 buffer accesses.
These are single synthetic plan measurements, not timing or complete semantic
proof. The change was rejected on this evidence and reverted; its patch and
regression scenario remain in `.scratch/storage-redesign/` as a local draft.
Any replacement must address both workload shapes and verify all dispatch kinds,
linked ownership, history waits, priority, concurrent claims and cancellation.
