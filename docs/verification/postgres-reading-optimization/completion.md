# Completion optimization verification — 2026-10-26

Base: `fe0dd9aadca7de4881fe3e976b2408260c4cb820`. Baseline imported the unchanged product tree in the primary checkout; after used the integration worktree. Synthetic PostgreSQL, isolated schema per size, one demo feed, saved native publications, collection disabled. No personal data.

## Observed HTTP costs

| Publications | Preview execute before → after | Completion execute before → after | Reachability before → after | Preview returned SQL bytes before → after | Completion returned SQL bytes before → after |
|---:|---:|---:|---:|---:|---:|
| 10 | 80 → 8 | 526 → 308 | 10 → 1 | 16493 → 9695 | 18961 → 12743 |
| 100 | 710 → 8 | 4756 → 2468 | 100 → 1 | 165195 → 96367 | 188715 → 125785 |
| 1000 | 7010 → 20 | 47060 → 24110 | 1000 → 1 | 1650897 → 965177 | 1888651 → 1261968 |

Preview performs 8 execute calls for 10/100 items and 20 for 1000 (bounded chunks). Completion still performs per-publication durable cleanup jobs after commit: the 1000-item run has 2004 transaction starts, preserving cancellation, recovery and open-file semantics. These remaining calls were not moved to an unmeasured background task. The graph traversal and prerequisite membership/content writes are batched; this is not a constant-cost physical deletion operation.

Batch calls are measured separately. The baseline completion invokes 10/100/1000 empty executemany calls for Telegram eligibility; after invokes one empty call per selected feed. Batch row count is zero on this non-Telegram fixture. SQL result bytes sum libpq returned field bytes, excluding protocol overhead and database-internal trigger work. Rows include returned/affected rows; these are not a database I/O or trigger-operation counter. Additional background calls over the preview/completion interval are recorded separately in JSON.

Wall times are recorded, but concurrent test/benchmark processes ran during these measurements. They must not be interpreted as a latency SLA or a clean speedup factor. The first uncorrected observation mislabeled empty executemany as execute; it was discarded and both published runs use explicit batch markers.

## Checks

- Red: 10 graph traversals instead of 1; then green after batched membership deletion.
- Red: preview 89 execute calls versus budget 40 for 10 items; then green after shared compact eligibility and reader batches.
- Red: completion 390 execute calls versus budget 350 for 10 items; then green after batched unreachable-content clearing.
- Latest focused run: 24 passed — completion query cost, folder completion, shared publications and action transactions (9.25 s).
- Earlier expanded run: 41 passed and one failure in a new SQLite fixture because the test mutated the connection from the TestClient caller thread. The fixture now executes on the TestClient portal thread. Four corruption variants passed after correction: missing content, incomplete attachment, invalid media ID JSON, invalid processing metadata JSON. Each checks cards/preview agreement, retained pending members and successful completion.
- Prior green suites include feed completion, Telegram read sync and PostgreSQL transactions; integration verification remains owned by the final PR check run.
- `./scripts/check python types` passed contracts and types (155 files), but also started the Python suite. That duplicate run was explicitly interrupted (130) to avoid competing with the integration frozen all run. Its checkout-change check reported concurrently written verification artifacts. It is not a successful full-suite result.

## Reproduce

Run the same script with `PYTHONPATH` pointing to the desired checkout and `--dsn` pointing to a disposable synthetic PostgreSQL:

```sh
PYTHONPATH=/path/to/checkout /path/to/venv/bin/python completion_probe.py --dsn "$SYNTHETIC_POSTGRES_URL" --output completion-result.json
```

The script creates and drops a fresh schema for every size and checks successful preview counts, accepted completion and an empty resulting card list. Existing HTTP tests cover shared dependencies/cycles, rollback, delayed collection, repeat tokens, changed composition, recovery and Telegram read frontier.
