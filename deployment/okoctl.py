#!/usr/bin/env python3
"""Root-owned, serialized maintenance for the one Oko cloud installation."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import subprocess
import tempfile
from urllib.parse import urlsplit

ROOT = Path('/srv/oko')
SOURCE = Path(__file__).resolve().parent


def run(*args, **options):
    return subprocess.run(list(map(str, args)), check=True, **options)


@contextmanager
def maintenance(root=ROOT):
    root.mkdir(parents=True, exist_ok=True, mode=0o755)
    with (root/'maintenance.lock').open('a') as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another maintenance operation is running') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def atomic(path, content, *, mode=0o600, uid=0, gid=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name+'-')
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(content.encode() if isinstance(content, str) else content)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fchown(output.fileno(), uid, gid)
            os.fsync(output.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


def manifest(path):
    value = json.loads(path.read_text())
    if (not re.fullmatch('[0-9a-f]{40}', value.get('sha', '')) or
        not re.fullmatch('ghcr.io/tgenman/oko@sha256:[0-9a-f]{64}', value.get('image', '')) or
        value.get('platform') != 'linux/amd64' or value.get('helper', {}).get('release') != value['sha']):
        raise ValueError('Invalid Linux release manifest')
    return value


def compose(project, *args, release=None, **options):
    env = dict(os.environ, DOCKER_CONFIG=str(ROOT/'secrets/docker'))
    if release is not None: env['OKO_IMAGE'] = release['image']
    return run('docker', 'compose', '-f', SOURCE/f'compose.{project}.yaml', *args, env=env, **options)


def installed():
    return manifest(ROOT/'release.json')


def initialize_files():
    for name, uid, gid, mode in [('data',10001,10001,0o700), ('postgres',999,999,0o700),
        ('loki',10001,10001,0o750), ('alloy',10001,10001,0o750), ('prometheus',65534,65534,0o750),
        ('grafana',472,472,0o750), ('tls',0,10001,0o750), ('secrets',0,0,0o700)]:
        directory = ROOT/name
        directory.mkdir(parents=True, exist_ok=True)
        os.chown(directory, uid, gid)
        directory.chmod(mode)
    credentials = ROOT/'secrets'
    names = ['postgres-admin','database-migrator','database-reader','postgres-init.sql']
    if any((ROOT/'postgres').rglob('PG_VERSION')) and any(not (credentials/name).is_file() for name in names):
        raise RuntimeError('Existing PostgreSQL needs its original credentials; refusing regeneration')
    if not (credentials/'postgres-admin').exists():
        atomic(credentials/'postgres-admin', secrets.token_hex(32)+'\n', uid=999, gid=999)
    for role in ('migrator','reader'):
        path = credentials/f'database-{role}'
        if not path.exists():
            atomic(path, f'postgresql://oko_{role}:{secrets.token_hex(32)}@postgres:5432/oko\n', uid=10001, gid=10001)
    if not (credentials/'postgres-init.sql').exists():
        passwords = {role:urlsplit((credentials/f'database-{role}').read_text().strip()).password for role in ('migrator','reader')}
        for password in passwords.values():
            if not password or not re.fullmatch('[0-9a-f]{64}',password): raise ValueError('Invalid generated database credential')
        sql = f"""CREATE ROLE oko_migrator LOGIN PASSWORD '{passwords['migrator']}';
