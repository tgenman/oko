# Cloud observability

The monitoring Compose project is independent of `oko-app` and `oko-infra`.
Changing dashboards, retention or collectors does not recreate the reader or
PostgreSQL. Images and Linux/amd64 digests are pinned in `images.json`.

## Installation and access

Run `sudo python3 deployment/observability/install.py` from a reviewed release
checkout on the Oko VM. It pulls and checks images before taking the common
maintenance lock, creates component-only credentials, grants `pg_monitor` to
`oko_monitor`, installs persistent journald and the host collector, and starts
only `oko-monitoring`. Install the reader from the same release to enable its
separately authorized, GET-only `/metrics` endpoint. Its credential grants no
reader or administration access. The host collector does not invoke Codex. Availability comes from actual worker
checks or an explicit UI check and becomes unknown after five minutes. Scrapes
do not make an AI call; unknown cached status alone is not a down alert. Helper
health and unresolved work errors have separate alerts.

Open an SSH tunnel:

```sh
ssh -N -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 okoadmin@201.79.3.253
```

Grafana: `http://localhost:3000`, user `oko-admin`. Read its generated password
privately from `/srv/oko/secrets/grafana-admin` on the VM. Do not paste it into
issues or logs. Prometheus is `http://localhost:9090`. Neither panel has a public
port. Loki also binds only loopback. Exporters and Alloy have no published port.

## Persistence and limits

| Component | Persistent host path | Retention / memory limit |
| --- | --- | --- |
| Loki chunks, TSDB index, WAL, compactor | `/srv/oko/loki` | 7 days; asynchronous compactor deletion after 2-hour delay; 384 MiB |
| Alloy positions and outgoing WAL | `/srv/oko/alloy` | durable delivery state; 192 MiB |
| Prometheus TSDB, head and WAL | `/srv/oko/prometheus` | 15 days or 2 GB, whichever removes old blocks sooner; 256 MiB |
| Grafana users, settings, dashboards | `/srv/oko/grafana` | 256 MiB |
| Docker stdout/stderr | `/var/lib/docker/containers` | json-file, 3 × 10 MB per service |
| Helper journal | `/var/log/journal` | persistent, 7 days / 200 MB |

Retention is asynchronous and does not reserve free disk. `OkoDiskLow` checks
root filesystem space independently. Node exporter (48 MiB) reads host proc/sys
and filesystems as nonroot; PostgreSQL exporter (64 MiB) has no library table
permissions and never enables SQL text collectors. Individual memory limits are
ceilings; the actual combined load must be measured on the 4 GB VM before final
commissioning.

Alloy reads supported Docker **json-file** logs and the systemd journal. It has
no Docker socket. A root-owned timer generates targets for fixed Oko Compose
projects only; the collector receives no source text or credential values.
Alloy needs root for these read-only log mounts; it has no host root/proc mount.

Indexed log labels are bounded `deployment`, `service`, and `level`; task IDs
stay in JSON fields. In Grafana Explore, select Loki and query, for example:

```logql
{deployment="oko",service="reader",level="error"} | json
{deployment="oko",service="codex"} | json | job_id="42"
```

Reader/helper operational events omit prompts, source contents, results and
exception messages. PostgreSQL can interpolate stored values into errors, so
Alloy forwards its fixed `postgresql_event` marker and severity, dropping the
arbitrary database message. Raw local Docker logs remain root-only and should
be treated as sensitive; they are not backed up. Reader's private diagnostic
journal remains library data and is not ingested by Alloy.

## GitHub notifications and external checks

The owner selected GitHub account notifications. `.github/workflows/monitor.yml`
runs from GitHub, outside this VM. It checks trusted public HTTPS, the VM via a
dedicated forced-command SSH key, current Prometheus alerts, and the backup
heartbeat independently of Prometheus. An incident creates an `operations-alert`
issue assigned to `tgenman`; recovery closes it. Stable incidents do not generate
repeated comments. A failed observation never closes unrelated active alerts.
GitHub account notification preferences control email/push presentation.
Scheduled Actions are best effort, roughly every ten minutes; GitHub can delay
runs. They are not a hard real-time paging service. No new paid service or
GitHub write credential is installed on the VM.

Authorize only the dedicated public key by piping it into
`sudo python3 deployment/observability/authorize-monitor.py`. The key's forced
command returns fixed alert names and the backup timestamp; no general shell,
port forwarding, arbitrary commands, environment values or log payloads. The
corresponding private key belongs in repository secret `OKO_MONITOR_SSH_KEY`;
the independently verified SSH host key goes in `OKO_SSH_KNOWN_HOSTS`.
Create label `operations-alert`, then enable repository variable
`OKO_MONITOR_ENABLED=true` only after end-to-end delivery verification.

## Backup heartbeat contract

The backup producer atomically writes root-only `/srv/oko/backup/status.json`:

```json
{
  "last_success_timestamp_seconds": 1800000000,
  "last_success_size_bytes": 1024,
  "last_attempt_timestamp_seconds": 1800000000,
  "last_attempt_result": "success"
}
```

`last_success_*` only advance after a complete, verified remote backup;
`last_attempt_result` is `unknown`, `success`, or `error`. Failure preserves the
previous success. Missing/invalid data or a last success older than 36 hours is
not healthy. The collector emits fixed metric names with a bounded result label;
the external monitor reads the same success timestamp even if Prometheus is
down. Backup implementation and actual restore acceptance belong to #131.

This document describes configuration. Live verification, delivery, rotation,
receiver outages and measured resource use must be recorded separately before
closing #130 or commissioning the full installation in #133.
