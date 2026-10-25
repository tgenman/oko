# Linux release and private Codex helper

The cloud reader image targets `linux/amd64`; the local Mac installation keeps
its existing Compose/release path. PostgreSQL configuration is documented in
[PostgreSQL storage](postgresql.md).

## Publish a checked image

`Publish Linux release` starts only after `Checks` succeeds for a push to the
same repository's `main`. PRs and fork workflows cannot publish or obtain
production credentials. The job uses its temporary `GITHUB_TOKEN` for GHCR.

The workflow checks existing package privacy before reuse/publication, refuses
unknown registry/auth/network outcomes, and reuses existing SHA tags by digest.
A new image is built for AMD64 and tested in a running container with no network,
a read-only root, dropped capabilities and temporary synthetic data before push.
A second privacy/digest readback produces the uploaded release manifest.

The package must already be private and associated with this repository. A
package metadata 404 is ambiguous for a repository-scoped token; it does not
authorize bootstrap or replacing a possibly existing SHA tag. Publication stops
until an administrator has provisioned/associated the private package.

The manifest records the Git SHA, OCI digest, architecture, helper release and
protocol, supported schema range and the exact public Codex package/components.
There is no production deployment in this workflow; deployment is a separate
operation. Tags are commit SHAs; deployment must use the recorded digest.

## Host runtime

Codex CLI stays on the Linux host under the dedicated `okocodex` system user.
`deployment/install-codex-linux.py` verifies the full official 0.164.0 package and
its CLI/code-mode-host hashes before installing an immutable version directory.
Its pin is `oko/processing/codex_runtime.json`. Use the distribution `bubblewrap`
package and the Ubuntu AppArmor profile; retain the global user-namespace
restriction. See [official sandbox guidance](https://learn.chatgpt.com/docs/sandboxing).

The helper and CLI must come from the manifest's matching release. The systemd
unit in `deployment/systemd/oko-codex.service` sets a private home/auth directory,
private temporary files, memory/task limits, no new privileges and whole-cgroup
termination. A failed supervisor, including one lost after cancellation, ends
the service unsuccessfully. systemd removes its owned runtime generation before
restarting it. An uncertain process stop is never a successful AI result.

Example operational commands:

```sh
sudo systemctl start oko-codex.service
sudo systemctl status oko-codex.service
sudo systemctl restart oko-codex.service
sudo systemctl stop oko-codex.service
```

## Authentication and filesystem boundary

Use ChatGPT authentication; there is no paid API fallback. OpenAI documents
headless login using device authorization or secure transfer of the local login
cache. Treat the cache as a password and let CLI refresh it during use:
[official authentication guidance](https://learn.chatgpt.com/docs/auth).

The service's persistent `CODEX_HOME` is private (0700). Every CLI invocation
gets a separate credential-only view containing auth, installation identity and
model-cache files. Ambient config, rules and skills never enter it. Linux
bubblewrap mounts only the fixed package/system files, owned scratch and this
view. Tool/plugin/agent features are disabled and completion uses read-only
sandbox policy with no approvals. API/provider keys are removed from child env;
Linux inference enforces ChatGPT login and file credential storage.

Credential views use private persisted pending records. Atomic refresh writeback
checks the original auth digest so an operator's newer login wins. Recovery
preserves a refresh already written by CLI if a supervisor dies before the
context exits. This does not claim to recover a token that CLI never wrote.

Auth cache and its pending views are host-only secrets. They never enter the
reader image or its mounts. Source input/results stay bounded by the invocation
lease, deadline, response limits, ephemeral CLI mode and checked scratch cleanup.

## Container-to-host transport

Linux uses `/srv/oko/ipc/helper.sock` on the host, mounted as
`/run/oko-ipc/helper.sock` in the reader, not a public TCP listener. Bind-mount the
socket's **persistent directory** read-only so helper restarts replace the socket
safely without replacing the mounted parent inode. Job scratch remains ephemeral.
The directory belongs to `okocodex:oko-ipc` with mode 0750; the socket is 0660.
The reader's supplementary `oko-ipc` GID permits connection. A separate private
Bearer token authenticates requests and boot/session IDs own each attempt.

```sh
python -m oko --codex-socket /run/oko-ipc/helper.sock \
  --codex-token-file /run/secrets/codex --persistent --no-browser --data /data
```

The host token and reader token files contain the same IPC token, each readable
only by its service identity. Codex OAuth credentials stay exclusively in the
host user's private directory. Helper unavailability or exhausted quota stops
processing while stored library content remains readable. Model/effort/Fast
choices preserve ADR-0001; Luna is used for smoke verification only.
