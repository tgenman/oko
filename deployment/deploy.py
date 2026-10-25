#!/usr/bin/env python3
"""Serialize immutable reader/helper releases without replacing infrastructure."""
import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

DEFAULT_ROOT = Path('/srv/oko')
REPOSITORY = 'https://github.com/tgenman/oko.git'
MAX_REQUEST = 65536


def validate(value):
    """Accept the public manifest contract, never a mutable image or shell input."""
    try:
        sha = value['sha']
        schema = value['schema']
        codex = value['codex']
        valid = (value['version'] == 1 and re.fullmatch('[0-9a-f]{40}', sha)
                 and re.fullmatch('ghcr.io/tgenman/oko-runtime@sha256:[0-9a-f]{64}', value['image'])
                 and value['platform'] == 'linux/amd64'
                 and value['helper'] == dict(release=sha, protocol=1)
                 and type(schema['minimum']) is int and type(schema['maximum']) is int
                 and 1 <= schema['minimum'] <= schema['maximum']
                 and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', codex['version'])
                 and codex['target'] == 'x86_64-unknown-linux-musl'
                 and all(re.fullmatch('[0-9a-f]{64}', codex[k]) for k in
                         ('archive_sha256', 'binary_sha256', 'code_mode_host_sha256')))
        if not valid:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError('Invalid release manifest') from None
    return value


def read_manifest(path):
    if path.stat().st_size > MAX_REQUEST:
        raise ValueError('Invalid release manifest')
    return validate(json.loads(path.read_text()))


def read_pending(path):
    if path.stat().st_size > MAX_REQUEST:
        raise ValueError('Invalid pending deployment')
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or set(value) != {'release', 'components'}:
        raise ValueError('Invalid pending deployment')
    validate(value['release'])
    components = value['components']
    if (not isinstance(components, list) or len(components) > 4
            or any(not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha) for sha in components)):
        raise ValueError('Invalid pending component identities')
    return value


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def command(*args, env=None, cwd=None, timeout=120):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, text=True,
                          env=env, cwd=cwd, timeout=timeout).stdout.strip()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(descriptor, 'w') as output:
            output.write(json.dumps(value, sort_keys=True) + '\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def maintenance(root):
    with (root / 'maintenance.lock').open('a') as lock:
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another maintenance operation is running') from None
        yield


def point(path, target):
    path.parent.mkdir(exist_ok=True, mode=0o755)
    temporary = path.with_name('.' + path.name + '-deploy')
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, path)
    sync_directory(path.parent)


def compose(root, release, *args):
    env = dict(os.environ, OKO_IMAGE=release['image'])
    return command('docker', 'compose', '-f', root / 'releases' / release['sha'] / 'deployment/compose.app.yaml',
                   *args, env=env)


def database_state(root, release):
    # A cutover may select a new schema in the private DSN. Both identities must
    # select that same database/schema; inspecting the old `oko` schema would
    # give an unrelated compatibility result. The DSNs never leave these files.
    probe = '''import json,psycopg
from pathlib import Path
from oko.storage.credentials import private_database_url
states=[]
for identity in ('reader','migrator'):
 with psycopg.connect(private_database_url(Path('/run/secrets/database-'+identity)),autocommit=True,connect_timeout=5) as db:
  states.append(db.execute('SELECT current_database(),current_schema(),(SELECT max(version) FROM oko_schema_migrations)').fetchone())
assert states[0]==states[1]
database,schema,version=states[0]
print(json.dumps(dict(database=database,schema=schema,version=version)))
'''
    raw = command('docker', 'run', '--rm', '--network', 'oko-backend', '--read-only', '--cap-drop', 'ALL',
                  '--security-opt', 'no-new-privileges', '--memory', '128m', '--pids-limit', '32',
                  '-v', str(root / 'secrets/database-reader') + ':/run/secrets/database-reader:ro',
                  '-v', str(root / 'secrets/database-migrator') + ':/run/secrets/database-migrator:ro',
                  '--entrypoint', 'python', release['image'], '-c', probe)
    value = json.loads(raw)
    if (type(value.get('version')) is not int or value['version'] < 1
            or not isinstance(value.get('schema'), str) or not value['schema']
            or not isinstance(value.get('database'), str) or not value['database']):
        raise ValueError('Database schema version could not be verified')
    return value


