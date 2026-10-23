# Large archive preparation - 2026-10-22

Request: apply the measured large-archive fixture optimization and measure the complete run. Working Oko settings, data, release and startup were not changed. No commit, publication or merge was requested or performed in this task.

## Implementation and coverage

The history-dispatch test now builds its immutable base once using the existing closed LibraryTemplates: 10,000 viewed archive items and 10,000 completed fetch jobs. Each of the four count100/10000 x ANALYZE false/true measurements receives independent SQLite/files and a new Store. The builder closes its Store before publication. Each private scenario adds its own history_members and live job, applies ANALYZE as before, and verifies original cardinalities before starting measurement. Real next_job(0), SQLite VM counting and the +200 threshold are unchanged.

Full-run archive case totals: **9.207s before -> 1.784s after** (False:4.947 ->1.547; True:4.260 ->0.237). These are case wall times in their respective full runs, not the elapsed time of the whole runner. The isolated affected history/templates suite passed22 tests in8.66s. No test IDs, dataset sizes, skips or acceptance thresholds were removed.

Two pre-existing full-run blockers were corrected at their actual seams:

- Reply-preview equality rejected ordinary GET /collection-status as content rereading. Only exact GET /status and /collection-status are now allowed as background, alongside the prior reading/check and presence heartbeat. The test explicitly performs both GETs. All other requests remain under equality and all new material/filter/theses/media reading/check requests must keep idA and delivered known_version. Targeted browser regression PASS4.23s.
- The owned-caption diagnostics test waited without renewing its six-second reader lease. At failure, diagnostics showed0 active sessions, work_allowed=false and client_presence_required. It now uses the existing wait(...,keep_alive=True), as a neighbouring caption case already does. The production lease,200 iterations, sleeps and original exception/count/traceback/redaction assertions are unchanged. Targeted case PASS3.52s.

Independent review covered archive ownership/data/measurement and both narrow blocker fixes; no remaining actionable findings. Reviewers did not run competing benchmarks.

## Complete runs

All runs used ./scripts/check all tools --compact, four Python workers, two tools workers and eight browser workers, sequentially without other local tests or benchmarks launched by this executor. The fresh baseline has the same source tree as main3c113fa854a4aae6f36c2787ffe25572552faf32. Prepared task snapshots excluded old untracked reports and raw evidence.

| Attempt | Total | Python | Browser | Tools | Result |
|---|---:|---:|---:|---:|---|
| Fresh baseline |190.47s|166.64s|142.65s|137.57s|FAIL: Codex unconfirmed group-stop PID wait|
| Archive optimization |179.75s|159.58s|103.58s|129.85s|FAIL: reply-preview background collection-status GET|
| Archive + poll guard |165.08s|144.56s|121.69s|115.11s|FAIL: caption worker lost reader lease|
| Final, both blockers corrected |**171.94s**|**149.82s**|**141.66s**|**135.79s**|**PASS**|

Final snapshot: dfdcb97fa8210ec20fb1fb4e9b786158dc1014f2, base3c113fa854a4aae6f36c2787ffe25572552faf32. 772 Python,9 HTTP,162 tools,323 functional browser/Node and4 performance passed; two original user skips retained. Browser functional104.78s + performance36.85s. Python workers149.80/111.80/129.74/144.86s; tools69.79/135.77s. Parallel suite times must not be added.

The first archive attempt did not execute performance because its functional browser command failed. The baseline, poll-guard attempt and final run did execute all320 latency clicks. Final p95 first/repeat:104 active17.9/24.5ms,104 idle19.5/18.5ms,400 active28.4/26.8ms,400 idle28.0/27.1ms. The existing p95<200ms threshold and normal Playwright clicks were retained. The400-active screenshot was inspected; list and opened material render normally.

The final whole-run result is about2 seconds below the previous complete PASS173.90s. The fresh baseline failed and the individual runs vary substantially, so a reliable percentage gain of the full runner is not established. The fixture-specific reduction is clear; the full-run critical path remains Python with a substantial worker tail.

Final source report: /tmp/oko-archive-full-complete/20261022T203321Z-f8377afe/report.json. All failed complete attempts, JUnit, full logs and latency JSON/PNG are retained in .scratch/large-archives/evidence/ outside the index. The final staged check passed; subsequent changes are this report only.
