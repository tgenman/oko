# First cloud installation acceptance — 2026-10-25

This report records the installed Oko reader, PostgreSQL and subscription helper
for #128/#129. Observability, off-machine backups, regular Actions deployment and
the final 4 GB load acceptance are separate remaining tasks.

## Installed release and boundaries

- Target: `oko-prod-fra1`, Frankfurt, Ubuntu 24.04, amd64, 2 vCPU / 4 GB / 80 GB.
- Public reader: `https://oko.observer`, DNS-only A record `201.79.3.253`, TTL 300.
- Cloud reader/helper source SHA: `6413e41ab004c9cf907b347e0949ad4f888d3da5`.
- Image: `ghcr.io/tgenman/oko-runtime@sha256:2552fc14a0c3def50e4b43887b764b43160748ed7c422c5c4acd40ae18add59c`.
- Release publication run: [40168565422](https://github.com/tgenman/oko/actions/runs/40168565422), successful after all main Checks; manifest records amd64, helper protocol 1, Codex 0.164.0 and PostgreSQL schema range 3–3.
- PostgreSQL 18 uses its pinned official image and a bind mount at `/srv/oko/postgres`; app text/results are SQL rows and media are files under `/srv/oko/data`. No `state.sqlite` fallback exists.
- App/infra are separate Compose projects with resource limits, restart policies and private credentials. Loki/Alloy/Prometheus/Grafana data directories are prepared for #130.
- Only SSH and HTTPS are allowed by the new VM's cloud firewall. Port 80, PostgreSQL, helper and administrative monitoring ports are not public. Root/password/keyboard-interactive SSH login are disabled; public-key authentication remains enabled.
- The Mac working release and VPN server were not modified.

The private `oko-runtime` package replaces the earlier public `oko` package.
GitHub cannot revert a public package to private. Private visibility and repository
association were read back; anonymous pull was denied. Server pulls used only a
package-scoped read bearer, removed after each operation. The personal GitHub
OAuth credential remains in the local system keyring.

## HTTPS, authentication and subscription

Let’s Encrypt staging and production DNS-01 issuance succeeded with a dedicated
Cloudflare DNS Edit token restricted to this zone. Temporary TXT records were
removed. The actual production certificate is issued by Let’s Encrypt YE2 and
expires on 2027-01-23. `certbot reconfigure --run-deploy-hooks` succeeded and
persisted the certificate-specific hook; renewal and apply timers are enabled.

Actual certificate application was checked under the maintenance lock: an invalid
key left the active pair/container unchanged; an equivalent valid PEM version
caused a controlled reader recreation; the canonical pair and applied receipt
were restored. Keys stayed `0640 root:GID10001` and the DNS credential `0600 root`.

The real Playwright HTTPS run checked login, secure-context status, Secure /
HttpOnly / SameSite=Strict cookie flags, wrong-Origin rejection and cross-tab
logout. It first verified an empty library and disabled work. The initial owner
is `reader`; its random password is only in the root-private VM credential file,
not in this report. Public service-bearer access was denied, including spoofed
localhost and forwarded headers; private verified loopback control worked.

A separately authorized tiny synthetic request ran from the actual reader
container through the host UDS using `gpt-6-luna`, effort `low`. The helper check
reported ChatGPT authorization, and the result was `{"ok":true}`. No API key or
paid-API fallback was configured. Model inference is not part of automatic CI.

## Persistence and recovery checks

The public [synthetic fixture](../../../tests/fixtures/cloud-acceptance/README.md)
was collected through the normal production downloader with AI disabled. It
created one test publication with saved text and PNG attachments. No personal
sources or Telegram credentials were connected.

The same material ID, text and media SHA-256 remained readable in a real browser
and through authenticated HTTP in all four phases:

1. Before container recreation.
2. After stopping reader/helper and recreating only the reader with helper still
   stopped. The library remained readable. Restarting helper restored authenticated
   UDS health without another reader recreation.
3. After an actual VM reboot. Kernel boot ID changed from
   `19c34df1-3b97-4fb0-96bf-02111a23ceeb` to
   `9884aee5-deb5-41a8-9c77-f5ff06001c01`. Both containers became healthy, helper
   resumed, browser authentication survived, and work stayed disabled.

4. After controlled recreation of the same PostgreSQL container/image using its
   original bind mount. Container ID changed, PostgreSQL cluster identifier stayed
   the same, and the same material/text/both PNG attachments remained readable.

Both post-reboot attachments were independently checked against the fixture hash.

Media SHA-256: `a2f5d6c396543993cbbbe38571336bf4aa10bff1f56dba98cd6820b0fafec4ae`.
The accepted text and IDs are recorded in `persistence-acceptance.json`; cookie
security results are in `https-acceptance.json`. Screenshots were visually inspected.
The synthetic witness remains temporarily for backup/deployment acceptance and
will be removed before the final empty-library handoff.

## Failures encountered and corrections

- First bootstrap rejected Ubuntu's `2.40.3+ds1…` Compose suffix. PR #138 accepts
  that metadata while preserving the upstream `2.40.3` pin; all CI passed.
- Default helper UID 999 collided with PostgreSQL. Before DB creation, helper was
  stopped under the maintenance lock, target UID availability / old-process exit
  were checked, and only Codex home/code/IPC ownership was migrated to UID 20002
  without following symlinks. PostgreSQL paths were not changed. Authenticated
  helper health recovered; current socket is `20002:20001 0660`.
- First reader start failed to load TLS: service `UMask=0077` made the versions
  directory `0700`. A minimal real non-root container probe reproduced Permission
  denied while root loaded the same pair. A new real-umask regression failed,
  then passed after explicit directory `0750`; keys remain `0640`. Existing
  PostgreSQL data/schema were retained and the installed release restarted.
  These source fixes merged in PR #140; the operator/release suite passed 18 tests
  plus independent review and full CI.
- The harness initially attempted verification while work was disabled (correctly
  rejected), expected an obsolete nested subscription response, and used a private
  Docker URL (correctly rejected by the downloader). The harness was corrected to
  the canonical DTO and public synthetic fixture; product safeguards were retained.
- Browser navigation immediately following planned reader replacement hit closed
  connections / an asynchronous Chrome error page. A fresh page in the same cookie
  context and bounded retries for transient navigation errors completed the scenario.
  Certificate verification and data assertions remained strict.

The installed application/helper remain at the release SHA above. Later host
bootstrap source fixes and this report have their own Git commits; they do not
claim an application update. Whole-stack monitoring/backup/CD/resource acceptance
has not yet been completed by this report.
