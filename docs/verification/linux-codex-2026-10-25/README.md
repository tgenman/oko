# Linux Codex and release verification — 2026-10-25

Scope: [#128](https://github.com/tgenman/oko/issues/128). Tests used the new,
dedicated Ubuntu 24.04 x86_64 VM. No existing VPN or Mac working release changed.
The installed helper is a private preflight generation, not the public Oko site.

## Actual Linux evidence

- Official Codex full package 0.164.0 verified against its published archive
  digest; CLI and code-mode-host binary hashes match the checked-in pin.
- Distribution bubblewrap and its AppArmor profile installed; global
  `apparmor_restrict_unprivileged_userns` remains 1.
- Real Linux synthetic tests cover private UDS authentication, hidden outside
  filesystem data, permitted auth refresh, stripped API keys and cancellation
  with confirmed disappearance of the owned process group. Kernel/init reaping
  is asynchronous, so disappearance is checked with a bounded two-second wait.
- Dedicated systemd synthetic unit: killing helper parent, killing supervisor,
  and cancelling then killing supervisor all remove the old generation and
  produce a healthy new generation. The test unit is stopped/removed afterwards.
- A real non-root Docker container (UID 10001, supplementary IPC GID 20001)
  connects through the read-only socket-directory mount with networking disabled.
  It receives authenticated health data and has no Codex OAuth file mounted.
- ChatGPT-only login cache was securely initialized outside the image. Quota and
  catalogue checks succeeded. Bounded synthetic `gpt-6-luna` completions returned
  the requested JSON through the actual systemd helper and Unix socket. No API
  key or personal source/material was used; these subscription smokes are
  separate from automated tests.

## Review corrections

Independent review found and corrected:

1. Unexpected supervisor loss after cancellation was hidden by a zeroed lease.
   Unexpected exit/EOF now restarts a systemd generation regardless of lease;
   an uncertain stop with a retained marker also requires restart.
2. Registry privacy was checked after push. It is now checked beforehand and
   read back afterwards.
3. Unknown tag inspection errors could mean "absent". Only confirmed absence
   permits a new publish; authentication/network/registry errors fail closed.
4. Binding the whole auth directory exposed ambient settings. The CLI now sees
   only a credential view, with crash-safe refresh recovery and concurrent-login
   protection. Synthetic process-exit tests verify recovery of a written refresh.

Local targeted checks passed: 53 Python cases (2 Linux-only skips on Mac),
67 publication/Docker/CI tooling cases, strict mypy and contracts. Linux-only
filesystem/process cases were separately executed on the actual Ubuntu host. Initial sandboxed
attempts at socket tests were blocked by local OS restrictions; permitted
repeats passed. One initial Linux cancellation assertion raced kernel reaping;
its bounded exit-confirmation repeat passed. The first cancel/systemd test used
a synchronous cancellation call that waited for cleanup; the corrected test
issues cancellation concurrently to reach the supervisor-loss window.

## Publication gate

The workflow builds only successfully checked main commits, verifies a running
network-isolated image, publishes an immutable SHA tag and records its OCI digest
and matched helper/protocol/CLI/schema metadata. No production deployment or
production secrets are in this workflow. Actual GHCR publication/privacy and
final project/CI results are verified after the implementation PR is merged.

## Final isolated project run

Prepared tree `25ddde3e521e92fb9962bddf8e1b195d481723f2`, based on
`031d28d37bf33f647d24d3219fbdd08db3cbbab0`:

- Full project run passed every group: contracts, tooling/types, syntax, lint,
  build, HTTP, entrypoint and diff.
- Python: 949 passed, 2 Linux-only cases skipped on Mac. Tools: 273 passed.
- Node: 113 passed. Playwright: 225 passed, 3 skipped.
- Linux filesystem/namespace checks and synthetic systemd crash acceptance were
  separately executed on Ubuntu, rather than inferred from the Mac run.
- Independent follow-up review accepted registry fail-closed checks, cancellation
  recovery and persisted credential refresh recovery. Four focused recovery cases
  passed independently.

GHCR publication remains a separate external readback after merge.
