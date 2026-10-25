# Cloud installation

The production target is the separately provisioned `oko-prod-fra1` VM,
Ubuntu 24.04, amd64, 2 vCPU / 4 GB RAM / 80 GB disk. Its domain is
`oko.observer`. The Mac installation and VPN server are independent.

## Installed components

```text
Internet :443 -> reader container / Uvicorn TLS
                   | PostgreSQL connection file -> private postgres container
                   | Unix socket -> host systemd Codex helper -> ChatGPT subscription
Certbot DNS-01 -> Cloudflare zone oko.observer -> Let's Encrypt
                -> validated immutable certificate pair -> reader restart
```

`compose.app.yaml` and `compose.infra.yaml` are separate Compose projects.
The app image must be a checked Linux release digest from the private GHCR
package; its source checkout and host helper must have the manifest's SHA.
PostgreSQL is pinned to an official image digest. Neither project publishes a
database or helper port. The reader runs as UID 10001, without capabilities,
with a read-only root filesystem and bounded memory, processes and Docker logs.

Persistent bind mounts are under `/srv/oko`: `data`, `postgres`, `tls`, `ipc`,
`loki`, `alloy`, `prometheus`, `grafana`. The last four directories are prepared
for the observability task. PostgreSQL 18 mounts `/var/lib/postgresql`, including
its versioned data directory. Text and processing results are SQL data; media
are files in `data`. A persistent mount survives container replacement; an
off-machine backup is still necessary for VM loss.

`secrets` is root-only. Separate private files hold the PostgreSQL admin
password, migration DSN, reader DSN and helper reader token. The reader has DML
access but cannot change schema or the migration ledger. Only the migration
container receives its schema-owner credential. The helper's private writable
ChatGPT login stays in `/var/lib/oko-codex/auth` and never enters the reader.

The IPC directory is persistent: replacing a socket leaves the mounted parent
directory intact across helper restarts. Job scratch remains in the helper's
systemd runtime directory and is removed during group cleanup.

The host helper uses UID 20002 and IPC GID 20001. Its UID must differ from
PostgreSQL's 999 and the reader's 10001, since a host process can otherwise have
owner access to the database's bind mount. Bootstrap rejects an older conflicting
account; stop the whole helper cgroup and migrate only its own home/code/IPC
ownership under the maintenance lock before retrying. Do not change PostgreSQL
ownership as part of that account migration.

## Operator interface

From an immutable, root-owned checkout at `/srv/oko/releases/<SHA>`, run the
reviewed `deployment/bootstrap-host.sh` as root. It installs the pinned host
dependencies, dedicated helper account, operator wrapper and certificate retry
timer. It does not provision authentication or start the reader.

Install private GHCR read credentials in the root-only Docker config directory
`/srv/oko/secrets/docker`; do not put a token in argv, Git, an image or Actions
logs. Authenticate the dedicated helper through the approved ChatGPT login
procedure in [Linux release](linux-release.md).

The operator commands are:

```sh
sudo okoctl install --manifest /path/to/verified-manifest.json
sudo okoctl start
sudo okoctl stop
sudo okoctl status
```

All operations use `/srv/oko/maintenance.lock`. Backup, deploy and restore must
use that same lock. Stop waits for the reader to exit and systemd to kill the
entire helper cgroup before reporting success. It leaves PostgreSQL running for
a consistent dump. Start waits for infrastructure health before the reader.
The PostgreSQL CLI additionally retries connection readiness for 30 seconds,
then exits on failure so Docker can retry after a reboot; an unavailable database
does not leave a permanently live but unusable reader process.

Install pulls and verifies the manifest's image before database changes, creates
separate SQL roles, applies migrations, disables work in the initially empty
library and starts the services. It refuses a second installation and refuses
to regenerate missing credentials for an existing PostgreSQL data directory.
Create reader credentials through `scripts/oko-access`; do not import personal
sources as part of the first installation.

## Native HTTPS and renewal

Uvicorn receives `--tls-cert` and `--tls-key` together with the HTTPS public URL.
It loads the pair before creating reader data. There is one HTTPS listener and
no reverse proxy. Private control connects to loopback while verifying the
certificate against the configured public hostname; the request uses a local
Host and the private service bearer. The server additionally requires the actual
peer to be loopback. Forwarded headers cannot grant that access. External
requests use password sessions, Secure cookies and the existing Origin checks.

Issue through Certbot's Cloudflare DNS plugin using a separate DNS Edit token
restricted to `oko.observer`, in a private credentials file. Run ACME staging
first with separate config/work/log directories; staging certificates never enter
the serving directory. Production uses `/etc/letsencrypt`, certificate name
`oko.observer`, DNS-01, and no port 80. Set the production deploy hook to
`deployment/certbot-deploy-hook.sh` and enable `certbot.timer`.

The hook starts `oko-certificate-apply.service`; its persistent hourly timer
retries even when a previous attempt lost the maintenance lock. The operator
checks key/certificate pairing, hostname and expiry before copying either file.
It writes a complete immutable version and atomically replaces `tls/current`.
The reader sees the parent directory through a read-only mount and is recreated
under the maintenance lock. A failed health check restores the previous pointer
and recreates the reader with the previous pair. A durable applied-version receipt
is written only after successful readiness; a crash after pointer replacement
without a receipt is retried. Unchanged applied certificates do not restart the
reader every hour.

## Acceptance is separate

Source checks do not prove installation. Before closing the installation ticket,
record the actual image digest/helper SHA, private GHCR visibility, DNS-only A
record, staging and production DNS challenge cleanup, certificate renewal/apply,
reader login/media behavior, delayed PostgreSQL startup, persistent synthetic
data after container recreation and VM reboot, and reader access while Codex is
stopped. Use Playwright and inspect its screenshots. Keep work disabled and
personal source credentials absent.

Sources: [Uvicorn HTTPS settings](https://uvicorn.dev/settings/#https),
[PostgreSQL image](https://hub.docker.com/_/postgres),
[Certbot Cloudflare plugin](https://certbot-dns-cloudflare.readthedocs.io/).
