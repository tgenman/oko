# #56 implementation decisions

Approved input: reading-updates-design.md, original #56. New opened-material behavior
is separate. Implement additive /api/v3/reading/check and /reading/materials/{item}.
Old DTOs remain. Canonical schemas/examples generated successfully.

Each entity has a persisted version row; write-side SQLite triggers increment it
with the changed inputs and roll back together. No global DB version, cursor,
event journal or runtime revision counter. Lists use scope/sort/hidden key and a
digest of ordered service IDs, refreshed on writes; known versions avoid reading
IDs/cards on unchanged checks. Navigation uses content-free feed/folder counts.
Compact cards have one preview URL and processing states; no full theses/filter
results. Material entity omits duplicated feed_title/folder_id/importance/snippet.
Feed entities provide linked metadata. Folder entities are inline with own version.

Wire versions are decimal strings to avoid JS integer loss. Root storage UUID
persists ordinary restart. Different UUID or known version ahead of current
produces reset. Backup restore must rotate this identity as an explicit maintenance
step (cannot perfectly infer a rolled-back full backup from its own data).

Runtime admits one synchronous frame under existing action lock. Status reconciles
problems outside reading_snapshot; remaining projections share read snapshot with
no await/write inside. Stable per-frame time and persisted due flags publish time
transitions; expired problems are pruned by indexed owner transitions before compare.
Old v3 reads retain existing time behavior. Final stopped frame uses the persisted
status version captured before Store closes, not an invented memory counter.

Backend slice currently6HTTP tests PASS: unchanged no data/no settings/fullitem
reads; material restart/viewed; noop/otherfolder; +1feed/SQLrollback; duefetchloading;
selectivelistchange/storage reset. New server files reader_versions.py,versions_v3.py.
Generated models/SDK produced. Frontend not yet switched. No commits or PR for56 yet.
Need additional status/time/expiry, feed/profile/cursor dependency tests, frontend
cache/1sec coalescing/admission, synthetic transport adaptation, real Playwright
request/latency/bytes tests, fullverification/review/CI/merge then57audit.