def immutable_containers():
    names = ('oko-infra-postgres-1', 'oko-monitoring-loki-1', 'oko-monitoring-alloy-1',
             'oko-monitoring-prometheus-1', 'oko-monitoring-grafana-1',
             'oko-monitoring-node-exporter-1', 'oko-monitoring-postgres-exporter-1')
    return {name: command('docker', 'inspect', '--format', '{{.Id}}', name) for name in names}


def helper_identity():
    # systemd sets Group=oko-ipc. An existing account may legitimately keep a
    # different primary group; validate the named service group instead.
    group = command('getent', 'group', 'oko-ipc').split(':')
    if command('id', '-u', 'okocodex') != '20002' or len(group) < 3 or group[0] != 'oko-ipc' or group[2] != '20001':
        raise ValueError('Unexpected helper account identity')


def prepare(root, release, token, *, cached=False, minimum=()):
    helper_identity()
    source = root / 'releases' / release['sha']
    source.parent.mkdir(exist_ok=True, mode=0o755)
    if not source.exists():
        previous = os.umask(0o022)
        try:
            command('git', 'clone', '--no-checkout', REPOSITORY, source, timeout=180)
            command('git', '-C', source, 'checkout', '--detach', release['sha'])
        finally:
            os.umask(previous)
    if source.is_symlink() or command('git', '-C', source, 'rev-parse', 'HEAD') != release['sha']:
        raise ValueError('Source does not match the immutable release')
    if command('git', '-C', source, 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('Release source contains tracked changes')
    if not cached:
        command('git', '-C', source, 'fetch', REPOSITORY, 'main', timeout=180)
        command('git', '-C', source, 'merge-base', '--is-ancestor', release['sha'], 'FETCH_HEAD')
    for sha in minimum:
        try:
            command('git', '-C', source, 'merge-base', '--is-ancestor', sha, release['sha'])
        except subprocess.CalledProcessError as error:
            if error.returncode == 1:
                raise ValueError('Older or divergent release is not allowed') from None
            raise
    if json.loads((source / 'oko/processing/codex_runtime.json').read_text()) != release['codex']:
        raise ValueError('Codex pin differs from the checked source')
    unit = (source / 'deployment/systemd/oko-codex.service').read_text()
    if 'Environment=OKO_CODEX_BINARY=/opt/oko/codex/' + release['codex']['version'] + '/bin/codex' not in unit:
        raise ValueError('Helper service and CLI pin differ')
    if not cached:
        with tempfile.TemporaryDirectory(prefix='oko-registry-') as temporary:
            env = dict(os.environ, DOCKER_CONFIG=temporary)
            if token is not None:
                if not isinstance(token, str) or not 20 <= len(token) <= 16000 or not token.isascii() or any(c.isspace() for c in token):
                    raise ValueError('Invalid package-scoped registry credential')
                atomic(Path(temporary) / 'config.json', {'auths': {'ghcr.io': {'registrytoken': token}}})
            command('docker', 'pull', release['image'], env=env, timeout=300)
    image = json.loads(command('docker', 'image', 'inspect', release['image']))[0]
    if image['Architecture'] != 'amd64' or image['Os'] != 'linux' or image['Config']['Labels'].get('org.opencontainers.image.revision') != release['sha']:
        raise ValueError('Image architecture or revision differs from manifest')
    if not cached:
        previous = os.umask(0o022)
        try:
            command('python3', source / 'deployment/install-codex-linux.py', timeout=180)
            command('uv', 'sync', '--frozen', '--no-dev', '--python', '/usr/bin/python3', cwd=source, timeout=300)
        finally:
            os.umask(previous)
    command('runuser', '-u', 'okocodex', '--', source / '.venv/bin/python', '-c',
            'import oko.processing.codex_bridge', cwd=source)
    return source


def stop(root, release):
    compose(root, release, 'stop', '--timeout', '30', 'reader')
    command('systemctl', 'stop', 'oko-codex.service')
    if compose(root, release, 'ps', '--status', 'running', '--quiet', 'reader'):
        raise ValueError('Reader did not stop')
    if subprocess.run(['systemctl', 'is-active', '--quiet', 'oko-codex.service'],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20).returncode == 0:
        raise ValueError('Helper did not stop')


def activate(root, release, helper_config, unit, history):
    helper_identity()
    config = dict(helper_config, release=release['sha'])
    staged = history / ('activate-' + release['sha'] + '.json')
    atomic(staged, config)
    command('install', '-o', 'okocodex', '-g', 'oko-ipc', '-m', '0600', staged, '/var/lib/oko-codex/helper.json')
    command('install', '-o', 'root', '-g', 'root', '-m', '0644', unit, '/etc/systemd/system/oko-codex.service')
    point(root / 'codex/current', root / 'releases' / release['sha'])
    command('systemctl', 'daemon-reload')
    command('systemctl', 'start', 'oko-codex.service')
    compose(root, release, 'up', '-d', '--force-recreate', '--wait', '--wait-timeout', '90', 'reader')


def health(root, release):
    # All probes execute within the reader's network/mount boundary. TLS and the
    # private service bearer are verified by the existing control interface.
    probe = '''import http.client,json,socket,urllib.request
from pathlib import Path
from oko.reader.control import status
value=status(json.loads(Path('/data/runtime.json').read_text()))
assert value['runtime_state'] in ('running','disabled')
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('https://oko.observer/',timeout=10) as response:
 assert response.status==200
class UnixHTTP(http.client.HTTPConnection):
 def connect(self):
  self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(5);self.sock.connect('/run/oko-ipc/helper.sock')
connection=UnixHTTP('localhost')
try:
 connection.request('GET','/v1/health',headers={'Authorization':'Bearer '+Path('/run/secrets/codex').read_text().strip(),'x-oko-protocol':'1'})
 response=connection.getresponse();helper=json.loads(response.read())
 assert response.status==200 and helper['release']==EXPECTED_RELEASE
finally:connection.close()
'''.replace('EXPECTED_RELEASE', repr(release['sha']))
    compose(root, release, 'exec', '-T', 'reader', 'python', '-c', probe)


def migration_backup(root):
    location = Path(__file__).with_name('backup.py')
    spec = importlib.util.spec_from_file_location('oko_deployment_backup', location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create_backup(root, kind='pre-migration', lock_held=True)


def migrate(root, release):
    command('docker', 'run', '--rm', '--network', 'oko-backend', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:mode=1777',
            '-v', str(root / 'secrets/database-migrator') + ':/run/secrets/database:ro',
            '--entrypoint', 'python', release['image'], '-m', 'oko.storage.postgres_migrations',
            '--database-url-file', '/run/secrets/database', timeout=180)


def change(root, release, *, token=None):
    original = read_manifest(root / 'release.json')
    pending_path = root / 'deployment-pending.json'
    pending = read_pending(pending_path) if pending_path.exists() else None
    validate(release)
    minimum = (original['sha'],) + ((pending['release']['sha'],) if pending else ())
    prepare(root, release, token, cached=token is None and (release == original or pending is not None and pending['release'] == release), minimum=minimum)
    with maintenance(root):
        current = read_manifest(root / 'release.json')
        if current != original or (read_pending(pending_path) if pending_path.exists() else None) != pending:
            raise ValueError('Installed release changed during preparation')
        database = database_state(root, current)
        version = database['version']
        if version > release['schema']['maximum']:
            raise ValueError('Deploy blocked by incompatible database schema; a newer release is required')
        allowed = {current['sha']} | ({pending['release']['sha'], *pending['components']} if pending else set())
        if (root / 'operator').resolve() not in {root / 'releases' / sha / 'deployment' for sha in allowed}:
            raise ValueError('Installed operator source is inconsistent')
        helper = json.loads(command('cat', '/var/lib/oko-codex/helper.json'))
        if helper['release'] not in allowed or (root / 'codex/current').resolve() not in {root / 'releases' / sha for sha in allowed}:
            raise ValueError('Installed helper source is inconsistent')
        if release == current and pending is None:
            try:
                health(root, release)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass  # Repair this same confirmed release; never select an older one.
            else:
                return dict(success=True, already_installed=True, sha=release['sha'], image=release['image'],
                            schema_version=version, database_schema=database['schema'])
        components = sorted({current['sha'], helper['release'],
                             (root / 'codex/current').resolve().name,
                             (root / 'operator').resolve().parent.name})
        identifiers = immutable_containers()
        history = root / 'deployment-history' / (str(time.time_ns()) + '-' + release['sha'])
        history.mkdir(parents=True, mode=0o700)
        atomic(history / 'previous-release.json', current)
        atomic(history / 'previous-helper.json', helper)
        old_unit = history / 'previous-helper.service'
        old_unit.write_text(command('cat', '/etc/systemd/system/oko-codex.service') + '\n')
        started = time.monotonic()
        receipt = dict(success=False, sha=release['sha'], image=release['image'], previous_sha=current['sha'],
                       schema_version=version, database_schema=database['schema'], migrated=False,
                       forward_repair_required=False, timestamp=time.time())
        atomic(pending_path, dict(release=release, components=components))
        try:
            stop(root, current)
            if version < release['schema']['minimum']:
                receipt['backup'] = migration_backup(root)
                migrate(root, release)
                receipt['migrated'] = True
                migrated = database_state(root, release)
                if (migrated['database'], migrated['schema']) != (database['database'], database['schema']):
                    raise ValueError('Active database schema changed during deployment')
                version = migrated['version']
            if not release['schema']['minimum'] <= version <= release['schema']['maximum']:
                raise ValueError('Target schema does not match release manifest')
            activate(root, release, helper, root / 'releases' / release['sha'] / 'deployment/systemd/oko-codex.service', history)
            health(root, release)
            if immutable_containers() != identifiers:
                raise ValueError('Infrastructure changed during the application deployment')
            point(root / 'operator', root / 'releases' / release['sha'] / 'deployment')
            atomic(root / 'release.json', release)
            receipt.update(success=True, schema_version=version, maintenance_seconds=round(time.monotonic()-started, 3))
            atomic(history / 'result.json', receipt)
            pending_path.unlink()
            sync_directory(pending_path.parent)
            return receipt
        except BaseException as error:
            receipt['error_class'] = type(error).__name__
            # A failed candidate stays stopped. The durable pending manifest
            # admits a retry of that same candidate or a descendant, including
            # after partial helper activation or a forward schema migration.
            receipt['forward_repair_required'] = True
            try:
                stop(root, release)
            except BaseException as stopping:
                receipt['stop_error_class'] = type(stopping).__name__
            try:
                atomic(history / 'result.json', receipt)
            except OSError:
                print('Deployment result could not be persisted', file=sys.stderr)
            raise


def authorize(root, public_key):
    key = public_key.read_text().strip()
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]{40,100}={0,2}(?: [A-Za-z0-9_.@-]+)?', key):
        raise ValueError('Expected a dedicated Ed25519 public key')
    if root != DEFAULT_ROOT or os.geteuid() != 0:
        raise ValueError('SSH authorization requires the root-owned /srv/oko installation')
    source = Path(__file__).resolve()
    if (source.parent.parent.parent != root / 'releases'
            or not re.fullmatch('[0-9a-f]{40}', source.parent.parent.name)):
        raise ValueError('Authorize only from an immutable reviewed release')
    import grp
    import pwd
    with maintenance(root):
        try:
            if pwd.getpwuid(20004).pw_name != 'oko-deploy':
                raise ValueError('Deployment UID is occupied')
        except KeyError:
            pass
        try:
            group = grp.getgrnam('oko-deploy')
            if group.gr_gid != 20004:
                raise ValueError('Unexpected deployment GID')
        except KeyError:
            try:
                grp.getgrgid(20004)
            except KeyError:
                command('groupadd', '--system', '--gid', '20004', 'oko-deploy')
            else:
                raise ValueError('Deployment GID is occupied') from None
        try:
            account = pwd.getpwnam('oko-deploy')
        except KeyError:
            command('useradd', '--system', '--uid', '20004', '--gid', 'oko-deploy',
                    '--home-dir', '/var/lib/oko-deploy', '--shell', '/bin/sh', 'oko-deploy')
            account = pwd.getpwnam('oko-deploy')
        if (account.pw_uid != 20004 or account.pw_gid != 20004
                or account.pw_dir != '/var/lib/oko-deploy' or account.pw_shell != '/bin/sh'):
            raise ValueError('Unexpected deployment account')
        home = Path(account.pw_dir)
        for path in (home, home / '.ssh'):
            path.mkdir(exist_ok=True, mode=0o755)
            os.chown(path, 0, 0)
            path.chmod(0o755)
        with tempfile.TemporaryDirectory(prefix='authorize-', dir=root) as temporary:
            stage = Path(temporary)
            files = [
                ('authorized_keys', home / '.ssh/authorized_keys',
                 'restrict,command="sudo -n /usr/local/sbin/oko-deploy-receive" ' + key + '\n', '0644'),
                ('wrapper', Path('/usr/local/sbin/oko-deploy-receive'),
                 '#!/bin/sh\nexec /usr/bin/python3 ' + str(source) + ' receive\n', '0755'),
                ('sudoers', Path('/etc/sudoers.d/oko-deploy'),
                 'oko-deploy ALL=(root) NOPASSWD: /usr/local/sbin/oko-deploy-receive ""\n', '0440')]
            for name, destination, content, mode in files:
                path = stage / name
                path.write_text(content)
                if name == 'sudoers':
                    command('visudo', '-cf', path)
                command('install', '-o', 'root', '-g', 'root', '-m', mode, path, destination)
    return dict(authorized=True, principal='oko-deploy', forced_command='oko-deploy-receive')


def receive(root):
    data = sys.stdin.buffer.read(MAX_REQUEST + 1)
    if len(data) > MAX_REQUEST:
        raise ValueError('Deployment request exceeded limit')
    request = json.loads(data)
    if not isinstance(request, dict) or request.get('action') != 'deploy':
        raise ValueError('Unsupported operation')
    if set(request) - {'action', 'manifest', 'registry_token'}:
        raise ValueError('Unknown deployment input')
    manifest = validate(request['manifest'])
    return change(root, manifest, token=request.get('registry_token'))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest='operation', required=True)
    target = sub.add_parser('deploy')
    target.add_argument('--manifest', type=Path, required=True)
    target.add_argument('--registry-token-file', type=Path)
    sub.add_parser('receive')
    authorization = sub.add_parser('authorize')
    authorization.add_argument('--public-key-file', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.root == DEFAULT_ROOT and os.geteuid() != 0:
            raise ValueError('Run through the root-owned deployment interface')
        if args.operation == 'authorize':
            result = authorize(args.root, args.public_key_file)
        elif args.operation == 'receive':
            result = receive(args.root)
        else:
            manifest = read_manifest(args.manifest)
            token = None
            if args.registry_token_file:
                mode = args.registry_token_file.stat().st_mode
                if not stat.S_ISREG(mode) or mode & 0o077 or args.registry_token_file.is_symlink():
                    raise ValueError('Registry credential must be a private regular file')
                token = args.registry_token_file.read_text().strip()
            result = change(args.root, manifest, token=token)
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyError) as error:
        # Tool errors can contain credentials or private reader diagnostics.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print('Oko deployment failed: ' + message, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
