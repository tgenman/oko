# Immutable Docker and native helper releases

GitHub: https://github.com/tgenman/oko/issues/51
Status: done
Blocked by: 01, 02

Specification: [Docker runtime](../spec.md).

## Acceptance

- [x] Build clean explicit revision with locked dependencies and matching assets; exclude private data.
- [x] Publish only 127.0.0.1:8765; install native helper outside checkout.
- [x] Provide start/stop/open/status/update and consistent backups with protected rollback.
- [x] Verify Docker/Mac networking; leave personal migration until release passes checks.

## Result

Implementation: `2aea94c`. Full isolated check all PASS (184.14s); prepared Docker image smoke PASS on localhost:8765. Independent Standards and Spec re-reviews: no actionable findings. See [verification](../../../docs/verification/docker-runtime.md). Native CLI0.163 approval, LaunchAgent startup and personal-data migration remain separate deployment steps.
