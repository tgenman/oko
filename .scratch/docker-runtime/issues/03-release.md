# Immutable Docker and native helper releases

GitHub: https://github.com/tgenman/oko/issues/51
Status: claimed
Blocked by: 01, 02

Specification: [Docker runtime](../spec.md).

## Acceptance

- [ ] Build clean explicit revision with locked dependencies and matching assets; exclude private data.
- [ ] Publish only 127.0.0.1:80; install native helper outside checkout.
- [ ] Provide start/stop/open/status/update and consistent backups with protected rollback.
- [ ] Verify Docker/Mac networking; leave personal migration until release passes checks.
