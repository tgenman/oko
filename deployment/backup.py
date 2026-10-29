#!/usr/bin/env python3
"""Consistent, encrypted off-machine backups for the installed Oko release."""
import argparse
from contextlib import contextmanager, nullcontext
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import shutil
import tempfile
import time
import subprocess
import sys

ROOT = Path('/srv/oko')


def private_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError('Credentials require a private regular file (0600)')
    return path


def configuration(root):
    config = json.loads((root / 'backup/config.json').read_text())
    password = private_file(Path(config['password_file']))
    credentials = private_file(Path(config['credentials_file']))
    environment = dict(os.environ, RESTIC_REPOSITORY=config['repository'], RESTIC_PASSWORD_FILE=str(password),
                       RESTIC_CACHE_DIR=str(root / 'backup/cache'))
    for line in credentials.read_text().splitlines():
        if not line.strip():
            continue
        key, value = line.split('=', 1)
        if key not in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY') or not value:
            raise ValueError('Invalid backup credential format')
        environment[key] = value
    if not all(environment.get(key) for key in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY')):
        raise ValueError('Missing backup credentials')
    return environment



def execute(*args, env=None, cwd=None, input=None, stdin=None, stdout=subprocess.PIPE):
    return subprocess.run(args, env=env, cwd=cwd, input=input, check=True,
                          stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, timeout=3600).stdout


def atomic(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, indent=2)
            output.write('\n')
            os.fchmod(output.fileno(), mode)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def maintenance(root):
    # Same inode and flock protocol as okoctl and certificate application.
    with (root / 'maintenance.lock').open('a') as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another maintenance operation is running') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def compose(root, *args):
    release = json.loads((root / 'release.json').read_text())
    env = dict(os.environ, OKO_IMAGE=release['image'], DOCKER_CONFIG=str(root / 'secrets/docker'))
    return execute('docker', 'compose', '-f', str(root / 'operator/compose.app.yaml'), *args, env=env)


def is_running(root):
    return execute('docker', 'inspect', '--format', '{{.State.Running}}', 'oko-app-reader-1').strip() == b'true'


def helper_active():
    return subprocess.run(['systemctl', 'is-active', '--quiet', 'oko-codex.service'],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10).returncode == 0


def safe_copy(source, target, excluded=()):
    if source.is_symlink():
        raise ValueError('Backup source symlinks require an explicit recovery plan')
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source, target)
        target.chmod(0o600)
    elif source.is_dir():
        target.mkdir(parents=True, exist_ok=False, mode=0o700)
        for child in sorted(source.iterdir()):
            if child.name not in excluded:
                safe_copy(child, target / child.name)
    else:
        raise ValueError('Backup source must be a regular file or directory')


def hashes(root):
    values = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Unexpected symlink in backup')
        if path.is_file() and path != root / 'checksums.json':
            digest = hashlib.sha256()
            with path.open('rb') as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(block)
            values[str(path.relative_to(root))] = digest.hexdigest()
    return values


def verify_snapshot(root):
    expected = json.loads((root / 'checksums.json').read_text())
    if not expected or hashes(root) != expected:
        raise ValueError('Restored file checksums do not match')
    if not (root / 'database.dump').is_file() or not (root / 'release.json').is_file():
        raise ValueError('Incomplete library snapshot')



