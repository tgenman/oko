# Cloud installation source verification — 2026-10-25

This is source/preflight evidence for #129, not a completed production acceptance.
Base: `0a96e2830e92caeff8a6c613f8a647f704ceb0d5`.

## Checks executed

The full isolated source snapshot `3b0c0974ab9a1ded0a9590832ea4b460185060ef`
ran `scripts/check all --compact --in-place` with the temporary synthetic
PostgreSQL instance. Result: **955 Python passed, 2 failed, 2 Linux-only skips**;
**279 tooling**, **113 Node**, **225 Playwright passed / 3 skipped**. Contracts,
syntax, lint, all type checks, frontend build, HTTP delivery, entrypoint and diff
checks passed. The two failures were persistent-reader test clients using
Starlette's default non-IP peer for service-bearer probes; the new peer check
correctly rejected them. Those probes now explicitly use a loopback peer.

After that correction, the persistent-reader, native-TLS and password-login
files passed **16 tests**. Native TLS exercises the real CLI, verified local
certificate, login, Secure cookies, public bearer rejection, wrong Origin,
untrusted CA and wrong TLS hostname. Invalid TLS arguments fail before data
creation. The PostgreSQL CLI file passed **4 tests**, including delayed database
connection readiness and a bounded non-disclosing startup failure.

The subsequent certificate receipt/start recovery fixes and fail-closed registry
metadata check passed **17 operator/release tests**. A subprocess proves mutual
exclusion and lock release after failure. Certificate tests prove a rejected key
cannot replace the current pair, failed readiness rolls back, a pointer without
an applied receipt is retried, and ordinary start cannot acknowledge an unloaded
certificate. Existing database credentials are never regenerated when absent.
Strict mypy passed for the changed application modules.

The final isolated affected-suite run passed **37 tests** after building the
frontend in that new checkout. Its initial attempt had 12 missing-frontend
failures; source was unchanged between that attempt and the prepared rerun.
Docker Desktop readiness now runs service control inside the reader container,
so its bridge forwarding does not conflict with the loopback peer requirement.
The Docker release file passed **30 tests** with that compatibility correction,
including per-exec subprocess timeouts. The same stdlib-only probe passed in an
isolated container from the previous Mac image (network disabled, read-only root,
synthetic tmpfs data), proving old images need no new control module. That test
container was removed; the actual Mac installation was not restarted.

An independent reviewer inspected the external-state paths. Their first
combined TLS/operator/readiness run had 14 passes and one HTTP ReadTimeout;
the unchanged PostgreSQL CLI file then passed all 4 cases. This is recorded as
a limited rerun, not an all-green initial review run.

Both production Compose files were accepted by Docker 29.1.3 / Compose 2.40.3
on the new VM, using `config --quiet` without starting services. The official
PostgreSQL 18 Debian image was pulled there and its immutable digest and
postgres UID/GID 999 checked. Desktop/mobile login screenshots from the full
Playwright run were visually inspected.

## Registry observation and limit

The checked main release's first publication built and exercised the Linux
container, then pushed it. Its concluding package-visibility check failed.
An independent anonymous manifest request returned 200, proving the existing
`ghcr.io/tgenman/oko` package is public. No credentials are part of the source
allowlist or image. The release is not accepted as the planned private package.
The publisher now refuses unknown package metadata before any push; an API 404
does not prove absence for a repository-scoped token. Package privacy/association
and server read credentials still require authenticated GitHub administration.

## Production work still pending

No reader, PostgreSQL production database, DNS record, ACME certificate or
monitoring stack was installed by these checks. The host helper remains the
separately verified preflight installation from #128. The Cloudflare DNS-only
token for this domain is prepared but not created, pending user confirmation.
Actual install, reboot persistence, certificate staging/production/renewal and
Playwright acceptance at `https://oko.observer` remain required before closing
#129. Personal sources are absent and the Mac/VPN installations are untouched.
