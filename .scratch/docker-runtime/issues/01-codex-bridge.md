# Codex on Mac: authenticated helper and remote adapter

GitHub: https://github.com/tgenman/oko/issues/49
Status: done
Blocked by: Нет

Specification: [Docker runtime](../spec.md).

## Acceptance

- [x] Preserve CodexClient check/catalog/complete and execution settings.
- [x] Validate auth, protocol, sizes, IDs and single-slot admission.
- [x] Test leasing, cancellation, duplicate IDs, lost replies, boot changes and parent-death cleanup.
- [x] Keep existing macOS sandbox and credentials outside Docker.

## Result

Implementation: `2aea94c`. Full isolated check all PASS (184.14s); prepared Docker image smoke PASS on localhost:8765. Independent Standards and Spec re-reviews: no actionable findings. See [verification](../../../docs/verification/docker-runtime.md). Native CLI0.163 approval, LaunchAgent startup and personal-data migration remain separate deployment steps.
