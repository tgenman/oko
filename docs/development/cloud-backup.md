# Encrypted cloud backups

`deployment/backup.py` operates on the installed cloud library through the same
`/srv/oko/maintenance.lock` as `okoctl`, deployment and certificate application.
The CLI always acquires this lock. Its internal `create_backup(root, kind,
lock_held=True)` function is reserved for a deploy process that already owns it.
Do not expose the internal bypass as a forced SSH command or an operator flag.

## Provisioning

Install a reviewed, pinned Restic binary on the host. Provision a private Spaces
repository and a least-privilege key for that bucket/prefix. Save the independent
repository password in the owner's password manager and verify retrieval there
before enabling the timer. A copy of that password must not depend on decrypting
the backup itself. GitHub encrypted secrets alone do not meet that requirement.

Restic cache is explicitly `/srv/oko/backup/cache`, within the systemd writable
root even with `ProtectHome=true`.

Root-private `/srv/oko/backup/config.json` contains only repository/path metadata:

```json
{
  "repository": "s3:https://fra1.digitaloceanspaces.com/BUCKET/oko",
  "password_file": "/srv/oko/secrets/backup-password",
  "credentials_file": "/srv/oko/secrets/backup-s3"
}
```

Both credential files must be regular files without group/other permission bits.
The password file contains the Restic repository password. The S3 file contains
exactly `AWS_ACCESS_KEY_ID=value` and `AWS_SECRET_ACCESS_KEY=value` on separate
lines; it is parsed as data, never sourced as shell code. Credential contents do
not enter command arguments or reports. Restic receives credentials through its
supported environment variables and a private password file. Root access to the
host remains a trust boundary.

Initialize the encrypted Restic repository with these same credentials through
a private operator environment; the backup command deliberately does not treat
an unknown/unreachable repository as permission to create a new one. Then run:

```sh
sudo python3 deployment/backup.py check
sudo python3 deployment/backup.py backup --kind nightly
sudo python3 deployment/backup.py install
# Retry original service intent after an interrupted manual backup:
sudo python3 deployment/backup.py recover
```

The installed timer runs nightly at 03:30 UTC with up to ten minutes jitter and
catches missed runs after downtime. Installation does not run a backup itself.
The parent acceptance must verify the installed binary version and actual timer.

## Consistency, contents and retention

The command holds the shared maintenance lock, remembers whether reader/helper
were running, stops both and confirms their exit. It obtains a supported custom
PostgreSQL dump of the whole `oko` database, so an active imported schema and a
retained rollback schema are both captured. It copies durable reader files,
service credentials, the exact release manifest and currently served TLS pair.
It captures only the allowlisted Codex credential/configuration files:
`auth/auth.json`, `auth/installation_id`, `auth/models_cache.json`,
`auth/config.toml`, `helper.json` and `bridge-token`. Valid
`auth/.pending-<32hex>/recovery.json` records and their credential-only `home/`
files (`auth.json`, `installation_id`, `models_cache.json`) are also preserved:
after supervisor death these can contain the only current rotated refresh token.
The existing runtime performs recovery from that metadata after restore; backup
never refreshes or rewrites the original login. `models_cache.json` is the bounded
artifact explicitly consumed by this credential-view contract, not permission to
copy arbitrary cache trees. CLI sessions, history, arbitrary caches and logs are
excluded, including inside pending views. Invalid/symlink recovery state fails
the backup rather than silently dropping potentially current credentials. On the production root it also captures Certbot renewal
state. No live PostgreSQL directory, monitoring history,
Docker registry bearer, Restic recovery password, logs or ephemeral staging is
copied. Backup files remain root-private before Restic encrypts them.

Reader/helper resume in their previous running/stopped state after the immutable
local snapshot is complete, **before** upload/download/retention. Other maintenance
continues to wait on the lock. A backup receives `pending` first; after a full
Restic restore/download and SHA-256 verification of every snapshot file it
receives `verified`. Retagging changes the immutable snapshot ID; the command
reads the exact new ID from Restic JSON and persists/returns that new identity.
Only then may a nightly prune run. The last seven verified
nightlies are retained as one host/tag group; failed/pending snapshots cannot
displace that history. Failed pending snapshots may need separate operator
cleanup after diagnosis. Pre-migration snapshots have their own tag and remain
outside nightly pruning until an operator explicitly accepts the corresponding
release and removes them. No automatic cleanup of protected pre-migration copies
is performed.