def recover_locked(root):
    """Resume only pre-backup intent, including after SIGKILL/reboot; caller owns lock."""
    receipt = root / 'backup/resume.json'
    if not receipt.exists():
        return False
    intent = json.loads(receipt.read_text())
    release = json.loads((root / 'release.json').read_text())
    if (intent.get('version') != 1 or intent.get('release') != release['sha']
            or any(not isinstance(intent.get(key), bool) for key in ('reader', 'helper'))):
        raise ValueError('Backup resume intent does not match the installed release')
    failed = False
    if intent['helper']:
        try:
            execute('systemctl', 'start', 'oko-codex.service')
        except Exception:
            failed = True
    if intent['reader']:
        try:
            compose(root, 'up', '-d', '--wait', '--wait-timeout', '90', 'reader')
        except Exception:
            failed = True
    if failed or (intent['reader'] and not is_running(root)) or (intent['helper'] and not helper_active()):
        raise RuntimeError('Backup service recovery is incomplete; durable intent retained')
    receipt.unlink()
    directory = os.open(receipt.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return True


def copy_codex_credentials(home, target):
    """Preserve the runtime's credential-only refresh recovery state, never CLI history."""
    def copy_file(source, destination):
        if source.is_symlink():
            raise ValueError('Unexpected symlink in Codex credential recovery')
        if source.exists():
            if not source.is_file() or source.stat().st_size > 2_000_000:
                raise ValueError('Invalid Codex credential recovery file')
            safe_copy(source, destination)

    auth = home / 'auth'
    if home.is_symlink() or auth.is_symlink():
        raise ValueError('Unexpected symlink in Codex credential directory')
    # Match the files consumed by codex_linux.credentials(), plus explicit host config.
    credential_names = ('auth.json', 'installation_id', 'models_cache.json')
    for name in (*('auth/' + value for value in credential_names), 'auth/config.toml', 'helper.json', 'bridge-token'):
        copy_file(home / name, target / name)
    for pending in sorted(auth.glob('.pending-*')):
        if pending.is_symlink() or not pending.is_dir() or not re.fullmatch(r'\.pending-[0-9a-f]{32}', pending.name):
            raise ValueError('Unknown Codex credential recovery directory')
        metadata = pending / 'recovery.json'
        if metadata.is_symlink():
            raise ValueError('Unexpected symlink in Codex credential recovery')
        if not metadata.exists():
            # The runtime fsyncs metadata before starting any CLI: no record means no refresh.
            continue
        if not metadata.is_file() or metadata.stat().st_size > 2_000_000:
            raise ValueError('Invalid Codex credential recovery metadata')
        record = json.loads(metadata.read_bytes())
        if (not isinstance(record, dict) or record.get('version') != 1
                or not isinstance(record.get('base'), str) or not re.fullmatch('[0-9a-f]{64}', record['base'])):
            raise ValueError('Unknown Codex credential recovery metadata')
        view = pending / 'home'
        if view.is_symlink() or (view.exists() and not view.is_dir()):
            raise ValueError('Invalid Codex credential recovery view')
        destination = target / 'auth' / pending.name
        copy_file(metadata, destination / 'recovery.json')
        for name in credential_names:
            copy_file(view / name, destination / 'home' / name)

def snapshot_locked(root, target):
    """Create immutable files while the caller owns maintenance.lock."""
    recover_locked(root)
    reader, helper = is_running(root), helper_active()
    release = json.loads((root / 'release.json').read_text())
    atomic(root / 'backup/resume.json', dict(version=1, release=release['sha'], reader=reader, helper=helper))
    started = time.monotonic()
    try:
        if reader:
            compose(root, 'stop', '--timeout', '30', 'reader')
        if helper:
            execute('systemctl', 'stop', 'oko-codex.service')
        if is_running(root) or helper_active():
            raise RuntimeError('Reader or helper did not stop')
        target.mkdir(mode=0o700)
        with (target / 'database.dump').open('wb') as dump:
            execute('docker', 'exec', '-u', 'postgres', 'oko-infra-postgres-1',
                    'pg_dump', '-U', 'postgres', '-d', 'oko',
                    '--format=custom', '--no-owner', '--no-acl', stdout=dump)
        (target / 'database.dump').chmod(0o600)
        # Only durable library state; process state, staging and private log files are excluded.
        excluded = {'runtime.json', 'runtime.tmp', 'instance.lock', 'access.lock', 'staging',
                    'launcher.log', 'debug.json', '.DS_Store'}
        safe_copy(root / 'data', target / 'data', excluded)
        safe_copy(root / 'release.json', target / 'release.json')
        config = json.loads((root / 'backup/config.json').read_text())
        omitted = {'docker', Path(config['password_file']).name, Path(config['credentials_file']).name}
        safe_copy(root / 'secrets', target / 'secrets', omitted)
        if (root / 'tls/current').exists():
            # Store the currently served complete pair, not symlink targets across snapshots.
            safe_copy((root / 'tls/current').resolve(), target / 'tls')
        codex_home = Path('/var/lib/oko-codex') if root == ROOT else root / 'host-codex'
        copy_codex_credentials(codex_home, target / 'codex-host')
        if root == ROOT:
            # Certbot renewal trees contain internal relative symlinks; copy their resolved
            # bytes only when they stay within the selected certificate directory.
            acme = Path('/etc/letsencrypt')
            for path in acme.rglob('*'):
                if path.is_file():
                    resolved = path.resolve()
                    if not resolved.is_relative_to(acme):
                        raise ValueError('Certificate path leaves its private directory')
                    safe_copy(resolved, target / 'letsencrypt' / path.relative_to(acme))
        atomic(target / 'checksums.json', hashes(target))
    finally:
        recover_locked(root)
    return round(time.monotonic() - started, 3)


def create_backup(root, kind='nightly', *, lock_held=False):
    """Internal deploy integration may pass lock_held only while owning maintenance.lock."""
    if kind not in ('nightly', 'pre-migration'):
        raise ValueError('Invalid backup kind')
    root = Path(root).resolve()
    state_path = root / 'backup/status.json'
    # Keep the shared lock throughout upload: app can run, but deploy/restore must wait.
    with nullcontext() if lock_held else maintenance(root):
        try:
            previous = json.loads(state_path.read_text())
        except (OSError, ValueError):
            previous = {}
        state = dict(last_success_timestamp_seconds=previous.get('last_success_timestamp_seconds', 0),
                     last_success_size_bytes=previous.get('last_success_size_bytes', 0),
                     last_attempt_timestamp_seconds=time.time(), last_attempt_result='unknown')
        atomic(state_path, state, 0o644)
        try:
            recover_locked(root)
            env = configuration(root)
            with tempfile.TemporaryDirectory(prefix='snapshot-', dir=root / 'backup') as stage:
                stage = Path(stage)
                pause = snapshot_locked(root, stage / 'snapshot')
                raw = execute('restic', 'backup', '--json', '--host', 'oko-prod-fra1',
                              '--tag', 'oko', '--tag', kind, '--tag', 'pending', 'snapshot', env=env, cwd=stage)
                summaries = [json.loads(line) for line in raw.splitlines() if line.strip()]
                summary = next(value for value in reversed(summaries) if value.get('message_type') == 'summary')
                identity = summary['snapshot_id']
                if not isinstance(identity, str) or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
                    raise ValueError('Invalid backup snapshot identity')
                # Actually download/decrypt the new snapshot and compare every durable byte.
                restored = stage / 'verified'
                execute('restic', 'restore', identity, '--target', str(restored), env=env)
                verify_snapshot(restored / 'snapshot')
                size = sum(path.stat().st_size for path in (stage / 'snapshot').rglob('*') if path.is_file())
                tagged = execute('restic', 'tag', '--json', '--add', 'verified', '--remove', 'pending', identity, env=env)
                changes = [json.loads(line) for line in tagged.splitlines() if line.strip()]
                changed = [value for value in changes if value.get('message_type') == 'changed'
                           and value.get('old_snapshot_id') == identity]
                if len(changed) != 1:
                    raise ValueError('Retagged snapshot identity was not confirmed')
                identity = changed[0].get('new_snapshot_id')
                if not isinstance(identity, str) or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
                    raise ValueError('Invalid retagged snapshot identity')
                if kind == 'nightly':
                    execute('restic', 'forget', '--tag', 'oko,nightly,verified', '--group-by', 'host,tags',
                            '--keep-last', '7', '--prune', env=env)
                state.update(last_success_timestamp_seconds=time.time(), last_success_size_bytes=size,
                             last_attempt_result='success', snapshot_id=identity, pause_seconds=pause, kind=kind)
                atomic(state_path, state, 0o644)
                return state
        except BaseException as error:
            state.update(last_attempt_result='error', error_class=type(error).__name__)
            atomic(state_path, state, 0o644)
            raise



def restore_backup(root, identity, target, container):
    root, target = Path(root).resolve(), Path(target).absolute()
    if target.is_symlink() or target == root or target in root.parents or target.exists():
        raise ValueError('Restore requires an empty separate target that does not exist')
    if len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
        raise ValueError('Restore requires an exact snapshot ID')
    environment = configuration(root)
    with maintenance(root):
        details = json.loads(execute('docker', 'inspect', container))[0]
        if (details['Config'].get('Labels', {}).get('org.oko.restore.root') != str(target)
                or not details['State']['Running'] or container == 'oko-infra-postgres-1'):
            raise ValueError('Restore requires a dedicated labeled PostgreSQL container')
        count = execute('docker', 'exec', '-u', 'postgres', container, 'psql', '-U', 'postgres',
                        '-d', 'oko', '-Atc', "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')")
        if count.strip() != b'0':
            raise ValueError('Restore target database is not empty')
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix='.oko-recovery-', dir=target.parent) as temporary:
            temporary = Path(temporary)
            execute('restic', 'restore', identity, '--target', str(temporary / 'download'), env=environment)
            snapshot = temporary / 'download/snapshot'
            verify_snapshot(snapshot)
            # Input is a checked custom-format dump, never a live PostgreSQL directory.
            with (snapshot / 'database.dump').open('rb') as dump:
                execute('docker', 'exec', '-i', '-u', 'postgres', container, 'pg_restore',
                        '-U', 'postgres', '-d', 'oko', '--exit-on-error', '--single-transaction',
                        '--no-owner', '--no-acl', stdin=dump)
            # Set every restored library's power state before any reader can start.
            statement = """DO $body$ DECLARE s text; BEGIN
FOR s IN SELECT table_schema FROM information_schema.tables WHERE table_name='state'
AND table_schema NOT IN ('pg_catalog','information_schema') LOOP
EXECUTE format('INSERT INTO %I.state(key,value) VALUES (%L,%L) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value',s,'work-enabled','false');
IF to_regclass(format('%I.reader_identity',s)) IS NOT NULL THEN
    EXECUTE format('UPDATE %I.reader_identity SET value=gen_random_uuid()::text WHERE id=1',s);
ELSE
    EXECUTE format('INSERT INTO %I.state(key,value) VALUES (%L,%L) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value',s,'storage-id',to_json(gen_random_uuid()::text)::text);
END IF;
END LOOP; END $body$;"""
            execute('docker', 'exec', '-i', '-u', 'postgres', container, 'psql', '-U', 'postgres',
                    '-d', 'oko', '-v', 'ON_ERROR_STOP=1', input=statement.encode())
            atomic(snapshot / 'recovery.json', dict(snapshot_id=identity, recovered_at=time.time(),
                   work_enabled=False, postgres_container=container, reader_started=False))
            snapshot.rename(target)
    return dict(ok=True, snapshot_id=identity, work_enabled=False, reader_started=False)


