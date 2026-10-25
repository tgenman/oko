# Cloud application deployment

`deployment/deploy.py` updates only the reader and host helper. PostgreSQL and
the monitoring project keep their container identities, volumes and configuration.
The common `/srv/oko/maintenance.lock` serializes application deployment,
certificate application, backups and manual maintenance. Workflows also use
one concurrency group with `cancel-in-progress: false`.

Before stopping services, deployment verifies helper UID 20002 and that the named
`oko-ipc` service group has GID 20001. The account's primary group can differ:
systemd explicitly runs the helper with `Group=oko-ipc`. The identity is checked
again at activation without changing the existing account or file ownership.

## Prepare the dedicated operator

Install the reviewed script from an immutable, root-owned release checkout
`/srv/oko/releases/<SHA>`. Generate an independent Ed25519 identity; store its
private half only in the repository Actions secret `OKO_DEPLOY_SSH_KEY`.
The existing `OKO_SSH_KNOWN_HOSTS` secret must contain the separately verified
server host key. Do not copy a personal SSH private key or GitHub OAuth token.

Authorize the public half on the VM:

```sh
sudo python3 /srv/oko/releases/<SHA>/deployment/deploy.py authorize \
  --public-key-file /path/to/dedicated-deploy-key.pub
```

This creates UID/GID 20004 (`oko-deploy`), checks account collisions, and installs
a root-owned `restrict` authorized-key entry. It invokes only
`sudo -n /usr/local/sbin/oko-deploy-receive` without arguments. The sudoers rule
does not permit an arbitrary shell, `sudo python`, forwarding, PTY or file copy.
The wrapper uses that specific immutable script; deploying an application does
not silently replace the privileged deployment implementation. Reauthorize from
a reviewed new release when updating the operator itself.

The endpoint accepts at most 64 KiB of JSON on stdin. It accepts only `deploy`
with a validated manifest and optional package-scoped pull bearer. Older or
divergent releases are rejected before stopping any service. No path, command, shell fragment,
arbitrary image registry or source repository can be supplied by the caller.

## Release selection and Actions

Use **Deploy Oko → Run workflow** on `main` with the numeric
ID of a successful **Publish Linux release** run. The workflow checks the exact
publication workflow path, event, repository, branch and conclusion; downloads
one bounded release-manifest artifact; verifies a successful push-triggered
**Checks** run for its exact SHA; and checks that SHA is an ancestor of `main`.
PR artifacts and failed or unverified publications are rejected before the
deployment key is loaded. It neither builds an image nor executes release source
on the Actions runner with production credentials.

The registry credential derives from this job's temporary `GITHUB_TOKEN` and
grants only pull access to `tgenman/oko-runtime`. Package privacy is checked again.
The bearer travels on SSH stdin into a temporary private Docker config removed
after pull. It is not placed in argv, Git, the image, an environment variable on
the VM, or the deployment receipt. The personal GitHub token is never sent.

Regular publication-triggered deployment remains disabled until repository
variable `OKO_DEPLOY_ENABLED=true` is explicitly set during commissioning.
Manual deployment does not require that flag. Every run keeps its sanitized
result artifact; server receipts are under `/srv/oko/deployment-history/`.

## Operator sequence

Before stopping anything, the command clones/checks the exact public source SHA,
verifies main ancestry, pulls the image digest, checks its Linux/amd64 revision,
compares the CLI pin with the manifest, installs its checksum-verified runtime,
and prepares the locked helper virtual environment. It then acquires the lock
and rechecks the active release and helper pointers to reject stale preparation.
The current database/schema and migration version come from the private reader
and migrator DSN files inside a bounded, disposable container. Both credentials
must select the same database/schema. This follows an imported schema such as
`oko_import146` rather than inspecting the historical `oko` schema. Credential
values never enter argv, environment variables or receipts; the receipt records
only the selected schema and version.

It stops reader/helper and confirms both have stopped. A pending schema release
requires `backup.create_backup(root, kind='pre-migration', lock_held=True)` first:
the real off-machine snapshot must be uploaded, downloaded, decrypted and
checksum-verified before migration starts. The backup producer must be configured
and its recovery key stored independently as described in
[cloud-backup.md](cloud-backup.md). A backup failure resumes the compatible old
pair and fails deployment; it never continues with migration.

Migration uses the new checked image, the separate migration DSN and the existing
backend network. Activation replaces the helper config/unit/source and only the
reader container. After Compose readiness, the reader's private control request
checks Store and authorization with verified TLS, public HTTPS is checked, and
authenticated UDS helper health must report the expected release. These probes
do not perform inference. Only after those checks and unchanged infrastructure
identities does the command persist the confirmed active manifest and operator pointer. Previous
release/helper metadata remains in the private history directory. Receipt fields include SHA/digest, previous SHA, schema, migration and
backup result, maintenance duration, and any stopping failure.

## Failed releases and forward repair

Deployment only moves to the same commit or a descendant of the last confirmed
release. If an attempt is already pending, the next candidate must also descend
from that pending commit. Neither the CLI, forced SSH receiver nor Actions offers
a rollback operation.

Before stopping the reader/helper, the operator atomically records the checked
candidate and the observed component identities in root-private
`deployment-pending.json`. File replacement and marker removal sync their parent
directories. Superseding a failed candidate preserves any known partial
helper/source/operator state for the next forward retry. A failed activation or
healthcheck stops the candidate and records `forward_repair_required=true`.
It never starts the previous pair or restores a database. PostgreSQL, durable
reader files and monitoring remain in place. A failure to stop is recorded as
`stop_error_class` alongside the original error.

Retry the same published candidate or deploy a newer fixed release through the
normal interface. The pending manifest permits the known partially activated
helper/source pair while preserving identity checks. A retry of that exact
candidate may reuse its already checked image/runtime without another registry
credential; a newer release receives normal preparation. Any completed schema
migration stays in place. A failed migration can be retried with the same or a
newer compatible release.

`release.json` and the operator pointer represent the last confirmed successful
release until activation passes healthchecks. The pending manifest and failure
receipt identify an incomplete attempt; they must be inspected together rather
than treating the previous successful manifest as evidence of a running reader.
Success writes the new manifest/pointer and removes the pending marker. A healthy
same-release request is a no-op; a stopped or unhealthy confirmed release can
be repaired by activating that same release. A retry
also rechecks that both manifests were unchanged while preparing.

Keep historical source, receipts and separately tagged pre-migration copies as
records. Their presence does not authorize starting an older release or applying
a database restore. No working-installation rollback is part of commissioning.

## Verification boundary

`tests/development/test_cloud_deploy.py` drives the real CLI against synthetic
Docker/Git/systemd/Restic executables. It checks ordering, rejection of older releases,
backup-before-migration, stopped failure states and forward retries,
maintenance contention and bounded SSH input. This is not live deployment proof.
Commissioning must separately execute a successful deploy through Actions
and verify that rejected or failed attempts never select an older pair and verify the reader's preserved data through HTTPS
and Playwright. Do not run destructive synthetic trials on the migrated personal
library.