Before stopping either service, the command fsyncs `/srv/oko/backup/resume.json`
with the installed release SHA and original reader/helper running state. Both
resume attempts are independent: a failed helper restart does not prevent reader
recovery. Partial failure retains the receipt for a retry. The backup unit uses
`ExecStopPost` to run the lock-protected `recover` command after a signal/crash;
`oko-backup-recover.service` retries it at boot, including recovery from SIGKILL
or reboot while Docker's explicitly stopped reader remains stopped. Recovery
starts only services recorded as originally running and refuses a mismatched
release. An interrupted recovery marks the attempt as error without changing
previous success. Manual CLI users can invoke the same `recover` command.

`/srv/oko/backup/status.json` is written atomically. Its public metadata contract
is `last_success_timestamp_seconds`, `last_success_size_bytes`,
`last_attempt_timestamp_seconds`, `last_attempt_result` (`unknown/success/error`).
Failure preserves the previous success; `error_class` is diagnostic metadata
without raw exception/command output. The result includes measured pause seconds
and the immutable Restic snapshot ID. Monitoring treats unknown/stale success as
unhealthy. A successful night gives an RPO of up to one day; failures increase
that window. Snapshot-size metadata is uncompressed durable payload size.

## Isolated recovery

Existing target directories and production PostgreSQL are always refused. Prepare
an empty separate PostgreSQL container/database named `oko` with label
`org.oko.restore.root=/absolute/recovery/target`. Its Docker image must match the
PostgreSQL version required by the saved release. The target path must not yet
exist, and the PostgreSQL container must have no application tables. Run:

```sh
sudo python3 deployment/backup.py restore \
  --snapshot FULL_64_HEX_SNAPSHOT_ID \
  --target /srv/oko/recovery/ACCEPTANCE_ID \
  --postgres-container oko-isolated-recovery
```

The command downloads/decrypts and verifies the full snapshot before restoring
the custom dump transactionally. It sets `work-enabled=false` in every restored
library schema before any reader starts. Files become visible only after those
steps succeed. `recovery.json` records the snapshot, target container and disabled
state. Reader/helper are never automatically started for recovery. Failed DB
restore may leave the dedicated DB for diagnosis; discard/recreate that isolated
container before retrying. The live production database is not touched.

The dump omits ownership and ACLs to support recovery into an empty PostgreSQL
cluster. Before launching a reader, recreate the saved database identities and
apply least-privilege schema/table/sequence grants using the recovered private
credentials, including their current `search_path`. This provisioning is not
performed automatically by the restore command. Use the exact saved release and
configure its mounts/credentials to the isolated data/DB, verify material/media,
settings and disabled work through HTTPS/Playwright, and measure elapsed RTO.
Do not start a second real Codex helper against the original rotating login.

Moving a recovered instance into production is a separate deliberate switch:
first preserve the current production DB/files, hold maintenance lock, select the
recovery and its matching release/credentials, then verify before enabling work.
This CLI never overwrites newer production data or silently restores on rollback.

## Verification status

CLI tests use temporary synthetic libraries and external executable substitutes;
they verify credential privacy, stop/snapshot/resume/upload ordering, failed
upload/download, checksum corruption, stopped-state preservation, protected
pre-migration retention, retagged snapshot IDs, interrupted-service recovery and
refusal to overwrite a recovery target. A separate local roundtrip with the
checksum-verified official Restic 0.19.1 binary confirmed that the returned
retagged snapshot exists and restores the same synthetic media; Docker/systemd
were still synthetic executable substitutes. These checks do not prove Spaces
access, encryption-key recovery from the owner's manager, actual systemd boot
recovery, scheduler execution, a real PostgreSQL restore, final HTTPS/Playwright
behavior or actual notification delivery. Record those live acceptance results separately before
closing #131. Actual pause/RTO must come from those runs, not estimates.

Restic behavior: [relative backup paths](https://restic.readthedocs.io/en/v0.19.0/040_backup.html#absolute-and-relative-paths),
[restore](https://restic.readthedocs.io/en/v0.19.0/050_restore.html).