def install(root, unit_directory):
    source = Path(__file__).resolve().parent
    root = Path(root).resolve()
    if root != ROOT:
        raise ValueError('Scheduled installation supports only /srv/oko')
    configuration(root)
    with maintenance(root):
        destination = root / 'config/backup.py'
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source / 'backup.py', destination)
        destination.chmod(0o700)
        for name in ('oko-backup.service', 'oko-backup.timer', 'oko-backup-recover.service'):
            shutil.copy2(source / 'systemd' / name, unit_directory / name)
            (unit_directory / name).chmod(0o644)
        execute('systemctl', 'daemon-reload')
        execute('systemctl', 'enable', 'oko-backup-recover.service')
        execute('systemctl', 'enable', '--now', 'oko-backup.timer')
    return dict(ok=True)

def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check')
    backup = commands.add_parser('backup')
    backup.add_argument('--kind', choices=('nightly', 'pre-migration'), default='nightly')
    restore = commands.add_parser('restore')
    restore.add_argument('--snapshot', required=True)
    restore.add_argument('--target', type=Path, required=True)
    restore.add_argument('--postgres-container', required=True)
    commands.add_parser('install')
    commands.add_parser('recover')
    args = parser.parse_args()
    if args.command == 'backup':
        value = create_backup(args.root, args.kind)
        print(json.dumps({'ok': True, 'snapshot_id': value['snapshot_id'], 'pause_seconds': value['pause_seconds']}))
    elif args.command == 'restore':
        print(json.dumps(restore_backup(args.root, args.snapshot, args.target, args.postgres_container)))
    elif args.command == 'recover':
        with maintenance(args.root):
            recovered = recover_locked(args.root)
            if recovered:
                state_path = args.root / 'backup/status.json'
                try:
                    state = json.loads(state_path.read_text())
                except (OSError, ValueError):
                    state = dict(last_success_timestamp_seconds=0, last_success_size_bytes=0,
                                 last_attempt_timestamp_seconds=time.time())
                state.update(last_attempt_result='error', error_class='InterruptedBackup')
                atomic(state_path, state, 0o644)
        print(json.dumps({'ok': True, 'recovered': recovered}))
    elif args.command == 'install':
        print(json.dumps(install(args.root, Path('/etc/systemd/system'))))
    else:
        with maintenance(args.root):
            execute('restic', 'check', '--read-data', env=configuration(args.root))
        print(json.dumps({'ok': True}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Oko backup failed: ' + (str(error) if isinstance(error, ValueError) and ('private regular file' in str(error) or 'empty separate target' in str(error)) else type(error).__name__), file=sys.stderr)
        raise SystemExit(1) from None
