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