CREATE ROLE oko_reader LOGIN PASSWORD '{passwords['reader']}';
REVOKE ALL ON DATABASE oko FROM PUBLIC;
GRANT CONNECT ON DATABASE oko TO oko_migrator, oko_reader;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA oko AUTHORIZATION oko_migrator;
GRANT USAGE ON SCHEMA oko TO oko_reader;
ALTER ROLE oko_migrator IN DATABASE oko SET search_path = oko, public;
ALTER ROLE oko_reader IN DATABASE oko SET search_path = oko, public;
ALTER DEFAULT PRIVILEGES FOR ROLE oko_migrator IN SCHEMA oko GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO oko_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE oko_migrator IN SCHEMA oko GRANT USAGE, SELECT ON SEQUENCES TO oko_reader;
"""
        atomic(credentials/'postgres-init.sql', sql, uid=999, gid=999)


def reader_running(release):
    result = compose('app','ps','--status','running','--quiet','reader',release=release,capture_output=True,text=True)
    return bool(result.stdout.strip())


def stop(release):
    compose('app','stop','--timeout','30','reader',release=release)
    run('systemctl','stop','oko-codex.service')
    if reader_running(release): raise RuntimeError('Reader did not stop')
    result = subprocess.run(['systemctl','is-active','--quiet','oko-codex.service'])
    if result.returncode == 0: raise RuntimeError('Helper did not stop')


def start(release):
    run('systemctl','start','oko-codex.service')
    compose('infra','up','-d','--wait','--wait-timeout','90')
    target=str((ROOT/'tls/current').readlink())
    receipt=ROOT/'tls/applied-version'
    reload_cert=not receipt.exists() or receipt.read_text().strip() != target
    compose('app','up','-d',*(['--force-recreate'] if reload_cert else []),'--wait','--wait-timeout','90',release=release)
    atomic(receipt,target+'\n')


def install_helper(release):
    source=SOURCE.parent
    ROOT.chmod(0o755)
    if source != ROOT/'releases'/release['sha']:
        raise RuntimeError('Release source must use its immutable installation path')
    revision=run('git','-C',source,'rev-parse','HEAD',capture_output=True,text=True).stdout.strip()
    if revision != release['sha']:
        raise RuntimeError('Host helper source differs from release manifest')
    run('python3',SOURCE/'install-codex-linux.py')
    run('uv','sync','--frozen','--no-dev','--python','/usr/bin/python3',cwd=source)
    run('runuser','-u','okocodex','--','test','-r',source/'oko/processing/codex_bridge/__main__.py')
    run('runuser','-u','okocodex','--',source/'.venv/bin/python','-c','import oko.processing.codex_bridge',cwd=source)
    private=Path('/var/lib/oko-codex')
    import pwd
    account=pwd.getpwnam('okocodex')
    private.mkdir(exist_ok=True,mode=0o700); os.chown(private,account.pw_uid,account.pw_gid)
    token=private/'bridge-token'
    if not token.exists(): atomic(token,secrets.token_urlsafe(48)+'\n',uid=account.pw_uid,gid=account.pw_gid)
    atomic(ROOT/'secrets/codex-reader-token',token.read_bytes(),uid=10001,gid=10001)
    ipc=ROOT/'ipc'
    ipc.mkdir(exist_ok=True,mode=0o750); os.chown(ipc,account.pw_uid,20001); ipc.chmod(0o750)
    config=dict(token_file=str(token),scratch_root='/run/oko-codex/jobs',socket_path=str(ipc/'helper.sock'),release=release['sha'])
    atomic(private/'helper.json',json.dumps(config)+'\n',uid=account.pw_uid,gid=account.pw_gid)
    current=ROOT/'codex/current'
    current.parent.mkdir(exist_ok=True)
    temporary=current.with_name('.current-new'); temporary.unlink(missing_ok=True)
    temporary.symlink_to(source); os.replace(temporary,current)
    atomic(Path('/etc/systemd/system/oko-codex.service'),(SOURCE/'systemd/oko-codex.service').read_bytes(),mode=0o644)
    run('systemctl','daemon-reload')
    run('systemctl','enable','oko-codex.service')
    run('systemctl','restart','oko-codex.service')


def install(release):
    if (ROOT/'release.json').exists():
        raise RuntimeError('Already installed; use the deployment operation for release changes')
    # Pull and inspect before creating a database or replacing any installed state.
    run('docker','--config',ROOT/'secrets/docker','pull',release['image'])
    result=run('docker','image','inspect',release['image'],capture_output=True,text=True)
    image=json.loads(result.stdout)[0]
    if image['Architecture'] != 'amd64' or image['Config'].get('Labels',{}).get('org.opencontainers.image.revision') != release['sha']:
        raise RuntimeError('Image architecture or source revision differs from manifest')
    initialize_files()
    install_helper(release)
    compose('infra','up','-d','--wait','--wait-timeout','90')
    migration = ['docker','run','--rm','--network','oko-backend','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--tmpfs','/tmp:mode=1777',
        '-v',str(ROOT/'secrets/database-migrator')+':/run/secrets/database:ro',
        '--entrypoint','python',release['image']]
    run(*migration,'-m','oko.storage.postgres_migrations','--database-url-file','/run/secrets/database')
    # Initial installation is quiescent and has no reader. Never reset an
    # existing library's power setting during a later deployment.
    sql = "REVOKE INSERT, UPDATE, DELETE ON oko.oko_schema_migrations FROM oko_reader; INSERT INTO oko.state(key,value) VALUES ('work-enabled','false') ON CONFLICT(key) DO NOTHING;"
    compose('infra','exec','-T','postgres','psql','-U','postgres','-d','oko','-v','ON_ERROR_STOP=1','-c',sql)
    atomic(ROOT/'release.json', json.dumps(release,indent=2)+'\n')
    start(release)


def certificate_version(cert, key, root=ROOT):
    cert, key = cert.resolve(strict=True), key.resolve(strict=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    run('openssl','x509','-in',cert,'-noout','-checkend','3600',stdout=subprocess.DEVNULL)
    run('openssl','x509','-in',cert,'-noout','-checkhost','oko.observer',stdout=subprocess.DEVNULL)
    content, private = cert.read_bytes(), key.read_bytes()
    revision = hashlib.sha256(content+private).hexdigest()
    versions=root/'tls/versions'
    versions.mkdir(parents=True,exist_ok=True,mode=0o750)
    os.chown(versions,0,10001)
    target=versions/revision
    if not target.exists():
        temporary=Path(tempfile.mkdtemp(dir=versions,prefix='.cert-'))
        try:
            temporary.chmod(0o750); os.chown(temporary,0,10001)
            atomic(temporary/'fullchain.pem',content,mode=0o640,gid=10001)
            atomic(temporary/'privkey.pem',private,mode=0o640,gid=10001)
            os.rename(temporary,target)
            descriptor=os.open(versions,os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        finally:
            if temporary.exists(): shutil.rmtree(temporary)
    return Path('versions')/revision


def point_certificate(target, root=ROOT):
    temporary=root/'tls/.current-new'
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary,root/'tls/current')
    descriptor=os.open(root/'tls',os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def apply_certificate(cert, key):
    target=certificate_version(cert,key)
    current=ROOT/'tls/current'
    previous=current.readlink() if current.is_symlink() else None
    receipt=ROOT/'tls/applied-version'
    if previous == target and receipt.exists() and receipt.read_text().strip() == str(target): return
    release=installed() if (ROOT/'release.json').exists() else None
    active=bool(release and reader_running(release))
    point_certificate(target)
    if active:
        try:
            compose('app','up','-d','--force-recreate','--wait','--wait-timeout','90','reader',release=release)
            atomic(receipt,str(target)+'\n')
        except subprocess.CalledProcessError:
            if previous is not None:
                point_certificate(previous)
                compose('app','up','-d','--force-recreate','--wait','--wait-timeout','90','reader',release=release)
            raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='operation',required=True)
    install_parser=sub.add_parser('install'); install_parser.add_argument('--manifest',type=Path,required=True)
    for name in ('start','stop','status'): sub.add_parser(name)
    cert_parser=sub.add_parser('cert-apply')
    cert_parser.add_argument('--cert',type=Path,required=True); cert_parser.add_argument('--key',type=Path,required=True)
    args=parser.parse_args()
    if os.geteuid() != 0: parser.error('Run through sudo')
    with maintenance():
        if args.operation == 'install': install(manifest(args.manifest))
        elif args.operation == 'cert-apply': apply_certificate(args.cert,args.key)
        elif args.operation == 'start': start(installed())
        elif args.operation == 'stop': stop(installed())
        else:
            compose('infra','ps')
            compose('app','ps',release=installed())
            run('systemctl','status','oko-codex.service','--no-pager')


if __name__ == '__main__':
    main()
