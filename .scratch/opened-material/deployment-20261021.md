# Merge and working Oko update — 2026-10-21

Status: done

PR: https://github.com/tgenman/oko/pull/68 (merged)
PR CI: https://github.com/tgenman/oko/actions/runs/39601690091 — all jobs successful, including Full checks.

Origin/main: 2a61f6e085d7647c59dcb2ee254e3e36fb73a6fd
Running release: 2a61f6e085d7647c59dcb2ee254e3e36fb73a6fd
Helper release: 2a61f6e085d7647c59dcb2ee254e3e36fb73a6fd
Image: sha256:96daf920941d232f3870e9655efde0cebb5598e7b4d4947f914d775c4cc89380
Previous release: e19467a7c118f2c58a8e1e2cb94a59a7163589dd

## Execution and verification

The integration merge preserves the complete previously tested source tree (54fa14b3a870c7d04d7b0c38c9be8cf90a25d9fe). The PR diff contains only this task. GitHub merged it with squash.

`scripts/update-working-oko` built the fetched origin/main in a clean temporary clone. Tracked and untracked development changes were excluded. A consistent backup was created before switching the reader/helper pair. The first start stopped on a busy port 8765 check; no foreign process was terminated. A subsequent listener/bind check found the port free. The exact transient cause was not established. `scripts/oko-release start` was retried successfully without restoring or discarding data.

Reader container: running / healthy. Helper loaded, correct protocol and release verified from the container by the standard start probe. Reader status HTTP 200, runtime_state=running. Frontend HTML and /assets/reader-DF1Q809z.js served successfully.

## Backup

Location: /Users/dabondarev/Library/Application Support/Oko/backups/20261021T125016532755Z
Release: e19467a7c118f2c58a8e1e2cb94a59a7163589dd
Data digest: b28bc36bd39a2a77d9d6d1b1deb384b8e293a9f80b68dab51142d0789d443ff1

No rollback or data restore was performed.

## Evidence

- ../check-runs/opened-material-deploy/before.json
- ../check-runs/opened-material-deploy/update.log (first start failure retained)
- ../check-runs/opened-material-deploy/start-retry.log
- ../check-runs/opened-material-deploy/after.json
- ../check-runs/opened-material-deploy/pr-ci.json
