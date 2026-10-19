# Isolated Oko Docker runtime

User authorized implementation with implement-spec on 2026-10-19.
Canonical agreed specification: [Docker runtime](../../docs/architecture/docker-runtime.md).
Selected deployment: this Mac, separate native Codex process, http://localhost:8765; user deferred oko hostname and port 80 during implementation.

Graph: 01 and 02 independent; 03 integrates 01/02; 04 depends on 01/02/03.
Test seams: helper HTTP, reader HTTP, Playwright, release CLI. Use temporary stores and synthetic external adapters.
